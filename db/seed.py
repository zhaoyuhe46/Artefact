"""data/brand/*.yaml → brand schema；data/clients/clients.yaml → crm schema。可重复执行（重建 schema）。
用法：.venv/bin/python db/seed.py"""
import json
import sys
from pathlib import Path

import psycopg
import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from xuyu import config  # noqa: E402

B, C = ROOT / "data" / "brand", ROOT / "data" / "clients"
J = lambda v: json.dumps(v, ensure_ascii=False)


def y(p):
    return yaml.safe_load(p.read_text(encoding="utf-8"))


def main():
    with psycopg.connect(config.DATABASE_URL, autocommit=True) as con:
        con.execute((ROOT / "db" / "schema.sql").read_text(encoding="utf-8"))
        cur = con.cursor()
        for p in y(B / "products.yaml")["products"]:
            cur.execute("INSERT INTO brand.products VALUES (%s,%s,%s,%s,%s,%s,%s)",
                        (p["sku"], p["name"], p["category"], p["price"], p["material"], J(p["facts"]), p["uses"]))
        for s in y(B / "services.yaml")["services"]:
            cur.execute("INSERT INTO brand.services VALUES (%s,%s,%s,%s)", (s["id"], s["name"], s["detail"], s.get("condition_months")))
        for p in y(B / "policies.yaml")["policies"]:
            cur.execute("INSERT INTO brand.policies VALUES (%s,%s,%s)", (p["id"], p["title"], p["text"]))
        for x in y(B / "care.yaml")["care"]:
            cur.execute("INSERT INTO brand.care VALUES (%s,%s,%s,%s)", (x["id"], x["material"], x["topic"], x["advice"]))
        for e in y(B / "events.yaml")["events"]:
            cur.execute("INSERT INTO brand.events VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
                        (e["id"], e["name"], e["date"], e["weekday"], e["time"], e["location"], e["content"], e["status"]))
        for k, v in y(B / "brand.yaml").items():
            cur.execute("INSERT INTO brand.rules VALUES (%s,%s)", (k, J(v)))

        for cl in y(C / "clients.yaml")["clients"]:
            cp = cl.get("contact_prefs") or {}
            cur.execute("INSERT INTO crm.clients VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                        (cl["id"], cl["address"], cl.get("relationship"), cl.get("since"), J(cl.get("preferences") or {}),
                         cl.get("budget"), J(cp), bool(cp.get("no_marketing")), cl.get("notes")))
            for p in cl.get("purchases", []):
                cur.execute("INSERT INTO crm.purchases VALUES (%s,%s,%s,%s,%s)", (cl["id"], p["date"], p["sku"], p.get("color"), p.get("note")))
            for k in cl.get("key_dates", []):
                cur.execute("INSERT INTO crm.key_dates VALUES (%s,%s,%s)", (cl["id"], k["type"], k["date"]))
            for s in cl.get("sensitivities", []):
                cur.execute("INSERT INTO crm.sensitivities VALUES (%s,%s,%s,%s)", (cl["id"], s["type"], s.get("date"), s.get("note")))
            for h in cl.get("history", []):
                cur.execute("INSERT INTO crm.messages VALUES (%s,%s,%s,%s)", (cl["id"], h["time"], h["role"], h["text"]))

        for t in ["brand.products", "brand.services", "brand.policies", "brand.care", "brand.events", "brand.rules",
                  "crm.clients", "crm.purchases", "crm.key_dates", "crm.sensitivities", "crm.messages"]:
            print(f"{t:22s}", con.execute(f"SELECT count(*) FROM {t}").fetchone()[0])
    # schema 重建会清空 brand.chunks，紧接着重建向量索引
    sys.path.insert(0, str(ROOT / "db"))
    import index_brand
    index_brand.main()


if __name__ == "__main__":
    main()
