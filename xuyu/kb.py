"""知识访问层：brand schema（结构化事实 + 向量检索）与 crm schema（客户画像）。
硬事实（价格、产品事实、服务条件、活动时间）按 SKU / ID 精确查询；软知识（语气、范例、禁区、养护、政策）走 pgvector 语义检索。
每条返回的资料都带引用编号，生成时要求模型在 refs 中注明用了哪些。"""
import json
import re
from datetime import date, datetime

import psycopg
from pgvector.psycopg import register_vector
from psycopg.rows import dict_row

from . import config, llm


def short_name(name: str) -> str:
    m = re.search(r"「(.+?)」", name)
    return m.group(1) if m else name


def _d(s) -> date:
    return s if isinstance(s, date) and not isinstance(s, datetime) else date.fromisoformat(str(s)[:10])


def _iso(v):
    if isinstance(v, datetime):
        return v.strftime("%Y-%m-%d %H:%M")
    if isinstance(v, date):
        return v.isoformat()
    return v


def _clean(row: dict) -> dict:
    return {k: _iso(v) for k, v in row.items()}


class KB:
    def __init__(self, url: str = None):
        if not (url or config.DATABASE_URL):
            raise RuntimeError("缺少 DATABASE_URL（请在 .env 中填写 Neon 连接串）")
        self.url = url or config.DATABASE_URL
        self._connect()
        self.refresh()

    def _connect(self):
        self.con = psycopg.connect(self.url, autocommit=True, row_factory=dict_row)
        register_vector(self.con)

    def refresh(self):
        """重新读取品牌产品与规则。设置页修改后、每次生成前调用，保证读到数据库里的最新值。"""
        self._products = [self._prod(r) for r in self._rows("SELECT * FROM brand.products ORDER BY price")]
        self._rules = {r["key"]: r["value"] for r in self._rows("SELECT key, value FROM brand.rules")}

    def execute(self, sql, args=()):
        try:
            return self.con.execute(sql, args)
        except psycopg.OperationalError:      # serverless 空闲后连接可能被断开，重连一次
            self._connect()
            return self.con.execute(sql, args)

    def _rows(self, sql, args=()):
        return [_clean(r) for r in self.execute(sql, args).fetchall()]

    @staticmethod
    def _prod(r):
        r["facts"] = r["facts"] if isinstance(r["facts"], list) else json.loads(r["facts"])
        r["short"] = short_name(r["name"])
        return r

    # ---------- brand：结构化 ----------
    def brand_rules(self) -> dict:
        return self._rules

    def products_all(self):
        return self._products

    def product(self, sku):
        return next((p for p in self._products if p["sku"] == sku), None)

    def event(self, eid):
        rows = self._rows("SELECT * FROM brand.events WHERE id=%s", (eid,))
        return rows[0] if rows else None

    def events_upcoming(self, today):
        return self._rows("SELECT * FROM brand.events WHERE status='confirmed' AND date >= %s ORDER BY date", (today,))

    # ---------- brand：向量检索 ----------
    def rag(self, query: str, tags, k=config.RAG_K):
        vec = llm.embed(query)[0]
        import numpy as np
        rows = self._rows(
            """SELECT id, chunk_type, title, text, 1 - (embedding <=> %s) AS score
               FROM brand.chunks WHERE scenario_tags && %s
               ORDER BY embedding <=> %s LIMIT %s""",
            (np.array(vec), list(tags) + ["all"], np.array(vec), k))
        return [{"ref": r["id"], "type": r["chunk_type"], "score": round(float(r["score"]), 3),
                 "text": f"{r['title']}：{r['text']}"} for r in rows]

    # ---------- crm ----------
    def list_clients(self):
        return self._rows("SELECT id, address, relationship FROM crm.clients ORDER BY id")

    def client(self, cid):
        rows = self._rows("SELECT * FROM crm.clients WHERE id=%s", (cid,))
        if not rows:
            raise KeyError(f"客户 {cid} 不在库里")
        c = rows[0]
        c["purchases"] = self._rows("SELECT date, sku, color, note FROM crm.purchases WHERE client_id=%s ORDER BY date", (cid,))
        c["key_dates"] = self._rows("SELECT type, date FROM crm.key_dates WHERE client_id=%s", (cid,))
        c["sensitivities"] = self._rows("SELECT type, date, note FROM crm.sensitivities WHERE client_id=%s", (cid,))
        c["history"] = self._rows("SELECT time, role, text FROM crm.messages WHERE client_id=%s ORDER BY time", (cid,))
        if c.get("no_marketing"):
            c["contact_prefs"]["no_marketing"] = True
        c["ref"] = f"C:{cid}"
        return c

    # ---------- 合并输入 ----------
    def load_case(self, case: dict) -> dict:
        """库里带出客户资料，手填字段覆盖同名字段；latest_message 追加为客户最新一条消息。"""
        client = self.client(case["client_id"]) if case.get("client_id") else {}
        for k, v in (case.get("client") or {}).items():
            if v not in (None, "", [], {}):
                client[k] = v
        client.setdefault("ref", "INPUT:client")
        for k, default in [("address", "您"), ("relationship", "未知"), ("preferences", {}), ("contact_prefs", {}),
                           ("purchases", []), ("key_dates", []), ("sensitivities", []), ("notes", ""), ("budget", None)]:
            if client.get(k) is None:
                client[k] = default
        history = list(case.get("history") or client.get("history") or [])
        client.pop("history", None)
        today = case.get("today") or date.today().isoformat()
        if case.get("latest_message"):
            history.append({"time": f"{today} 12:00", "role": "client", "text": case["latest_message"]})
        return {
            "case_id": case.get("case_id", "adhoc"),
            "today": today,
            "advisor_name": case.get("advisor_name") or self._rules.get("advisor", {}).get("default_name", "安然"),
            "scenario": case["scenario"],
            "scenario_context": case.get("scenario_context") or {},
            "client": client,
            "history": history,
            "products": case.get("products") or [],
        }

    # ---------- 焦点产品 ----------
    def focus_sku(self, ctx):
        if ctx["products"]:
            return ctx["products"][0]
        shorts = {p["short"]: p["sku"] for p in self._products}
        for h in reversed(ctx["history"]):
            for name in re.findall(r"「(.+?)」", h["text"]):
                if name in shorts:
                    return shorts[name]
        if ctx["scenario"] == "post_purchase" and ctx["client"].get("purchases"):
            return ctx["client"]["purchases"][-1]["sku"]
        return None

    # ---------- 硬事实资料包 ----------
    def _uses(self, ctx):
        return (ctx["client"].get("preferences") or {}).get("uses", []) or []

    @staticmethod
    def _match_use(p, uses):
        return any(u in pu or pu in u for u in uses for pu in p["uses"])

    @staticmethod
    def _prod_rec(p, extra=""):
        return {"ref": f"P:{p['sku']}", "text": f"{p['name']}（{p['sku']}，{p['category']}）：统一售价{p['price']:,}元；"
                                                 f"{'，'.join(p['facts'])}；主要用途：{'、'.join(p['uses'])}{extra}"}

    @staticmethod
    def event_rec(r):
        f = lambda v: v if v else "TBD"
        when = f"{f(r.get('date'))}{('（' + r['weekday'] + '）') if r.get('weekday') else ''} {f(r.get('time'))}"
        return {"ref": r["id"], "text": f"活动：{f(r.get('name'))}；时间：{when}；地点：{f(r.get('location'))}；内容：{f(r.get('content'))}"}

    def event_target(self, ctx):
        sc = ctx["scenario_context"]
        if isinstance(sc.get("event"), dict):
            ev = {"id": "INPUT:event", "name": None, "date": None, "weekday": None, "time": None, "location": None, "content": None}
            ev.update(sc["event"])
            return ev
        if sc.get("event_id"):
            return self.event(sc["event_id"])
        ups = self.events_upcoming(ctx["today"])
        return ups[0] if ups else None

    @staticmethod
    def missing_event_fields(ev):
        if not ev:
            return ["内容", "时间", "地点"]
        miss = []
        if not (ev.get("content") or ev.get("name")):
            miss.append("内容")
        if not ev.get("date") or not ev.get("time"):
            miss.append("时间")
        if not ev.get("location"):
            miss.append("地点")
        return miss

    def pack(self, name, ctx, focus=None):
        c = ctx["client"]
        today = _d(ctx["today"])
        if name == "focus_product":
            p = self.product(focus) if focus else None
            return [self._prod_rec(p)] if p else []
        if name == "products_by_use":
            bought = {x["sku"] for x in c.get("purchases", [])}
            ps = [p for p in self._products if self._match_use(p, self._uses(ctx)) and p["sku"] not in bought]
            return [self._prod_rec(p) for p in ps[:3]]
        if name == "purchased_products":
            out = []
            for x in c.get("purchases", [])[-3:]:
                p = self.product(x["sku"])
                if p:
                    out.append(self._prod_rec(p, f"；客户于{x['date']}购买，颜色{x.get('color') or '未记录'}"))
            return out
        if name == "care_for_purchased":
            mats = {self.product(x["sku"])["material"] for x in c.get("purchases", [])[-1:] if self.product(x["sku"])}
            rows = self._rows("SELECT * FROM brand.care WHERE material = ANY(%s) OR material='通用'", (list(mats),))
            return [{"ref": r["id"], "text": f"{r['material']}·{r['topic']}：{r['advice']}"} for r in rows]
        if name == "services":
            # v2：免费清洁是否适用，按每件已购产品的购买日期算好再交给模型，不让模型自己推算
            out, eligible = [], False
            for x in c.get("purchases", []):
                days = (today - _d(x["date"])).days
                p = self.product(x["sku"])
                if not p:
                    continue
                if 0 <= days <= 365:
                    eligible = True
                    out.append({"ref": f"DER:CLEAN-{x['sku']}", "text": f"客户于{x['date']}购买{p['name']}，目前仍在购买后12个月内，可预约一次免费基础清洁"})
                else:
                    out.append({"ref": f"DER:CLEAN-EXPIRED-{x['sku']}", "text": f"客户于{x['date']}购买{p['name']}，已超过12个月，免费基础清洁不适用，不要提免费清洁"})
            for r in self._rows("SELECT * FROM brand.services"):
                if r["id"] == "SVC:CLEAN-12M" and not eligible:
                    continue
                out.append({"ref": r["id"], "text": f"{r['name']}：{r['detail']}"})
            return out
        if name == "policies_price":
            rows = self._rows("SELECT * FROM brand.policies WHERE id = ANY(%s)", (["POL:PRICE-UNIFIED", "POL:PRICE-OPEN", "POL:FIT-USE"],))
            return [{"ref": r["id"], "text": f"{r['title']}：{r['text']}"} for r in rows]
        if name == "budget_alternatives":
            budget = c.get("budget")
            if not budget:
                return [{"ref": "DER:NO-BUDGET", "text": "客户尚未说明具体预算；如需按预算推荐，先征得同意并询问预算"}]
            ps = [p for p in self._products if p["price"] <= int(budget) and self._match_use(p, self._uses(ctx)) and p["sku"] != focus]
            if not ps:
                return [{"ref": "DER:NO-BUDGET-MATCH", "text": f"在{int(budget):,}元预算内，暂时没有符合客户用途（{'、'.join(self._uses(ctx)) or '未说明'}）的款式；不能用不符合用途的低价产品替代"}]
            return [self._prod_rec(p) for p in ps]
        if name == "events_upcoming":
            return [self.event_rec(r) for r in self.events_upcoming(ctx["today"])[:2]]
        if name == "event_target":
            ev = self.event_target(ctx)
            if not ev:
                return [{"ref": "DER:NO-EVENT", "text": "输入中没有指定活动，活动内容、时间、地点均为 TBD"}]
            # v2：活动内容里提到的产品一并带上，话术提到它们时有据可查
            shorts = {p["short"]: p for p in self._products}
            prods = [self._prod_rec(shorts[n]) for n in re.findall(r"「(.+?)」", ev.get("content") or "") if n in shorts]
            return [self.event_rec(ev)] + prods
        raise KeyError(f"未知资料包 {name}")
