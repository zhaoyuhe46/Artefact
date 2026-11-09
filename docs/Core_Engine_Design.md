# 核心引擎设计（v0.4：模拟品牌数据库 + 策略 skill）

范围：只覆盖 brief 的核心功能——输入客户信息、场景、对话历史、产品信息，输出三条中文微信话术，覆盖六个场景。网页、部署、批量评估等运行时内容暂不在本文范围内。

品牌、产品、价格与政策的唯一事实来源是 `Brand_Book.md`（序隅 XUYU，虚构高端皮具品牌），本文不重复抄录。

## 1. 已定决策

| 项 | 决定 |
|---|---|
| 品牌 | 序隅 XUYU，当代中国高端皮具；6 个固定产品（XY-S01 至 XY-T01）。取代早先定的香水品类 |
| 流程 | 五个节点：N1 意图与情境识别 → N2 客户解读 + 三条策略 → N3 写作 → N4 规则校验 → N5 定点重写（最多一次） |
| 框架 | 不用 LangChain / LangGraph，自写轻量节点流水线：每个节点一个函数，输入输出为 Pydantic 类型，节点间共享 `State`，路由和回环集中在 `run()` 里 |
| 三条候选 | 每个场景各自定义三条策略（第 4 节） |
| 输入形式 | 结构化字段 + 自由备注；对话历史按「客户 / CA」分行 |
| 语言与 SDK | Python；只用 `anthropic` SDK：生成调 DeepSeek 的 Anthropic 兼容接口，打分调 Claude |
| 知识注入 | 模拟品牌数据库（SQLite）+ 确定性检索：每条策略是一个 skill，声明自己要取哪些数据，由代码查库后交给模型。不用向量检索（数据量小；DeepSeek 与 Claude 均无 embedding 接口） |
| 客户输入 | 填客户 ID 自动从库里带出资料；全新客户可以手填，手填内容覆盖库里的同名字段。产品、服务、养护、活动始终从库里取 |
| 决策方式 | 小决定由 Claude 自行决定并记录在本文档；涉及品牌、政策、输出形式的问题再问 Joey |

## 2. 好话术的定义

以 `Brand_Book.md` 第九节为准，并补充 Joey 确认的两条：

- **字数：** 硬约束 10–80 字，活动邀约 10–100 字。
- **措辞：** 不堆叠不必要的辞藻。

**不合格（任一即否决）：** 虚构产品信息、价格错误、越权承诺、隐私冒犯、购买施压。另外，超出字数、违反停止营销的要求、在客户亲人离世 30 天内主动联系，也判为不合格。

## 3. 流程

```
CaseInput（client_id 和 / 或手填字段）
  │
  ├─ N0 load()        查库带出客户资料、购买记录、对话历史，与手填内容合并
  ├─ N1 classify()    deepseek-flash，关思考，temperature 0，强制工具调用 → Signals
  │     ↓ 按标签路由（见下表）
  ├─ N2 understand()  开思考，输出 JSON 文本 → Pydantic 校验（失败重试 ≤2 次）
  │     → ClientRead：客户解读 + 时机判断 + 三条策略各自的计划
  ├─ R  retrieve()    按三个策略 skill 声明的检索项查库 → 三份带引用编号的资料包
  ├─ N3 write() ×3    并行，每条策略一次调用：skill 指令 + 自己的资料包；关思考，temperature≈1.3，强制工具调用
  │     → 每条：正文 + 一句理由 + 引用的记录编号；另汇总给 CA 的提示
  ├─ N4 validate()    纯代码规则 R1–R11
  │     └─ 不通过的那条 → N5 repair()（带失败原因重写一次）→ 回到 N4，仍不通过则标红
  └─ Result：status（generated / held）+ signals + client_read + 3 × {strategy, text, rationale, checks} + ca_note + meta
     （status = held 时 N1 之后直接结束，不调用 N2–N5，只返回 ca_note）
```

### N1 输出的标签（Signals）

| 字段 | 取值 |
|---|---|
| `latest_intent` | none / question / complaint / price_objection / positive_feedback / greeting / disengaged |
| `objection_type` | 只在价格异议时填：too_expensive / channel_comparison / discount_request / budget_limit |
| `open_question` | 客户还没被回答的问题原文，没有则为空 |
| `scenario_fit` | match / mismatch，不吻合时给出建议的场景 |
| `no_marketing` | 客户是否明确要求过停止营销联系（true / false），附原话出处 |
| `sensitivity_flags` | recent_complaint / bereavement / health / private_life |
| `bereavement_days` | 只在有 bereavement 时填：距离得知离世消息的天数（依据输入中的日期推算，推算不出时按 30 天以内处理） |
| `client_initiated` | 客户最后一条消息是否在等 CA 回复（true / false） |
| `missing_event_fields` | 只在活动邀约时填：缺少时间、地点或内容中的哪几项 |
| `send_advice` | send / send_with_caution / not_recommended |

### 路由规则

| 情况 | 处理 |
|---|---|
| `no_marketing = true` 且场景是产品推荐、活动邀约或沉睡客户唤醒 | 跳过原场景，改为生成三条发给客户的简短致歉（策略见第 4 节最后一行），不含任何产品或活动信息；`ca_note` 说明原因 |
| `no_marketing = true` 且场景是生日节日、购后关怀或价格异议 | 照常生成，但不得附带任何推荐 |
| 有 `open_question` | 三条都先回答这个问题，再按策略展开；答不了的写「我先核实一下再回复您」 |
| complaint 或 recent_complaint | 三条都先回应不满，不推产品 |
| bereavement，且离世消息在 30 天以内，CA 主动联系 | 不生成任何消息（`status = held`），给客户留出空间；`ca_note` 建议 30 天后再以「沉睡客户唤醒」场景发一次问候 |
| bereavement，且离世消息在 30 天以内，客户主动发来消息（如问售后、问价格） | 照常输出三条，只回答客户问的事，语气克制，不推荐、不邀约 |
| bereavement，离世消息已超过 30 天 | 只允许「沉睡客户唤醒」场景，三条都是不带任何请求的问候；其他 CA 主动发起的场景仍不生成 |
| `missing_event_fields` 非空 | 缺失的信息在话术中原位写成「TBD」，`ca_note` 列出缺了哪些 |
| `scenario_fit = mismatch` | 仍按所选场景生成，写法服从客户的真实意图，并在 `ca_note` 中说明 |
| `send_advice = not_recommended` | 仍给三条，`ca_note` 写明风险和建议的时机 |

### 数据层：模拟品牌数据库

- **来源与运行：** `knowledge/*.yaml` 是可读可改的源文件，`build_db.py` 生成运行时用的 `data/xuyu.db`（SQLite）。
- **表：**
  - 品牌知识：products、services、care_guides、policies、events
  - 客户：clients、purchases、key_dates、sensitivities、conversations
- **引用编号：** 每条记录带一个编号，如 `P:XY-B02`、`SVC:CLEAN-12M`、`CARE:GRAIN-01`、`EVT:2026-11-14`、`C:C03`；手填内容的编号为 `INPUT:<字段名>`。
- **检索函数（`retrieval.py`）：**
  - `get_client`、`get_purchases`、`get_history`
  - `get_product`、`find_products(use, max_price, exclude)`
  - `get_care(material)`、`get_services`、`get_events(upcoming)`、`get_policy(keys)`
- **数据内容：** 全部虚构，由 Claude 整理、Joey 审核。养护建议只写日常打理方法，不写防水、不磨损这类夸大说法。

### 策略 skill

每个场景的 A / B / C 各对应一个 skill 文件 `skills/<场景>/<A|B|C>.yaml`，内容包括：

- `intent`：这条策略要达成什么
- `must` / `must_not`：必须包含、不能出现的内容
- `retrieve`：要从库里取的数据，参数取自输入和 N1 / N2 的结果。例如价格异议 C「预算」写成 `find_products(use=<客户用途>, max_price=<客户预算>, exclude=<当前产品>)`
- `examples`：品牌手册里的范例和反例

三条并行写作时彼此看不到，靠两点保证差异：skill 本身侧重点不同，以及 R8 差异度检查。不通过的那条由 N5 重写。

### 事实核对的加强

每条话术都要返回 `refs`（引用的记录编号）。N4 额外检查两点：

- `refs` 必须是本条资料包里的编号；
- 正文里出现的产品名、价格、服务条件、活动时间，必须能在 `refs` 对应的记录里找到。

### DeepSeek 接口限制带来的实现约束

依据 DeepSeek 的 [Anthropic 兼容接口文档](https://api-docs.deepseek.com/guides/anthropic_api)：

- 开思考时 temperature 固定为 1.0，且不能强制指定工具，所以 N2 输出 JSON 文本，由代码解析。
- 关思考时可以强制工具调用，所以 N1、N3、N5 走工具调用。

总耗时预计 20–30 秒（N3 三条并行）。

### N1 的评估价值

50 个用例预先标注 Signals 的正确值，用来计算 N1 每个字段的准确率，作为评估报告 D 的硬指标。

## 4. 六个场景的三条策略

通用规则：

- 每张场景卡写明三条策略的定义、必须包含、不能出现，并附手册里的范例。
- 评估时由打分模型判断每条话术属于哪条策略，检查是否对得上。
- 三条之间的差异只能来自侧重点，不能靠编造折扣、赠品、库存来制造差异（手册第七节）。

| 场景 | 三条沿什么拉开 | A | B | C |
|---|---|---|---|---|
| 生日 / 节日 | 祝福的写法 | 简短真挚的祝福 | 结合客户提过的个人细节 | 礼貌得体的祝福问候（正式、用「您」，适合关系尚浅的客户） |
| 产品推荐 | 推荐理由从哪来 | 客户明确表达过的需求 → 具体产品事实 | 客户的使用场景（通勤、出行、会面、晚间） | 轻量的下一步（看内部布局、发尺寸资料、到店看实物） |
| 购后关怀 | 关怀的内容 | 问使用是否顺手 | 与所购产品相关的使用或养护建议 | 服务提醒：告诉客户购买后可以享受哪些售后服务，挑与所购产品和客户情况最相关的一项来说即可（服务内容和条件只能取自品牌手册，不得自创） |
| 沉睡客户唤醒 | 重新联系的由头 | 纯问候，零请求 | 与客户之前提过的需求相关的资料 | 已确认的活动或服务（例如仍在免费清洁期内） |
| 活动邀约 | 用什么打动客户 | 信息清晰版（内容、时间、地点，加一句为什么想到您） | 关系版（从客户的兴趣说起） | 体验描述版（现场能看到、了解到什么） |
| 价格异议 | 回应的路径（手册第七节） | 政策：统一定价，没有额外折扣 | 用途：对应客户在意的产品事实 | 预算：征得同意后按预算找符合用途的款式，也可以先等等 |
| 停止营销时的替代输出 | 致歉的侧重 | 简短致歉 | 致歉并确认之后不再主动发送产品与活动信息 | 致歉并说明售后服务问题仍可随时联系 |

## 5. 数据结构（Pydantic）

**CaseInput**

- `today`：日期
- `client_id`：可选；填了就从库里带出资料，下面的手填字段会覆盖库里的同名字段
- `advisor_name`：CA 的名字，如「安然」
- `client`：
  - `address`：称呼
  - `relationship`：新客 / 常客 / 重要客户 / 沉睡
  - `since`：成为客户的时间
  - `preferences`：颜色、用途、风格偏好，以及表达过的预算
  - `purchases[]`：日期、SKU、备注
  - `key_dates[]`：类型、日期
  - `contact_prefs`：联系频率、时段偏好，以及是否要求过停止营销
  - `sensitivities[]`：敏感事项
  - `notes`：自由备注
- `scenario`：六选一的枚举
- `scenario_context`：节日名称、活动内容 / 时间 / 地点、客户的异议原话等
- `history[]`：`role`（client / ca）、`time`、`text`
- `products[]`：可选，填 SKU 编号；目录外的产品信息必须标记为「已核实」才能使用

**Signals（N1 输出）**：字段见第 3 节。

**ClientRead（N2 输出）**

- `relationship_stage`、`address_form`
- `register`：客户的正式程度、平均消息长度
- `usable_hooks[]`：可以提的个人细节，每条带出处
- `do_not_mention[]`：知道但不能提的细节，每条带原因和出处
- `conversation_state`：谁最后说话、有无未回答的问题、距上次联系多少天
- `timing`：是否适合发送（`ok`）、风险、需要做的调整
- `plans[3]`：每条包括策略 ID、用到的个人细节、用到的产品 SKU、请求力度、目标字数

**WriteOutput（N3 / N5 输出）**

- `candidates[3]`：策略 ID、正文、一句理由、提到的 SKU
- `ca_note`：给 CA 的提示

## 6. 规则校验（代码，R1–R11）

| ID | 检查内容 | 不通过的判定 |
|---|---|---|
| R1 | 禁用表达 | 命中手册第八节的表达，或「宝子」「亲爱的」「姐」「亲」等称呼 |
| R2 | 价格政策 | 出现折扣、优惠、抹零、返现、赠品、送您、找店长等字样（「没有额外折扣」这类否定说法除外） |
| R3 | 价格与数字 | 提到某产品的价格时必须与目录完全一致；其他数字必须出现在输入里 |
| R4 | 产品真实性 | 「」内的产品名必须在目录里，且与 SKU 对应 |
| R5 | 字数 | 10–80 字；活动邀约 10–100 字 |
| R6 | 问句数 | 每条最多一个问句 |
| R7 | 越权承诺 | 「一定」「保证」「肯定有货」「明天到」「免费换新」「退款」「保值」「升值」等 |
| R8 | 差异度 | 三条两两字符二元组 Jaccard 相似度不超过阈值（初定 0.5，实测后再调） |
| R9 | 齐备性 | 刚好三条，策略 ID 是 A / B / C 各一；`status = held` 时必须为零条，且 `ca_note` 不为空 |
| R10 | 禁区 | `do_not_mention` 里的关键词不得出现在正文里 |
| R11 | 停止营销与 TBD | 致歉输出里不得出现产品名、价格或活动；「TBD」只允许出现在活动邀约中 |

是否虚构了客户经历、语气是否符合品牌、有没有堆砌辞藻，这些交给后续的 Claude 打分判断，不在代码规则里做。

## 7. 代码结构

```
data/xuyu.db     由 build_db.py 生成
build_db.py      knowledge/*.yaml → SQLite
engine/
  retrieval.py   检索函数，返回带引用编号的记录
  skills.py      读取 skills/ 并解析 retrieve 声明
  config.py      模型、温度、阈值、版本号
  schemas.py     Pydantic 数据结构
  llm.py         两个 anthropic 客户端（DeepSeek / Claude），统一重试与日志
  state.py       节点间共享的 State
  nodes/
    classify.py    N1
    understand.py  N2
    write.py       N3
    validate.py    N4（R1–R11）
    repair.py      N5
  pipeline.py    run(case) -> Result：节点顺序、路由、回环
  trace.py       每个节点的输入输出写入 runs/<时间戳>.jsonl
knowledge/
  brand.yaml         由 Brand_Book.md 整理：语气、禁用表达、政策、字数规则
  products.yaml      6 个 SKU 及其产品事实
  services.yaml  care.yaml  events.yaml  policies.yaml
  clients.yaml       虚构客户、购买记录、关键日期、敏感事项、对话历史
skills/
  <场景>/card.yaml   场景目标、时机、禁区
  <场景>/A|B|C.yaml  三条策略 skill（含停止营销的致歉卡）
prompts/
  classify.j2  understand.j2  write.j2  repair.j2
cli.py           python cli.py --case cases/x.json
```

版本号由 `prompts/` 和 `knowledge/` 的内容哈希加手动标签（v1、v2）组成，写进每次输出的 `meta`。

## 8. 实现记录（Claude 自行决定的小事）

- **数据库建在内存里：** 共享文件夹上的 SQLite 文件会报 I/O 错误，改为启动时由 YAML 在内存中重建 SQLite（数据很小，耗时几毫秒），检索代码不变。`data/` 下残留的两个空库文件没有用到，已加入 `.gitignore`。
- **提示词放在代码里：** 写在 `engine/prompts.py`，没有用 Jinja2 模板文件，少一层依赖；版本哈希同样覆盖它。
- **skill 文件结构：** 每个场景一个 `skills/<场景>/skills.yaml`，里面放场景卡和 A/B/C 三个 skill；停止营销用 `_no_marketing`，离世 30 天后的问候用 `_bereavement_followup`。
- **资料包名称：** client_core、history、focus_product、products_by_use、purchased_products、care_for_purchased、services、policies_price、budget_alternatives、events_upcoming、event_target。
- **派生记录（DER:）：** 由代码根据数据算出的事实，例如「仍在 12 个月免费清洁期内」「预算内没有符合用途的款式」，同样带编号、可校验。
- **结构化数据优先：** 停止营销、离世日期、投诉记录以客户资料为准，模型只补充从自由文本里识别出的情况。
- **新增 R12 引用检查**，以及 R2 对「九折」「8.5折」这类写法的识别。
- **虚构客户：** C01–C10 各对应一种典型情况：购后、价格异议、沉睡、旅行常客、停止营销、亲人离世、近期投诉、活动邀约对象、渠道比价、预算受限。

## 9. 待定事项

- **待审：** 养护建议已整理进 `knowledge/care.yaml`（11 条），虚构客户在 `knowledge/clients.yaml`，活动在 `knowledge/events.yaml`，需要 Joey 审核。
- **模型选择：** 用 `deepseek-flash` 还是 `deepseek-v4-pro`，用几条样例实测后决定。
- **运行时形态：** 界面、部署、批量评估，等核心引擎跑通后再定。
- **下一步（可写进 E）：** 规模变大时迁移到 LangGraph。
