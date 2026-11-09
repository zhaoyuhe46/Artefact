"""结构化记录 → 品牌文本块。index_brand.py 建索引和设置页修改后重算向量都用这里，保证格式一致。"""

CARE_TAGS = ["post_purchase", "reactivation", "event"]
SERVICE_TAGS = ["post_purchase", "reactivation", "price_objection"]
POLICY_TAGS = {
    "POL:PRICE-UNIFIED": ["price_objection"], "POL:PRICE-OPEN": ["price_objection", "recommendation"],
    "POL:FIT-USE": ["price_objection", "recommendation"], "POL:STOCK": ["recommendation", "price_objection", "post_purchase"],
    "POL:CARE": ["post_purchase", "reactivation"], "POL:PROMISE": ["post_purchase", "price_objection", "recommendation"],
    "POL:CONTACT": ["all"],
}


def care_chunk(x):
    return dict(id=x["id"], source="care", chunk_type="care", scenario_tags=CARE_TAGS,
                title=f"{x['material']}·{x['topic']}", text=f"{x['material']}的{x['topic']}：{x['advice']}")


def policy_chunk(p):
    return dict(id=p["id"], source="policies", chunk_type="policy", scenario_tags=POLICY_TAGS.get(p["id"], ["all"]),
                title=p["title"], text=f"政策「{p['title']}」：{p['text']}")


def service_chunk(s):
    return dict(id=s["id"], source="services", chunk_type="service", scenario_tags=SERVICE_TAGS,
                title=s["name"], text=f"售后服务「{s['name']}」：{s['detail']}")


def embed_input(c):
    return f"{c['title']}\n{c['text']}"
