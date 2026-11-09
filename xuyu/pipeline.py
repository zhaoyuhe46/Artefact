"""run(case) -> result
流程：load（crm）→ guards（纯代码）→ retrieve（硬事实 SQL + 软知识 pgvector）→ generate（1 次调用出三条）
     → validate（规则 R1–R12）→ repair（不通过的合并重写 1 次）→ validate。"""
import hashlib
import json
import time
from datetime import date

from . import config, prompts, skills
from .kb import KB
from .llm import LLM
from .schemas import SCENARIOS, Candidate
from .validate import check_one, check_set

ROOT = config.ROOT
_KB = None


def kb() -> KB:
    global _KB
    if _KB is None:
        _KB = KB()
    return _KB


def version_hash():
    h = hashlib.sha1()
    for d in ["data/brand", "skills"]:
        for f in sorted((ROOT / d).rglob("*")):
            if f.is_file():
                h.update(f.read_bytes())
    for f in ["prompts.py", "validate.py", "pipeline.py", "kb.py"]:
        h.update((ROOT / "xuyu" / f).read_bytes())
    return h.hexdigest()[:8]


# ---------------- guards：结构化数据优先，规则写死在代码里 ----------------
def guards(ctx):
    """返回 (mode, constraints, notes, held, flags)。mode 是要用的 SKILL 目录名。"""
    c, sc = ctx["client"], ctx["scenario"]
    mode, cons, notes = sc, [], []
    today = date.fromisoformat(ctx["today"])
    hist = ctx["history"]
    # v2：只有近 WAITING_DAYS 天内的客户消息才算「在等回复」；更早的消息不再强制先回应
    last_days = (today - date.fromisoformat(str(hist[-1]["time"])[:10])).days if hist else None
    waiting = bool(hist) and hist[-1]["role"] == "client" and last_days <= config.WAITING_DAYS
    if hist and hist[-1]["role"] == "client" and not waiting:
        cons.append(f"客户最后一条消息是 {last_days} 天前的，已经过去了，不要把它当作刚收到的消息来回应")
    no_mkt = bool((c.get("contact_prefs") or {}).get("no_marketing"))
    flags = {"client_waiting": waiting, "no_marketing": no_mkt}

    for s in c.get("sensitivities", []):
        days = (today - date.fromisoformat(str(s["date"])[:10])).days if s.get("date") else 0
        if s["type"] == "bereavement":
            flags["bereavement_days"] = days
            if waiting:
                cons.append("客户近期家中有人离世：只回应客户问的事，语气克制，不推荐、不邀约、不问预算、不写喜庆祝福")
                notes.append("客户近期家中有人离世，本次只回应客户主动提出的问题。")
            elif days <= config.BEREAVEMENT_QUIET_DAYS:
                notes.append(f"客户告知家中有人离世（{days} 天前）。建议暂不联系，给客户留出空间；"
                             f"{config.BEREAVEMENT_QUIET_DAYS} 天后可以用「沉睡客户唤醒」场景发一次不带请求的问候。")
                return mode, cons, notes, True, flags
            elif sc == "reactivation":
                mode = "_bereavement_followup"
                notes.append(f"客户家中有人离世已 {days} 天，本次只发不带请求的问候。")
            else:
                notes.append("客户家中有人离世后，只建议用「沉睡客户唤醒」场景发一次不带请求的问候，本场景暂不生成。")
                return mode, cons, notes, True, flags
        elif s["type"] == "recent_complaint" and days <= config.COMPLAINT_DAYS:
            flags["recent_complaint"] = True
            if sc in config.COMPLAINT_OVERRIDE_SCENARIOS and mode == sc:
                # v2：投诉未结时，营销类和购后场景不按原场景写，改为致歉与跟进
                mode = "_complaint_followup"
                notes.append(f"客户近期有不满（{s.get('note', '')}），本次不生成「{SCENARIOS[sc]}」内容，改为致歉与跟进；问题解决后再按原场景联系。")
            else:
                cons.append(f"客户近期有不满（{s.get('note', '')}）：先为让客户久等致歉并说明会跟进，不推荐产品、不邀约、不提服务权益")
                notes.append("客户近期有不满记录，三条都先致歉，不含推荐。")

    if no_mkt and sc in config.MARKETING_SCENARIOS and mode == sc:
        mode = "_no_marketing"
        notes.append(f"客户要求过停止营销联系，不生成「{SCENARIOS[sc]}」内容，改为三条简短致歉；也可以选择不发送。")
    elif no_mkt:
        cons.append("客户要求过停止营销联系：只回应客户问的事，不得附带任何推荐、活动，也不主动问预算或提出帮忙找其他款")

    if waiting and mode != "_no_marketing":
        cons.append(f"客户最后一条消息在等回复（「{hist[-1]['text']}」）：三条都必须先用一句话回应它；答不了的如实说先核实再回复。"
                    f"回应之后，本场景的内容最多再写一两句，整条控制在 {config.TARGET_LEN[1]} 字以内")

    if sc == "event" and mode == "event":
        miss = kb().missing_event_fields(kb().event_target(ctx))
        flags["missing_event_fields"] = miss
        if miss:
            cons.append(f"活动信息缺失：{'、'.join(miss)}。缺失项在正文中原位写成 TBD，不得编造")
            notes.append(f"活动信息缺失（{'、'.join(miss)}），话术中以 TBD 标出，请补全后再发送。")
    return mode, cons, notes, False, flags


def rag_query(ctx, skill):
    c = ctx["client"]
    last = next((h["text"] for h in reversed(ctx["history"]) if h["role"] == "client"), "")
    prefs = c.get("preferences") or {}
    return "；".join(x for x in [skill["name"], last, "、".join(prefs.get("uses") or []), prefs.get("style") or "", c.get("notes") or ""] if x)


# ---------------- 主流程 ----------------
def run(case: dict, save=True) -> dict:
    t0 = time.time()
    trace = []
    llm = LLM(trace)
    K = kb()
    K.refresh()   # 设置页可能刚改过品牌数据
    rules = K.brand_rules()
    ctx = K.load_case(case)
    mode, cons, notes, held, flags = guards(ctx)
    result = {"case_id": ctx["case_id"], "scenario": ctx["scenario"], "mode": mode,
              "status": "held" if held else "generated", "flags": flags, "candidates": [], "set_checks": [],
              "ca_note": notes, "retrieval": {}, "analysis": None,
              "meta": {"version": config.VERSION_LABEL, "hash": version_hash(), "model": config.GEN_MODEL}}
    if held:
        return _finish(result, ctx, trace, t0, save)

    skill = skills.load(mode)
    names = skill["strategies"]

    # 检索：硬事实（SQL）+ 软知识（pgvector）
    focus = K.focus_sku(ctx) if mode in SCENARIOS else None
    facts, seen = [], set()
    fact_packs = list(skill["facts"])
    if flags.get("client_waiting") and focus and "focus_product" not in fact_packs:
        fact_packs.insert(0, "focus_product")   # v2：客户在等回复（常是问价），带上相关产品，回答时有据可查
    for name in fact_packs:
        for r in K.pack(name, ctx, focus):
            if r["ref"] not in seen:
                seen.add(r["ref"]); facts.append(r)
    q = rag_query(ctx, skill)
    rag = K.rag(q, skill["rag"]["tags"], skill["rag"].get("k", config.RAG_K))
    result["retrieval"] = {"focus_sku": focus, "facts": facts, "rag_query": q,
                           "rag": [{"ref": r["ref"], "score": r["score"], "text": r["text"]} for r in rag]}

    # 生成：一次调用出三条
    system = prompts.system_prompt(rules, skill, ctx["advisor_name"])
    user = prompts.user_prompt(ctx, facts, rag, cons, config.HISTORY_N)
    out = llm.tool_call("generate", system, user, "submit_candidates", prompts.GEN_SCHEMA,
                        config.TEMPERATURE["generate"], config.MAX_TOKENS["generate"])
    an = out.get("analysis") or {}
    result["analysis"] = an
    if out.get("ca_note"):
        notes.append(out["ca_note"])

    def to_cands(items):
        got = {}
        for x in items or []:
            sid = x.get("strategy_id")
            if sid in names and sid not in got:
                got[sid] = Candidate(strategy_id=sid, strategy_name=names[sid], text=(x.get("text") or "").strip(),
                                     rationale=x.get("rationale", ""), refs=x.get("refs") or [])
        return got

    def norm_refs(c):
        # 模型有时省略前缀（XY-B03 → P:XY-B03），按资料包里的编号补齐
        fixed = []
        for r in c.refs:
            hit = r if r in allowed else next((a for a in allowed if a.endswith(":" + r)), r)
            fixed.append(hit)
        c.refs = fixed

    # 校验
    n_hist = min(len(ctx["history"]), config.HISTORY_N)
    allowed = {r["ref"] for r in facts} | {r["ref"] for r in rag} | {ctx["client"]["ref"]} | {f"H:{i + 1}" for i in range(n_hist)}
    corpus = json.dumps(ctx, ensure_ascii=False) + " ".join(r["text"] for r in facts + rag)
    address = an.get("address_form") or ctx["client"].get("address", "")
    dnm = [k for k in an.get("do_not_mention") or [] if 2 <= len(k) <= 6]
    catalog = K.products_all()
    cands = to_cands(out.get("candidates"))

    def validate(c):
        norm_refs(c)
        c.checks = check_one(c, brand=rules, mode=mode, scenario=ctx["scenario"], pack_refs=allowed, corpus=corpus,
                             catalog=catalog, address=address, dnm_keywords=dnm,
                             missing_event=bool(flags.get("missing_event_fields")))

    for c in cands.values():
        validate(c)
    ordered = [cands[s] for s in names if s in cands]
    set_checks, redo_sim = check_set(ordered, address)
    redo = {c.strategy_id for c in ordered if not c.passed} | redo_sim | (set(names) - set(cands))

    # 重写：不通过的合并成一次调用
    if redo:
        items = []
        for sid in sorted(redo):
            c = cands.get(sid) or Candidate(strategy_id=sid, strategy_name=names[sid], text="（缺失）")
            probs = [f"{k.rule}：{k.detail}" for k in c.checks if not k.ok]
            if sid in redo_sim:
                probs.append("与另一条候选太像，换一个角度和句式")
            if sid not in cands:
                probs.append("这一条缺失，请补写")
            items.append((c, probs))
        fix = llm.tool_call("repair", system, prompts.repair_prompt(user, items), "submit_candidates",
                            prompts.REPAIR_SCHEMA, config.TEMPERATURE["repair"], config.MAX_TOKENS["repair"])
        for sid, new in to_cands(fix.get("candidates")).items():
            if sid in redo:
                new.repaired = True
                new.previous_text = cands[sid].text if sid in cands else None
                validate(new)
                cands[sid] = new
        ordered = [cands[s] for s in names if s in cands]
        set_checks, _ = check_set(ordered, address)

    result["candidates"] = [c.model_dump() | {"passed": c.passed} for c in ordered]
    result["set_checks"] = [c.model_dump() for c in set_checks]
    failed = [c.strategy_id for c in ordered if not c.passed] + [c.rule for c in set_checks if not c.ok]
    if failed:
        notes.append("以下检查仍未通过，请 CA 人工确认：" + "、".join(failed))
    return _finish(result, ctx, trace, t0, save)


def _finish(result, ctx, trace, t0, save):
    result["input"] = ctx
    result["meta"]["seconds"] = round(time.time() - t0, 1)
    result["meta"]["calls"] = [(t["node"], t["seconds"]) for t in trace]
    if save:
        d = ROOT / "runs" / "adhoc"
        d.mkdir(parents=True, exist_ok=True)
        (d / f"{time.strftime('%Y%m%d-%H%M%S')}_{result['case_id']}.json").write_text(
            json.dumps({"result": result, "trace": trace}, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    return result
