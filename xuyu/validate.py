"""N4 规则校验（纯代码）。每条话术得到一组 Check。"""
import re
from itertools import combinations

from . import config
from .schemas import Candidate, Check

EMOJI = re.compile("[\U0001F300-\U0001FAFF☀-➿\U0001F000-\U0001F2FF]")
NUM = re.compile(r"\d[\d,]*(?:\.\d+)?")
PRICE = re.compile(r"(?:[¥￥]\s*(\d[\d,]*))|(?:(\d[\d,]*)\s*(?:元|块))")
PRODUCT = re.compile(r"「([^」]+)」")
SINGLE_ADDR = re.compile(r"(?:^|[\s，,。！!？?、])(亲|姐|哥)(?=[\s，,。！!？?~～]|$)")


def _norm(n: str) -> str:
    n = n.replace(",", "")
    try:
        return str(int(float(n))) if "." not in n else n
    except ValueError:
        return n


def length(text: str) -> int:
    return len(re.sub(r"\s", "", text))


def bigrams(text: str, drop: str = "") -> set:
    t = re.sub(r"[\s，。！？、,.!?：:；;「」（）()…—\-]", "", text.replace(drop, ""))
    return {t[i:i + 2] for i in range(len(t) - 1)}


def jaccard(a: str, b: str, drop: str = "") -> float:
    x, y = bigrams(a, drop), bigrams(b, drop)
    return len(x & y) / len(x | y) if x and y else 0.0


def check_one(cand: Candidate, *, brand, mode, scenario, pack_refs, corpus, catalog, address, dnm_keywords, missing_event):
    t = cand.text
    checks = []
    add = lambda rule, ok, detail="": checks.append(Check(rule=rule, ok=ok, detail=detail))

    # R1 禁用表达与称呼
    hits = [w for w in brand["banned_phrases"] if w in t]
    hits += [w for w in brand["banned_address"] if len(w) > 1 and w in t and w not in address]
    hits += [m for m in SINGLE_ADDR.findall(t) if m not in address]
    add("R1 禁用表达", not hits, "、".join(hits))

    # R2 价格政策（先去掉「没有额外折扣」这类否定说法）
    t2 = t
    for neg in brand["discount_negations"]:
        t2 = t2.replace(neg, "")
    hits = [w for w in brand["discount_words"] if w in t2]
    hits += re.findall(r"[一二三四五六七八九\d.]+折", t2)
    if scenario == "price_objection" and re.search(r"(价|会员|折扣|优惠|政策)[^。；，,]{0,8}核实|核实[^。；，,]{0,8}(价|会员|折扣|优惠|政策)", t):
        hits.append("对价格说「核实」，暗示有议价空间")   # v3
    add("R2 价格政策", not hits, "、".join(hits))

    # R3 价格与数字
    allowed = {_norm(n) for n in NUM.findall(corpus)}
    nums = [_norm(n) for n in NUM.findall(t)]
    bad_nums = [n for n in nums if n not in allowed]
    mentioned = [p for p in catalog if p["short"] in PRODUCT.findall(t) or p["name"] in t]
    prices = [_norm(a or b) for a, b in PRICE.findall(t)]
    bad_price = []
    if len(mentioned) == 1:
        bad_price = [p for p in prices if p != str(mentioned[0]["price"])]
    add("R3 价格与数字", not bad_nums and not bad_price,
        ("输入中没有的数字：" + "、".join(bad_nums) if bad_nums else "") + (f" 价格与目录不符：{bad_price}" if bad_price else ""))

    # R4 产品真实性
    shorts = {p["short"]: p for p in catalog}
    names = PRODUCT.findall(t)
    unknown = [n for n in names if n not in shorts]
    not_in_pack = [n for n in names if n in shorts and f"P:{shorts[n]['sku']}" not in pack_refs]
    add("R4 产品真实性", not unknown and not not_in_pack,
        ("目录里没有：" + "、".join(unknown) if unknown else "") + (" 不在本条资料包：" + "、".join(not_in_pack) if not_in_pack else ""))

    # R5 字数 + 表情/感叹号
    lo, hi = brand["length"]["event_invitation" if scenario == "event" and mode == scenario else "default"]
    n = length(t)
    emo = EMOJI.findall(t)
    excl = t.count("！") + t.count("!")
    add("R5 字数与格式", lo <= n <= hi and not emo and excl <= 1, f"{n}字（{lo}–{hi}）" + (" 含表情" if emo else "") + (f" 感叹号{excl}个" if excl > 1 else ""))

    # R6 问句数
    q = t.count("？") + t.count("?")
    add("R6 问句数", q <= brand["max_questions"], f"{q}个问句")

    # R7 越权承诺
    hits = [w for w in brand["promise_words"] if w in t]
    add("R7 越权承诺", not hits, "、".join(hits))

    # R10 禁区
    hits = [k for k in dnm_keywords if k and k in t2]   # t2 已去掉「没有额外折扣」这类品牌允许的否定说法
    add("R10 禁区", not hits, "、".join(hits))

    # R11 停止营销 / 离世问候 / TBD
    problems = []
    if mode in ("_no_marketing", "_bereavement_followup"):
        if names or prices:
            problems.append("出现了产品或价格")
        if "活动" in t:
            problems.append("出现了活动")
    if "TBD" in t and not (mode == "event" and missing_event):
        problems.append("TBD 只允许用在信息缺失的活动邀约")
    add("R11 场景专属", not problems, "；".join(problems))

    # R13 正文泄漏资料编号（v2）
    leak = re.findall(r"\[[^\]]*\]|\b(?:P|EVT|POL|SVC|CARE|DER|BB|H|C):[A-Z0-9.-]+", t)
    add("R13 编号泄漏", not leak, "、".join(leak))

    # R12 引用
    bad_refs = [r for r in cand.refs if r not in pack_refs]
    no_refs = (names or prices) and not any(r.startswith(("P:", "DER:")) for r in cand.refs)
    detail = ("不在资料包：" + "、".join(bad_refs) if bad_refs else "") + (" 提到产品或价格但没有注明引用编号" if no_refs else "")
    add("R12 引用", not bad_refs and not no_refs, detail.strip())
    return checks


def check_set(cands, address=""):
    """R8 差异度、R9 齐备性。返回 (set_checks, 需要重写的策略ID集合)。"""
    out, redo = [], set()
    ids = [c.strategy_id for c in cands]
    out.append(Check(rule="R9 齐备性", ok=sorted(ids) == ["A", "B", "C"], detail="、".join(ids)))
    for a, b in combinations(cands, 2):
        s = jaccard(a.text, b.text, address)
        ok = s <= config.SIM_THRESHOLD
        out.append(Check(rule=f"R8 差异度 {a.strategy_id}-{b.strategy_id}", ok=ok, detail=f"{s:.2f}"))
        if not ok:
            redo.add(b.strategy_id)
    return out, redo
