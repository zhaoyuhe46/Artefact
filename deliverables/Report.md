# XUYU Message Studio — Methodology, Evaluation, Defects

All brands, people, products and conversations in this submission are fictional.

## 0. Summary

XUYU Message Studio drafts three Chinese WeChat messages for a luxury Client Advisor (CA). The CA picks one, edits it if needed, and sends it.

The tool covers six scenarios: birthday and festive greetings, product recommendation, post-purchase care, reactivation, event invitation and price objection.

Before it writes anything, the tool looks up two databases:

- a **brand database**, which holds products, policies, services, care guides, events, and the brand book split into retrievable chunks;
- a **client database**, which holds each client's profile, purchases, key dates, sensitivities and chat history.

Every claim in a message has to be traceable to a record in one of these databases. Rules written in code then check every message. Some situations should not get a marketing message at all: a client who has opted out of marketing, a client who recently lost a family member, a client with an unresolved complaint. In these cases the decision is made by code, not left to the model.

I tested the tool on 50 cases (150 messages), scored them with rules and with an LLM judge from a different vendor, fixed the defects that came up, and re-ran all 50 cases as v2.

**Headline (v1 → v3, final):**
- Vetoed messages fell from 2 to 0.
- Messages passing every rule rose from 97% to 99%.
- Cases handled as expected rose from 43/50 to 45/50.
- The average "ready to send" score rose from 3.34 to 3.57 out of 5.

The main remaining weakness is **personalisation** (3.1/5). Only 20% of messages meet the strict bar of "4 or more on every dimension".

## 1. The brand and my assumptions (B)

**序隅 XUYU** is a fictional contemporary Chinese leather-goods house. Its tagline is 「让日常，有自己的秩序。」("Give everyday life its own order.") Its designs draw on architectural lines and proportions. The full brand book is in `data/brand/Brand_Book.md`.

| Topic | Assumption |
|---|---|
| Category and range | Six products from ¥3,800 to ¥38,800: two small leather goods, three handbags and one travel bag. Each product has a fixed list of facts. Anything not listed (stock, weight, delivery time) must be checked before a CA can mention it. |
| Clients | Urban professionals aged 28–45, of all genders. The CA recommends based on what the client has actually said about their use, budget and taste, never on assumptions about job, age or appearance. |
| Price policy | One price everywhere. CAs cannot discount, round down or offer gifts. When a client mentions a budget, the CA may suggest something cheaper only with the client's agreement and only if it suits their use. A card holder is never offered as a substitute for a work bag. |
| Service | One free basic clean within 12 months of purchase. Repairs and part replacements are quoted after an assessment. CAs never promise lifetime warranties, refunds or free fixes. |
| Voice | Natural, warm and measured, like a professional advisor writing one-to-one. One purpose and at most one question per message. 10–80 characters (up to 100 for event invitations). No emoji, no stacked exclamation marks, no 「亲 / 宝子 / 姐」. Products are explained through concrete facts, never through "status" or "top craftsmanship". |
| Boundaries | A client who has asked to stop receiving marketing gets no recommendations, invitations or reactivation messages. For 30 days after a client mentions a bereavement, the CA does not start a conversation. A client with an open complaint gets an apology and a follow-up before anything else. |
| Channel | CAs use WeCom to talk to clients on personal WeChat. The tool only drafts; the CA always reviews and sends. |
| Missing event details | If an event's time, place or content is not confirmed, the draft says "TBD" in that spot and the CA is told what is missing. |

## 2. How the tool works (B)

**Architecture diagram:** see `Architecture.png` (bonus question).

### 2.1 Data layer: one Postgres database, two schemas

The database is Neon Postgres with the pgvector extension.

**`brand` schema**
- Structured tables: `products`, `services`, `policies`, `care`, `events`, `rules`. The `rules` table holds tone rules and banned or required wording, shared by the prompt and the rule checks.
- `brand.chunks`: 50 chunks of brand text, each stored with a 1536-dimension embedding (`openai/text-embedding-3-small`) and an HNSW index.
  - **Chunking by meaning, not by length.** The brand book is split by section. Each row of the "communication principles" and "scenario" tables becomes its own chunk, and so does each taboo bullet. Each care, policy and service record is one chunk.
  - **Scenario tags.** Every chunk is tagged with the scenarios it applies to, so a search can be filtered by scenario before ranking by vector similarity.

**`crm` schema**
- `clients`, `purchases`, `key_dates`, `sensitivities`, `messages`.

**The key design decision is to split "hard facts" from "soft knowledge".**
- **Hard facts** are prices, product facts, service eligibility and event times. They are looked up exactly, by SKU or ID, with SQL. Products are deliberately not embedded at all.
- **Soft knowledge** is tone, example messages, taboos, care advice and policy wording. It is retrieved by vector search.

If vector search misses something, the message may sound less polished, but it can never get a price wrong. Some facts are also computed by code before the model sees them. For example, whether a client's free clean is still valid is worked out from the purchase date and handed to the model as a fact; the model never works out dates itself.

### 2.2 Pipeline

The tool makes at most three model calls per request: one embedding, one generation and one optional repair. A request takes 6–15 seconds.

1. **Load.** Fetch the client profile, purchases and last 8 messages from `crm`. If the CA has typed in a new client message, add it to the history.
2. **Guards (code, not model).** These decide which skill to use, or whether to write anything at all:
   - The client opted out of marketing, and the scenario is recommendation, event or reactivation: switch to `_no_marketing`, which writes three short apologies.
   - A bereavement was mentioned within the last 30 days and the CA would be starting the conversation: **held**. Nothing is generated, and the CA gets a note.
   - More than 30 days after a bereavement, in the reactivation scenario: switch to `_bereavement_followup`, which writes a greeting that asks for nothing.
   - The client has an open complaint and the scenario is a marketing or post-purchase one: switch to `_complaint_followup` (added in v2).
   - The client's last message is less than 3 days old: all three drafts must reply to it first.
   - An event's details are missing: write "TBD" in their place.
3. **Retrieve.**
   - Hard facts: each scenario's `SKILL.md` declares the fact packs it needs, for example `focus_product`, `budget_alternatives` or `services`. They are fetched by SQL.
   - Soft knowledge: a search query is built from the scenario name, the client's latest message and the client's preferences, then run against `brand.chunks` as a vector search filtered by scenario tag, returning the top 5.
   - Every record carries a reference ID, for example `P:XY-B02` or `BB:6.6`.
4. **Generate.** A single call to DeepSeek V4.1 Flash, pinned to snapshot `deepseek-v4.1-flash-1017`, with forced tool calling.
   - The system prompt contains the brand rules plus the body of the scenario's `SKILL.md`. Each SKILL.md defines three mutually exclusive strategies (A, B and C), each with what it must include, what it must avoid, and examples.
   - The model returns a short analysis, three candidates (each with text, rationale and reference IDs) and a note for the CA.
5. **Validate.** 13 rules run in code (R1–R13; see 4.1).
6. **Repair.** Candidates that fail any rule are rewritten in one batched call, with the failure reasons attached, and then checked again. Anything that still fails is flagged red for the CA.

### 2.3 Why the three candidates differ

The three candidates differ in **strategy**, not in wording. For example, the three price-objection strategies are:

- A: explain the one-price policy;
- B: connect the price to the facts the client cares about;
- C: offer, with the client's consent, to look within their budget.

Each strategy is defined in the scenario's SKILL.md. Rule R8 rejects any pair of candidates whose character-bigram Jaccard similarity is above 0.5. The candidates are not allowed to differ by inventing discounts, gifts or stock.

### 2.4 Deliberate choices

- **No LangChain.** The flow is fixed and short, so plain Python functions are easier to test and to explain.
- **Two vendors.** DeepSeek writes the messages and Claude judges them, so the model never grades its own work.
- **A fixed model snapshot**, so results can be reproduced.

## 3. Test set (B, C)

There are 50 cases (`eval/cases.yaml`). Eight to nine cover each scenario, and 20 of the 50 are edge cases.

They use the 10 fictional clients in the database plus 3 clients typed in by hand, including one with almost no information. Six cases add a new client message so that the price-objection variants can be tested.

Each case has a written **expected behaviour**, which the judge reads.

| Edge-case family | Cases |
|---|---|
| Opted out of marketing (recommendation, event, reactivation should become apologies; birthday, post-purchase and price questions continue without marketing) | TC-04, 15, 23, 31, 39, 47 |
| Bereavement (held within 30 days; a greeting only after 30 days; a client-initiated price question is still answered) | TC-08, 16, 24, 32, 48 |
| Open complaint | TC-07, 22, 30, 40, 49 |
| Price variants: cheaper through a personal shopper, duty-free comparison, "I'm a regular", second-item discount, over budget with nothing suitable in budget | TC-42–46, 14, 43 |
| Missing information: event details TBD; a client with no history; a purchase over 3 years old | TC-38, 27, 06, 21 |
| Scenario does not match the client's intent (the CA chose post-purchase or recommendation but the client is complaining or waiting on a price) | TC-49, 50 |

## 4. How I evaluated (B, D)

### 4.1 Rule checks (code, run on every message)

| Rule | What it checks |
|---|---|
| R1 | Banned phrases and forms of address, taken from brand book §8 |
| R2 | Discount wording ("没有额外折扣" — "no extra discount" — is allowed) |
| R3 | Every number appears in the input, and every price matches the catalogue |
| R4 | Every product named in 「」 exists and was among the retrieved facts |
| R5 | Length, emoji and exclamation marks |
| R6 | At most one question |
| R7 | Unauthorised promises: guarantees, free replacement, refunds, holding stock |
| R8 | The three candidates are different enough |
| R9 | Exactly three candidates, one per strategy |
| R10 | Topics the model itself flagged as off-limits do not appear |
| R11 | Mode-specific bans: no products in apology or bereavement modes; "TBD" only in event invitations |
| R12 | Reference IDs are real, and any message that names a product or price cites one |
| R13 | No reference IDs leak into the message text |

### 4.2 LLM judge

The judge is Claude Sonnet 5.5 via OpenRouter, a different vendor from the generator. It sees the case input, the retrieved facts, the expected behaviour and the three outputs, and returns, for each message:

- a 1–5 score on each of five dimensions: brand voice, personalisation, appropriateness and timing, factual consistency, and ready to send;
- whether the message follows its declared strategy;
- a **veto** flag with a reason;

and, for the case as a whole:

- a diversity score for the set of three;
- whether the tool handled the case as expected (`handling_ok`; for example, holding or switching to apologies when it should).

### 4.3 What "good" and "not acceptable" mean

- **Not acceptable (any one is enough):**
  - an invented product fact or a wrong price;
  - an unauthorised promise (discount, gift, free fix, stock);
  - a privacy breach or a mention of something sensitive;
  - pressure to buy or appeals to status;
  - bad timing, such as selling to a client who just complained or was just bereaved;
  - ignoring a marketing opt-out;
  - inventing something the client said or did.

  Code catches what it can (R1–R13). The judge's veto catches the rest.
- **Good:** passes every rule, has no veto, and scores at least 4 on all five dimensions, meaning a CA could send it with at most a light edit.
- **Acceptable:** has no veto, but needs editing before sending.

### 4.4 Human blind review

I rated 18 messages myself (three per scenario, sampled at random) without seeing any scores, labelling each good, acceptable or unacceptable. I then compared my labels with the judge's.

{{BLIND_REVIEW}}

## 5. Results (D)

Test set: 50 cases. Three are deliberately held (no message drafted), so 47 cases × 3 = 141 messages are scored per version. The judge is Claude Sonnet 5.5. Every v3 message appears in `Test_Cases_v3.xlsx` (the main deliverable C). v1 and v2 are provided for comparison.

### 5.1 Version comparison (all 141 messages)

| Version | Good | Veto | Rules pass | Brand voice | Personal. | Appropriateness | Factual | Ready to send | Handling OK | Diversity |
|---|---|---|---|---|---|---|---|---|---|---|
| v1 | 18% | 1.4% | 97% | 3.74 | 3.24 | 3.34 | 4.25 | 3.34 | 43/50 | 3.04 |
| v2 | 20% | 0.0% | 100% | 3.89 | 3.18 | 3.49 | 4.4 | 3.67 | 45/50 | 3.15 |
| v3 | 20% | 0.0% | 99% | 3.87 | 3.1 | 3.55 | 4.35 | 3.57 | 45/50 | 3.15 |

### 5.2 By scenario (v3; Good and Veto shown as v1 → v3)

| Scenario | n | Good | Veto | Brand voice | Personal. | Appropriateness | Factual | Ready to send |
|---|---|---|---|---|---|---|---|---|
| Birthday / festive | 21 | 24% → 14% | 0% → 0% | 4.19 | 2.52 | 4.19 | 4.76 | 4.14 |
| Recommendation | 24 | 12% → 25% | 4% → 0% | 4.0 | 3.54 | 3.58 | 3.71 | 3.17 |
| Post-purchase | 24 | 29% → 29% | 4% → 0% | 3.88 | 3.29 | 3.54 | 4.58 | 3.83 |
| Reactivation | 24 | 17% → 17% | 0% → 0% | 3.54 | 2.88 | 2.96 | 4.29 | 3.12 |
| Event invitation | 24 | 17% → 21% | 0% → 0% | 3.88 | 3.42 | 3.75 | 4.12 | 3.62 |
| Price objection | 24 | 8% → 12% | 0% → 0% | 3.75 | 2.88 | 3.33 | 4.67 | 3.62 |

### 5.3 What the numbers say

- **Safety is solid.**
  - After v2, nothing is vetoed and every message passes the rules.
  - All boundary cases are handled by code, so they behave the same every run: held for a recent bereavement (TC-08, 16, 24), apologies for marketing opt-outs (TC-15, 31, 39), and complaint follow-ups (TC-22, 40, 49).
  - Factual accuracy is the strongest dimension (4.35), because prices and services come from SQL, not from vector search.
- **Timing and tone improved in v2.** In birthday messages, the scores for appropriateness and ready to send both rose by about 0.6 once the tool stopped opening greetings with weeks-old chat.
- **Personalisation is the bottleneck.**
  - Strategy A ("short and sincere") and C ("courteous") are generic by design. The judge marks them down on personalisation even when it calls them "ready to send as is".
  - So part of the low score comes from my strategy definitions, not from errors.
  - The real gaps are in reactivation and event messages, which do not yet use the most relevant detail the client has given.
- **v3 vs v2.** The overall scores are within the judge's run-to-run noise. The specific defects v3 targeted did drop (table 6.2), so v3 is the final version.

### 5.4 Screenshots

- Web demo: `screenshots/demo_*.png`
- Spreadsheet views: `screenshots/xlsx_*.png`

## 6. Defects and changes (E)

### 6.1 How I found defects

1. I read the judge's comments for every message scoring 2 or below, every veto, and every case marked "not handled as expected".
2. I grouped them by root cause.
3. I fixed each cause at the right layer: data, code guard, rule, or prompt/SKILL.
4. I re-ran all 50 cases.
5. I counted each targeted defect with a script, so the before/after does not depend on judge noise.

Every result is tagged with its version and code hash (`v1-…`, `v2-…`, `v3-…`) in the "Version" column.

### 6.2 Defects and fixes

| # | Defect (found in) | Root cause | Fix (version) | v1 | v2 | v3 |
|---|---|---|---|---|---|---|
| D1 | Greetings and invitations open by replying to a message from weeks ago, e.g. a New Year greeting that starts with 「光面的事您说懂了」 ("You said you understood about the smooth leather"). (TC-03, 26, 29) | The code treated any last client message as "waiting for a reply". | Only messages from the last 3 days count as waiting; older ones come with an explicit "do not reply to this" note. (v2) | 31 | 8 | 9 |
| D2 | **Expired free clean offered as a benefit. This was the only veto in v1.** (TC-23, 26, 21, 22) | The generic service record was always retrieved, and the model worked out the 12-month window itself. | Code computes eligibility for each purchase. Expired purchases get an explicit "not applicable" fact, and the generic offer is dropped. (v2) | 4 | 0 | 0 |
| D3 | A client with an open complaint is still invited to an event or reminded of a service, with no apology. (TC-22, 40, 49) | The complaint was only a soft prompt instruction, and the scenario's own SKILL pulled the other way. | New `_complaint_followup` skill: apology plus progress, apology plus next step, short apology. The guard routes to it for marketing and post-purchase scenarios. (v2) | 4 | 0 | 0 |
| D4 | Over-length messages still too long after repair. (TC-43, 46, 50) | The prompt only stated the hard limit. Replying and then pitching in one message tends to overflow. | The prompt now targets 30–70 characters. Repair is told to cut to 70. A message that must reply first keeps the scenario part to one or two sentences. (v2) | 2 | 0 | 0 |
| D5 | A reference ID leaked into the text, e.g. `[EVT:TRAVEL-1121]`. (local test) | No rule covered it. | New rule R13, plus a prompt instruction. (v2) | – | 0 | 0 |
| D6 | Products mentioned in an event's description, or a client's own past purchase, were flagged as "not in facts". (TC-35, 01, 34) | Fact packs were too narrow. | Event packs now include the products the event mentions and the client's purchases. Birthday packs include purchases. (v2) | 3 | 0 | 0 |
| D7 | Price objection: asks "what's your budget?" when the client never mentioned one, which feels like assuming they can't afford it. (TC-44, 45, 47, 48) | Strategy C ("budget") was applied to every price objection. | Strategy C becomes "budget **or** a low-pressure next step": it asks about budget only if the client raised one. Opted-out and bereaved clients get an explicit "do not ask about budget". (v3) | 7 | 6 | 2 |
| D8 | Price objection: 「会员价我核实后回复您」 ("I'll check whether there's a member price"), which implies the price is negotiable. (TC-45, 50) | The prompt encouraged "I'll check" for unknowns, and the model applied it to price. | SKILL rule: one price is certain, never "check". R2 now catches "核实" (check) next to price words. (v3) | 2 | 5 | 1 |
| D9 | Reactivation: 「许久没联系」 ("we haven't been in touch for a long time"), an indirect way of saying "you haven't visited". (TC-25, 28, 29) | The banned list only had the literal phrase 「好久没来」 ("you haven't come by in ages"). | Eight gap phrases added to the banned list. (v3) | 0 | 5 | 1 |
| D10 | Data bug: a client's message 「24,800有点贵…」 ("24,800 is a bit expensive…") was stored as 「24」. | An unquoted comma inside YAML flow-style mappings split the text. | All message and case texts are now quoted. Found in the first smoke test, before v1. | – | – | – |

The D1 count is a keyword heuristic for phrases such as 「您说…」 ("you said…") and 「到时候再说」 ("let's talk later"). It over-counts a little, because some matches are legitimate references back to the client.

### 6.3 Before and after (same input)

**TC-23: post-purchase, client opted out of marketing, bag bought 2024-09 (more than 2 years ago)**
- v1 C (**vetoed**): 「提醒您一句：「隅」自购买起12个月内可预约一次免费基础清洁，需要时提前和我说，我帮您安排。」
  (*"A quick reminder: within 12 months of purchase you can book one free basic clean for the Yu bag. Let me know in advance and I'll arrange it."* The bag was bought more than two years ago, so this offer has expired.)
- v3 C: 「如果「隅」翻盖手袋有污渍或磕碰，别自己用清洁剂处理，可以先联系我安排评估，评估后再定方案。」
  (*"If the Yu flap bag gets a stain or a knock, please don't use cleaning products on it yourself. Contact me and I'll arrange an assessment, then we'll decide what to do."*)

**TC-03: New Year greeting; the last chat was three months ago**
- v1 C: 「上次光面皮料的问题，您回复已了解，我便不再赘述。值此元旦，谨祝您新年安顺……」
  (*"Regarding the smooth-leather question last time, you replied that you understood, so I won't go over it again. On the occasion of New Year's Day, I respectfully wish you a peaceful new year…"* It reopens an old chat, in stiff official language.)
- v3 B: 「郑女士，元旦快乐。记得您对皮面质感一直很有兴趣，愿新的一年里，喜欢的东西都能慢慢遇到。」
  (*"Ms Zheng, happy New Year. I remember you've always been interested in how leather surfaces feel. May the new year slowly bring you the things you love."*)

**TC-40: event invitation, but the client's repair quote is overdue**
- v1 A: 「拉链更换的报价我还没拿到，今天再跟店里确认后回您。11月21日周六……三里屯店有出行收纳小聚……」
  (*"I still don't have the zip replacement quote; I'll check with the store today and get back to you. On Saturday 21 November… the Sanlitun store is holding a travel-packing get-together…"* An invitation, with no apology.)
- v3 B: 「吴先生，报价拖了这些天，实在抱歉。我今天去跟进更换拉链的费用，确认后第一时间发您，方案和是否处理都由您决定。」
  (*"Mr Wu, I'm really sorry the quote has taken this long. I'll chase the zip replacement cost today and send it as soon as it's confirmed. Whether and how to go ahead is entirely your decision."*)

**TC-45: "Is there a member price? Duty-free seems cheaper."**
- v2 C: 「会员价我核实后再回您……如果这个价格超出您的考虑范围，我可以按您的预算再找……您看方便说下预算吗？」
  (*"I'll check on a member price and get back to you… If this price is beyond what you had in mind, I can look for something within your budget… Would you mind telling me your budget?"*)
- v3 C: 「没有会员价，「隅」是统一定价。若这款超出您的考虑范围，我先不劝。方便时到店看看实物，看过再决定也可以。」
  (*"There's no member price; the Yu is sold at one price everywhere. If it's more than you had in mind, I won't push it. Do drop by to see it in person when convenient, and decide after that."*)

**TC-26: reactivation, five months after the client's Kyoto trip**
- v1 C: 「您去年十二月买的「远行」旅行袋还在免费基础清洁期内……」
  (*"The Yuanxing travel bag you bought last December is still within its free basic clean period…"* This is false: the window has expired.)
- v3 B: 「上次您说「远行」旅行袋挺能装，我整理了一份它的日常养护要点，需要的话我发您。」
  (*"Last time you said the Yuanxing travel bag holds a lot. I've put together some everyday care tips for it; I can send them if you'd like."*)

## 7. What remains and next steps (E)

### 7.1 Remaining defects in v3

| Issue | Example | Proposed fix |
|---|---|---|
| Personalisation is uneven, especially in reactivation and events. | TC-33: A and C ignore that the client asked about smooth vs grained leather. | Make the model choose one "hook" per case (with its source) before writing, and require strategy B to use it. Add a hook-usage check. |
| Gap phrases still slip through in new wordings. | TC-26 v3 A: 「有段时间没打扰您了」 ("I haven't bothered you for a while") | A semantic check by the judge or a small classifier instead of a phrase list. |
| An implied hold ("I'll keep it for you to see") is not caught by R7. | TC-44 v3 C: 「把「隅」的实物留到您方便时」 ("keep the Yu aside until it suits you") | Add hold/reserve patterns to R7. Re-state "no holds" in the price SKILL. |
| A birthday during an open complaint does not reliably include an apology. | TC-07 | Treat the apology as required content, checked in code, or route to a "greeting + apology" skill. |
| Event invitations ignore the client's city. | TC-25, 27: Shanghai event for a client whose city is unknown | Add `city` to `crm.clients`, filter events by city, and tell the CA when the city is unknown. |
| "I'll check and send you the details" promises information we do not hold. | TC-09, 10, 13 | Store real dimension and layout assets (Neon Object Storage is already provisioned) and attach them; otherwise say nothing. |
| One judge run per version; scores move ±0.1 between runs. | v2 vs v3 | Judge three times and average, and report confidence intervals. Grow the human-labelled set to calibrate the judge. |

### 7.2 Product next steps

1. **WeCom sidebar.** Embed the web page as a WeCom chat-toolbar app. It can identify the current client, but it cannot read chat history, so the CA pastes the latest message, as in the demo. The 1/2/3 and C keys select and copy; the CA presses send.
2. **Learn from what CAs choose.** Log which candidate the CA picked and how they edited it. That gives preference data for tuning strategies and for few-shot examples per CA.
3. **Media.** Use Neon Object Storage for event posters and product detail images, and attach them to invitation and recommendation drafts.
4. **Operations.** Rate-limit the demo endpoint and add per-key spend caps. Add a nightly regression run of the 50 cases, with a rule pass-rate gate before any prompt or SKILL change ships.
