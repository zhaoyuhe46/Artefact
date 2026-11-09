# XUYU Message Studio

Drafts three Chinese WeChat messages for a luxury Client Advisor (CA), using retrieval from a brand database and a client database. The brand (序隅 XUYU), its clients and all conversations are fictional.

- **How it works**: `deliverables/Report.md` §2 and `deliverables/Architecture.png`
- **Results**: `deliverables/Test_Cases_v3.xlsx` (final); v1 and v2 are kept for comparison

## Layout

```
data/brand/       brand book + products/services/policies/care/events/rules (YAML source of truth)
data/clients/     fictional client profiles, purchases, sensitivities, chat history
db/               schema.sql (Postgres: schema brand + schema crm + pgvector), seed.py, index_brand.py
skills/<mode>/SKILL.md   per-scenario writing guide: fact packs to retrieve + strategies A/B/C
xuyu/             engine: kb (SQL + vector retrieval), pipeline (guards → retrieve → generate → validate → repair), validate (R1–R13)
api/ + public/    Vercel functions and the web page
eval/             50 test cases, batch runner, LLM judge, xlsx export, human–judge agreement
runs/<version>/   raw results and judge scores per version
```

## Run locally

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
# .env needs: OPENROUTER_API_KEY, DATABASE_URL (Neon Postgres with pgvector)
.venv/bin/python db/seed.py                       # creates the schemas, loads the data and builds the vector index
.venv/bin/python cli.py --client C02 --scenario price_objection
.venv/bin/python dev_server.py                    # http://localhost:8000
```

## Evaluate

```bash
.venv/bin/python eval/run_batch.py v3             # 50 cases → runs/v3/
.venv/bin/python eval/judge.py v3                 # Claude judge → runs/v3/judge/, summary.json
.venv/bin/python eval/export_xlsx.py v3           # deliverables/Test_Cases_v3.xlsx
.venv/bin/python eval/export_xlsx.py v1 --blind   # blind-review sheet
.venv/bin/python eval/agreement.py v1             # human vs judge agreement
```

## Models (via OpenRouter)

| Role | Model |
|---|---|
| Generation | `deepseek/deepseek-v4.1-flash-1017` (pinned snapshot) |
| Judge | `anthropic/claude-sonnet-5.5` |
| Embeddings | `openai/text-embedding-3-small` |
