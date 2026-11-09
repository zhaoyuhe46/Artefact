"""生成与重写的提示词。品牌规则来自 brand.rules，场景写法来自 SKILL.md，资料来自检索。"""
import json

from . import config


def brand_block(rules):
    b, v = rules["brand"], rules["voice"]
    return (f"品牌：{b['name']}（虚构）。主张：{b['tagline']}\n定位：{b['positioning']}\n"
            f"客户经理角色：{rules['advisor']['role']}\n语气要求：\n- " + "\n- ".join(v))


def system_prompt(rules, skill, advisor):
    L = rules["length"]
    lo, hi = L["event_invitation"] if skill["scenario"] == "event" else L["default"]
    return f"""你是序隅的客户经理{advisor}，要在微信上给一位客户发一条一对一消息。请写出三条候选，供 CA 挑选后发送。
{brand_block(rules)}

硬性规则（每一条候选都要遵守）：
- 中文，像真人在微信里说话；不用表情符号，不用感叹号堆情绪，不堆砌辞藻。
- 字数硬上限 {hi} 字（标点也算），目标 {config.TARGET_LEN[0]}–{config.TARGET_LEN[1] + (20 if skill['scenario'] == 'event' else 0)} 字，宁短勿长。
- 正文里不要出现方括号或资料编号（编号只写在 refs 里）。
- 最多一个问句。
- 只用「硬事实资料」里的事实：产品名用「」括起简称（如「序」），价格必须与资料一致；不得编造库存、颜色、交期、赠品、折扣、活动细节。
- 不打折、不抹零、不承诺赠品，不说「找店长」，不贬低其他渠道。
- 不提消费金额、会员等级、多久没来；不暴露敏感私事；个性化细节必须能在客户资料或对话历史里找到出处。
- 不用「亲」「宝子」「亲爱的」「姐」等称呼，按客户资料里的称呼称呼客户。
- 三条按 A / B / C 三种策略写，侧重点要明显不同；不能靠编造折扣、赠品、库存来制造差异。
- 每条的 refs 填写正文实际用到的资料编号（方括号里的编号）。
- 不编造客户说过的话或做过的事：「您之前提过……」后面的内容必须能在对话历史或备注里找到原话；没有对话历史时，不说「之前联系过」。客户资料里的 notes 是 CA 的内部记录，不是客户原话，不能写成「您说过……」。
- 产品只说资料里列出的事实（例如不能把「可拆卸肩带」说成「手提或肩背都行」，不能自己补充重量、容量、颜色）。
- 不承诺发送资料里没有的信息（如尺寸表、内部布局图）；需要时说「我核实后发您」。
- 三条的开头不要相同，句式和切入点要明显不同。

===== 场景指引 =====
{skill['body']}"""


def _history(ctx, n):
    h = ctx["history"][-n:]
    if not h:
        return "（无对话历史）"
    return "\n".join(f"[H:{i + 1}] {x['time']} {'客户' if x['role'] == 'client' else 'CA'}：{x['text']}" for i, x in enumerate(h))


def _client(ctx):
    c = ctx["client"]
    keep = {k: c.get(k) for k in ["address", "relationship", "since", "preferences", "budget", "contact_prefs",
                                    "key_dates", "notes", "purchases"]}
    return json.dumps(keep, ensure_ascii=False)


def user_prompt(ctx, facts, rag, constraints, n_hist):
    f = "\n".join(f"[{r['ref']}] {r['text']}" for r in facts) or "（本场景不需要产品、服务或活动资料）"
    k = "\n".join(f"[{r['ref']}] {r['text']}" for r in rag) or "（无）"
    return f"""今天：{ctx['today']}
场景补充信息：{json.dumps(ctx['scenario_context'], ensure_ascii=False) if ctx['scenario_context'] else '无'}

客户资料 [{ctx['client']['ref']}]：
{_client(ctx)}

对话历史（按时间，最后一条最新）：
{_history(ctx, n_hist)}

必须遵守的额外约束：
{chr(10).join('- ' + c for c in constraints) or '- 无'}

硬事实资料（价格、产品、服务、活动只能来自这里）：
{f}

品牌知识（从品牌手册检索到的片段，用来把握语气、分寸和写法）：
{k}

请先做简短分析，再按 A / B / C 三条策略各写一条消息，调用工具提交。"""


GEN_SCHEMA = {
    "type": "object",
    "properties": {
        "analysis": {
            "type": "object",
            "properties": {
                "address_form": {"type": "string", "description": "应使用的称呼"},
                "client_waiting": {"type": "boolean", "description": "客户最后一条消息是否在等 CA 回复"},
                "reply_to": {"type": ["string", "null"], "description": "需要先回应的客户原话，没有填 null"},
                "usable_hooks": {"type": "array", "items": {"type": "string"}, "description": "可以自然提起的个人细节（带出处编号）"},
                "do_not_mention": {"type": "array", "items": {"type": "string"}, "description": "知道但不能提的内容，写成 2-4 字关键词"},
            },
            "required": ["address_form", "client_waiting", "usable_hooks", "do_not_mention"],
        },
        "candidates": {
            "type": "array", "minItems": 3, "maxItems": 3,
            "items": {
                "type": "object",
                "properties": {
                    "strategy_id": {"type": "string", "enum": ["A", "B", "C"]},
                    "text": {"type": "string", "description": "发给客户的微信消息正文"},
                    "rationale": {"type": "string", "description": "一句话说明为什么这样写（给 CA 看）"},
                    "refs": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["strategy_id", "text", "rationale", "refs"],
            },
        },
        "ca_note": {"type": "string", "description": "给 CA 的一句提示（时机、风险、需要补充的信息），没有就留空"},
    },
    "required": ["analysis", "candidates", "ca_note"],
}

REPAIR_SCHEMA = {
    "type": "object",
    "properties": {"candidates": GEN_SCHEMA["properties"]["candidates"] | {"minItems": 1}},
    "required": ["candidates"],
}


def repair_prompt(user, items):
    lines = "\n".join(f"- 策略 {c.strategy_id}「{c.strategy_name}」上一版：{c.text}\n  问题：{'；'.join(p)}" for c, p in items)
    return f"""{user}

===== 重写 =====
下面几条候选没有通过检查。请保持各自的策略不变，逐条改写，解决列出的问题；只提交需要重写的这几条。
字数超标的，删掉次要信息，压缩到 {config.TARGET_LEN[1]} 字以内，不要只删几个字。
{lines}"""
