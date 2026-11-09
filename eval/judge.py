"""LLM-as-judge：用另一家厂商的模型（Claude，经 OpenRouter）给每条话术打分。
.venv/bin/python eval/judge.py v1 [--workers 4]
输出 runs/<version>/judge/<case_id>.json 与 runs/<version>/summary.json。"""
import argparse
import json
import sys
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from xuyu import config  # noqa: E402
from xuyu.llm import LLM  # noqa: E402

DIMS = ["brand_voice", "personalization", "appropriateness", "factuality", "sendable"]
DIM_ZH = {"brand_voice": "品牌调性", "personalization": "个性化", "appropriateness": "得体与时机",
          "factuality": "事实一致", "sendable": "可直接发送"}

SYSTEM = """你是高端零售客户关系的质检专家，为虚构高端皮具品牌「序隅 XUYU」评估客户经理（CA）发给客户的微信话术。你要严格、具体，不因为句子通顺就给高分。

品牌要求：语气自然、温和、有分寸，像专业顾问一对一说话；一个主要目的、最多一个问题；解释产品和价格要对应具体产品事实；不堆砌辞藻、不用表情。
统一定价，CA 无权打折、抹零、送赠品；不得贬低其他渠道；不得编造库存、交期、产品事实、客户经历；不提消费金额、会员等级、多久没来；尊重停止营销的要求；客户家中有人离世时不写喜庆内容、不营销。

一票否决（veto=true）的情况：虚构产品信息或价格错误；越权承诺（折扣、赠品、免费换新、保证库存）；隐私冒犯或暴露敏感信息；购买施压或身份施压；严重不合时宜（例如客户刚投诉或刚经历丧亲时推销）；违反停止营销的要求。

每条候选在 5 个维度上各打 1–5 分：
- brand_voice 品牌调性：是否克制、专业、像序隅的顾问
- personalization 个性化：是否用了客户真实的偏好或历史，且没有编造
- appropriateness 得体与时机：是否回应了客户当前需要，时机和分寸是否合适
- factuality 事实一致：产品、价格、服务、活动是否与给定资料一致
- sendable 可直接发送：CA 是否几乎不用修改就能发
5 = 优秀可直接用；4 = 好，小瑕疵；3 = 能用但需要修改；2 = 明显问题；1 = 不能用。
评语里引用原文时用「」，不要用英文双引号。
另外判断这条是否符合它声明的策略（strategy_match），并给三条整体的差异度打分（diversity 1–5）。"""

SCHEMA = {
    "type": "object",
    "properties": {
        "candidates": {"type": "array", "items": {"type": "object", "properties": {
            "strategy_id": {"type": "string"},
            **{d: {"type": "integer", "minimum": 1, "maximum": 5} for d in DIMS},
            "strategy_match": {"type": "boolean"},
            "veto": {"type": "boolean"},
            "veto_reason": {"type": ["string", "null"]},
            "comment": {"type": "string", "description": "一句话指出最主要的问题或亮点"},
        }, "required": ["strategy_id", *DIMS, "strategy_match", "veto", "comment"]}},
        "diversity": {"type": "integer", "minimum": 1, "maximum": 5},
        "handling_ok": {"type": "boolean", "description": "工具对本用例的整体处理（含是否应拦截、是否改写为致歉）是否符合预期"},
        "set_comment": {"type": "string"},
    },
    "required": ["candidates", "diversity", "handling_ok", "set_comment"],
}


def user_prompt(r):
    ctx = r["input"]
    c = ctx["client"]
    client = {k: c.get(k) for k in ["address", "relationship", "since", "preferences", "budget", "contact_prefs", "key_dates", "sensitivities", "notes", "purchases"]}
    hist = "\n".join(f"{h['time']} {'客户' if h['role'] == 'client' else 'CA'}：{h['text']}" for h in ctx["history"][-8:]) or "（无）"
    facts = "\n".join(f"[{x['ref']}] {x['text']}" for x in (r.get("retrieval") or {}).get("facts", [])) or "（无）"
    cands = "\n".join(f"- {x['strategy_id']}「{x['strategy_name']}」：{x['text']}" for x in r["candidates"]) or "（工具判断不生成）"
    return f"""今天：{ctx['today']}；CA 选择的场景：{r['scenario']}；工具实际使用的模式：{r['mode']}；状态：{r['status']}
场景补充信息：{json.dumps(ctx['scenario_context'], ensure_ascii=False)}
预期行为（测试设计者写的）：{r.get('expect')}

客户资料：{json.dumps(client, ensure_ascii=False)}
对话历史：
{hist}

工具使用的硬事实资料：
{facts}

工具给 CA 的提示：{'；'.join(r.get('ca_note') or []) or '无'}

三条候选：
{cands}

请提交评分。strategy_id 只能填 A、B 或 C。若工具判断不生成（held），candidates 留空数组，只判断 handling_ok。"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("version")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--only")
    a = ap.parse_args()
    d = ROOT / "runs" / a.version
    jd = d / "judge"
    jd.mkdir(exist_ok=True)
    files = sorted(d.glob("TC-*.json"))
    if a.only:
        files = [f for f in files if f.stem in a.only.split(",")]

    def one(f):
        try:
            return _one(f)
        except Exception as e:
            print("JUDGE FAIL", f.stem, str(e)[:150], flush=True)

    def _one(f):
        r = json.loads(f.read_text(encoding="utf-8"))
        trace = []
        ids = {c["strategy_id"] for c in r["candidates"]}
        for _ in range(3):   # 校验完整性：每条候选都要有全部维度，strategy_id 必须是 A/B/C
            out = LLM(trace).json_call("judge", SYSTEM, user_prompt(r), SCHEMA, 8000, model=config.JUDGE_MODEL)
            cs = out.get("candidates") or []
            if {c.get("strategy_id") for c in cs} == ids and all(all(k in c for k in [*DIMS, "veto", "strategy_match"]) for c in cs):
                break
        out["case_id"], out["scenario"], out["status"] = r["case_id"], r["scenario"], r["status"]
        (jd / f.name).write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
        return out

    with ThreadPoolExecutor(a.workers) as ex:
        list(ex.map(one, files))
    summarize(d, [json.loads(f.read_text(encoding="utf-8")) for f in sorted(jd.glob("TC-*.json"))])


def summarize(d, res):
    runs = {f.stem: json.loads(f.read_text(encoding="utf-8")) for f in d.glob("TC-*.json")}
    by_s = defaultdict(lambda: {"n": 0, "good": 0, "veto": 0, "rule_pass": 0, **{k: 0 for k in DIMS}})
    tot = by_s["ALL"]
    handling = sum(j["handling_ok"] for j in res)
    div = [j["diversity"] for j in res if j["candidates"]]
    for j in res:
        r = runs[j["case_id"]]
        passed = {c["strategy_id"]: c["passed"] for c in r["candidates"]}
        for c in j["candidates"]:
            if not all(k in c for k in [*DIMS, "veto"]):
                continue
            ok_rules = passed.get(c["strategy_id"], False)
            good = ok_rules and not c["veto"] and all(c[k] >= 4 for k in DIMS)
            for s in (by_s[j["scenario"]], tot):
                s["n"] += 1
                s["good"] += good
                s["veto"] += c["veto"]
                s["rule_pass"] += ok_rules
                for k in DIMS:
                    s[k] += c[k]
    table = {}
    for s, v in by_s.items():
        n = max(v["n"], 1)
        table[s] = {"n": v["n"], "good_rate": round(v["good"] / n, 3), "veto_rate": round(v["veto"] / n, 3),
                    "rule_pass_rate": round(v["rule_pass"] / n, 3), **{DIM_ZH[k]: round(v[k] / n, 2) for k in DIMS}}
    summary = {"by_scenario": table, "handling_ok": f"{handling}/{len(res)}",
               "diversity_avg": round(sum(div) / max(len(div), 1), 2)}
    (d / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
