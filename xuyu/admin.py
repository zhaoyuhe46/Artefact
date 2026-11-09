"""设置页与聊天页的写操作。所有可写字段都在白名单里；改到会被向量检索的记录时，同步重算该块的向量。"""
import json
import re
from datetime import datetime

import numpy as np

from . import llm
from .chunks import care_chunk, embed_input, policy_chunk, service_chunk

J = lambda v: json.dumps(v, ensure_ascii=False)

# entity → (表, 主键, {字段: 类型})
EDITABLE = {
    "product": ("brand.products", "sku", {"name": "text", "category": "text", "price": "int", "material": "text",
                                          "facts": "jsonb", "uses": "textarray"}),
    "event": ("brand.events", "id", {"name": "text", "date": "date", "weekday": "text", "time": "text",
                                     "location": "text", "content": "text", "status": "text"}),
    "policy": ("brand.policies", "id", {"title": "text", "text": "text"}),
    "service": ("brand.services", "id", {"name": "text", "detail": "text"}),
    "care": ("brand.care", "id", {"material": "text", "topic": "text", "advice": "text"}),
    "client": ("crm.clients", "id", {"address": "text", "relationship": "text", "since": "text", "preferences": "jsonb",
                                     "budget": "int", "contact_prefs": "jsonb", "notes": "text"}),
}
RULE_KEYS = {"brand", "voice", "banned_phrases"}
CHUNKED = {"policy": policy_chunk, "service": service_chunk, "care": care_chunk}
LIST_KINDS = {"sensitivities": ("crm.sensitivities", ["type", "date", "note"]),
              "key_dates": ("crm.key_dates", ["type", "date"])}


def _cast(kind, v):
    if v in ("", None):
        return None
    if kind == "int":
        return int(re.sub(r"[^\d]", "", str(v)) or 0)
    if kind == "jsonb":
        return J(v)
    if kind == "textarray":
        return [x for x in (v if isinstance(v, list) else re.split(r"[、,，\n]", str(v))) if x.strip()]
    return str(v).strip()


def update(K, entity, rid, fields):
    if entity == "rule":
        if rid not in RULE_KEYS:
            raise ValueError(f"不可编辑的规则：{rid}")
        K.execute("UPDATE brand.rules SET value=%s WHERE key=%s", (J(fields["value"]), rid))
        K.refresh()
        return {"ok": True, "reindexed": False}
    table, pk, spec = EDITABLE[entity]
    cols = [(k, _cast(spec[k], v)) for k, v in fields.items() if k in spec]
    if not cols:
        raise ValueError("没有可更新的字段")
    sets = ", ".join(f"{k}=%s" for k, _ in cols)
    cur = K.execute(f"UPDATE {table} SET {sets} WHERE {pk}=%s", [v for _, v in cols] + [rid])
    if cur.rowcount == 0:
        raise KeyError(f"{entity} {rid} 不存在")
    if entity == "client" and "contact_prefs" in fields:
        K.execute("UPDATE crm.clients SET no_marketing=%s WHERE id=%s", (bool((fields["contact_prefs"] or {}).get("no_marketing")), rid))
    reindexed = False
    if entity in CHUNKED:      # 这几类记录在 brand.chunks 里有对应的向量块，改完立即重算
        row = K._rows(f"SELECT * FROM {table} WHERE {pk}=%s", (rid,))[0]
        c = CHUNKED[entity](row)
        vec = llm.embed(embed_input(c))[0]
        K.execute("UPDATE brand.chunks SET title=%s, text=%s, embedding=%s WHERE id=%s", (c["title"], c["text"], np.array(vec), rid))
        reindexed = True
    K.refresh()
    return {"ok": True, "reindexed": reindexed}


def replace_list(K, client_id, kind, rows):
    table, cols = LIST_KINDS[kind]
    K.execute(f"DELETE FROM {table} WHERE client_id=%s", (client_id,))
    for r in rows:
        vals = [r.get(c) or None for c in cols]
        if any(vals):
            K.execute(f"INSERT INTO {table} (client_id, {', '.join(cols)}) VALUES (%s{', %s' * len(cols)})", [client_id] + vals)
    return {"ok": True}


def add_message(K, client_id, role, text, time=None):
    if role not in ("client", "ca") or not text.strip():
        raise ValueError("role 必须是 client 或 ca，且内容不能为空")
    time = time or datetime.now().strftime("%Y-%m-%d %H:%M")
    K.execute("INSERT INTO crm.messages VALUES (%s,%s,%s,%s)", (client_id, time, role, text.strip()))
    return {"ok": True, "time": time}


def snapshot(K):
    """设置页和聊天页需要的全部数据（每次实时从库里读）。"""
    K.refresh()
    clients = [K.client(c["id"]) for c in K.list_clients()]
    q = lambda sql: K._rows(sql)
    return {
        "clients": clients,
        "products": K.products_all(),
        "events": q("SELECT * FROM brand.events ORDER BY id"),
        "policies": q("SELECT * FROM brand.policies ORDER BY id"),
        "services": q("SELECT * FROM brand.services ORDER BY id"),
        "care": q("SELECT * FROM brand.care ORDER BY id"),
        "rules": {k: v for k, v in K.brand_rules().items() if k in RULE_KEYS | {"advisor"}},
    }


def handle(K, body):
    a = body.get("action")
    if a == "update":
        return update(K, body["entity"], body["id"], body.get("fields") or {})
    if a == "replace_list":
        return replace_list(K, body["client_id"], body["kind"], body.get("rows") or [])
    if a == "message":
        return add_message(K, body["client_id"], body["role"], body["text"], body.get("time"))
    if a == "reset":
        import sys
        from .config import ROOT
        sys.path.insert(0, str(ROOT / "db"))
        import seed
        seed.main()
        K._connect()
        K.refresh()
        return {"ok": True}
    raise ValueError(f"未知操作 {a}")
