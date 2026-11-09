"""品牌文本切块 → embedding → brand.chunks（pgvector）。
切块策略：按语义单元切，而不是按固定字数切。
  - Brand_Book.md：按「## 节」切；节内的表格逐行成块（每行就是一条完整的原则或场景范例），列表逐条成块。
  - care / policies / services：每条记录一块。
  - 产品不做向量化：价格与产品事实只从 brand.products 按 SKU 精确查询，避免语义检索漏召回导致写错价格。
用法：.venv/bin/python db/index_brand.py"""
import re
import sys
from pathlib import Path

import psycopg
import yaml
from pgvector.psycopg import register_vector

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from xuyu import config, llm  # noqa: E402
from xuyu.chunks import care_chunk, embed_input, policy_chunk, service_chunk  # noqa: E402

B = ROOT / "data" / "brand"
ALL = ["all"]
SCEN_BY_NAME = {"生日与节日祝福": ["birthday"], "产品推荐": ["recommendation"], "购后关怀": ["post_purchase"],
                "沉睡客户唤醒": ["reactivation"], "活动邀约": ["event"], "价格异议": ["price_objection"]}


def sections(md):
    parts = re.split(r"^## ", md, flags=re.M)[1:]
    return {p.split("\n", 1)[0].strip(): p.split("\n", 1)[1] for p in parts}


def table_rows(text):
    rows = [r.strip().strip("|").split("|") for r in text.splitlines() if r.strip().startswith("|")]
    rows = [[c.strip() for c in r] for r in rows if not re.match(r"^[-:\s|]+$", "|".join(r))]
    return rows[0], rows[1:]


def prose(text):
    return "\n".join(l for l in text.splitlines() if not l.strip().startswith("|")).strip()


def bullets(text):
    return [l.strip()[2:].strip() for l in text.splitlines() if re.match(r"^\s*(-|\d+\.)\s", l)]


def build_chunks():
    md = (B / "Brand_Book.md").read_text(encoding="utf-8")
    S = sections(md)
    ch = []
    add = lambda cid, src, typ, tags, title, text: ch.append(dict(id=cid, source=src, chunk_type=typ, scenario_tags=tags, title=title, text=text))

    for name, sec in S.items():
        if name.startswith("一、"):
            add("BB:1", "Brand_Book §一", "positioning", ALL, "品牌设定", prose(sec))
        elif name.startswith("二、"):
            add("BB:2", "Brand_Book §二", "positioning", ALL, "客户与市场定位", prose(sec))
        elif name.startswith("三、"):
            add("BB:3", "Brand_Book §三", "policy", ["price_objection", "recommendation"], "定价逻辑", prose(sec))
        elif name.startswith("五、"):
            add("BB:5", "Brand_Book §五", "voice", ALL, "客户经理角色与语气", prose(sec))
            head, rows = table_rows(sec)
            for i, r in enumerate(rows, 1):
                add(f"BB:5.{i}", "Brand_Book §五", "voice", ALL, r[0], f"沟通原则「{r[0]}」。应该这样说：{r[1]}；应避免：{r[2]}")
        elif name.startswith("六、"):
            head, rows = table_rows(sec)
            for i, r in enumerate(rows, 1):
                add(f"BB:6.{i}", "Brand_Book §六", "scenario_example", SCEN_BY_NAME.get(r[0], ALL), r[0],
                    f"场景「{r[0]}」。沟通重点：{r[1]}。可以说：{r[2]}。不该说：{r[3]}")
        elif name.startswith("七、"):
            add("BB:7", "Brand_Book §七", "scenario_example", ["price_objection"], "价格异议三条完整候选", prose(sec))
        elif name.startswith("八、"):
            for i, b in enumerate(bullets(sec), 1):
                add(f"BB:8.{i}", "Brand_Book §八", "taboo", ALL, "明确禁止", b)
        elif name.startswith("修订"):
            tags = [ALL, ["event"], ["recommendation", "event", "reactivation"]]
            for i, b in enumerate(bullets(sec), 1):
                add(f"BB:R{i}", "Brand_Book 修订", "policy", tags[i - 1] if i <= len(tags) else ALL, "修订", b)

    for x in yaml.safe_load((B / "care.yaml").read_text(encoding="utf-8"))["care"]:
        ch.append(care_chunk(x))
    for p in yaml.safe_load((B / "policies.yaml").read_text(encoding="utf-8"))["policies"]:
        ch.append(policy_chunk(p))
    for s in yaml.safe_load((B / "services.yaml").read_text(encoding="utf-8"))["services"]:
        ch.append(service_chunk(s))
    return ch


def main(dry=False):
    chunks = build_chunks()
    if dry:
        for c in chunks:
            print(c["id"], c["chunk_type"], c["scenario_tags"], len(c["text"]), c["text"][:60].replace("\n", " "))
        print(len(chunks), "chunks")
        return
    vecs = llm.embed([embed_input(c) for c in chunks])
    with psycopg.connect(config.DATABASE_URL, autocommit=True) as con:
        register_vector(con)
        con.execute("TRUNCATE brand.chunks")
        import numpy as np
        with con.cursor() as cur:
            for c, v in zip(chunks, vecs):
                cur.execute("INSERT INTO brand.chunks VALUES (%s,%s,%s,%s,%s,%s,%s)",
                            (c["id"], c["source"], c["chunk_type"], c["scenario_tags"], c["title"], c["text"], np.array(v)))
        n, nn = con.execute("SELECT count(*), count(embedding) FROM brand.chunks").fetchone()
        print(f"brand.chunks: {n} 块，{nn} 块有向量（{len(vecs[0])} 维）")


if __name__ == "__main__":
    main(dry="--dry" in sys.argv)
