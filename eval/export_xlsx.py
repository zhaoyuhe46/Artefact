"""导出交付物 C（50 条用例 × 3 条输出）与人工盲评表。
.venv/bin/python eval/export_xlsx.py v1            → deliverables/Test_Cases_v1.xlsx（Brief 规定的 8 列 + Version，另附规则检查和 judge 分数）
.venv/bin/python eval/export_xlsx.py v1 --blind    → deliverables/Blind_Review_v1.xlsx（每个场景 3 条，不含任何分数）"""
import json
import random
import sys
from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from xuyu.schemas import SCENARIOS  # noqa: E402

OUT = ROOT / "deliverables"


def client_info(r):
    c = r["input"]["client"]
    prefs = c.get("preferences") or {}
    parts = [f"{c.get('address')}（{c.get('relationship')}，{c.get('since') or '—'} 起）"]
    if prefs:
        parts.append("偏好：" + "；".join(f"{k}={('、'.join(v) if isinstance(v, list) else v)}" for k, v in prefs.items() if v))
    if c.get("budget"):
        parts.append(f"预算：{c['budget']}元以内")
    if c.get("purchases"):
        parts.append("已购：" + "；".join(f"{p['date']} {p['sku']} {p.get('color') or ''}" for p in c["purchases"]))
    if c.get("key_dates"):
        parts.append("关键日期：" + "；".join(f"{k['type']} {k['date']}" for k in c["key_dates"]))
    cp = c.get("contact_prefs") or {}
    if cp.get("no_marketing"):
        parts.append("已要求停止营销联系")
    if c.get("sensitivities"):
        parts.append("敏感事项：" + "；".join(f"{s['type']} {s.get('date')} {s.get('note') or ''}" for s in c["sensitivities"]))
    if c.get("notes"):
        parts.append(f"备注：{c['notes']}")
    parts.append(f"今天：{r['input']['today']}")
    sc = r["input"].get("scenario_context")
    if sc:
        parts.append(f"场景补充：{json.dumps(sc, ensure_ascii=False)}")
    return "\n".join(parts)


def history(r):
    h = r["input"]["history"][-8:]
    return "\n".join(f"{x['time']} {'客户' if x['role'] == 'client' else 'CA'}：{x['text']}" for x in h) or "（无）"


def product_info(r):
    facts = (r.get("retrieval") or {}).get("facts", [])
    return "\n".join(f"[{x['ref']}] {x['text']}" for x in facts if x["ref"].startswith(("P:", "EVT:", "INPUT:event", "SVC:", "DER:", "CARE:"))) or "（本场景不需要）"


def outputs(r):
    if r["status"] == "held":
        msg = "【不生成】" + "；".join(r.get("ca_note") or [])
        return [msg, "—", "—"]
    outs = [f"[{c['strategy_id']} {c['strategy_name']}] {c['text']}" for c in r["candidates"]]
    return (outs + ["—"] * 3)[:3]


def style(ws, widths):
    for i, w in enumerate(widths):
        ws.column_dimensions[chr(65 + i)].width = w
    for cell in ws[1]:
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = PatternFill("solid", fgColor="2F3E46")
    for row in ws.iter_rows(min_row=2):
        for cell in row:
            cell.alignment = Alignment(wrap_text=True, vertical="top")
    ws.freeze_panes = "B2"


def load(version):
    d = ROOT / "runs" / version
    runs = sorted((json.loads(f.read_text(encoding="utf-8")) for f in d.glob("TC-*.json")), key=lambda r: r["case_id"])
    judge = {f.stem: json.loads(f.read_text(encoding="utf-8")) for f in (d / "judge").glob("TC-*.json")} if (d / "judge").exists() else {}
    return runs, judge


def main(version, blind=False):
    OUT.mkdir(exist_ok=True)
    runs, judge = load(version)
    wb = Workbook()
    if blind:
        ws = wb.active
        ws.title = "Blind review"
        ws.append(["Review ID", "Scenario", "Client information", "Conversation history", "Product information", "Output",
                   "Verdict (good / acceptable / unacceptable)", "Reason"])
        random.seed(7)
        picked = []
        for s in SCENARIOS:
            pool = [r for r in runs if r["scenario"] == s and r["status"] == "generated"]
            picked += random.sample(pool, min(3, len(pool)))
        key = []
        for i, r in enumerate(picked, 1):
            c = random.choice(r["candidates"])
            rid = f"BR-{i:02d}"
            ws.append([rid, SCENARIOS[r["scenario"]], client_info(r), history(r), product_info(r), c["text"], "", ""])
            key.append({"review_id": rid, "case_id": r["case_id"], "strategy_id": c["strategy_id"]})
        style(ws, [10, 14, 40, 40, 40, 50, 18, 30])
        (OUT / f"Blind_Review_{version}_key.json").write_text(json.dumps(key, ensure_ascii=False, indent=1), encoding="utf-8")
        p = OUT / f"Blind_Review_{version}.xlsx"
    else:
        ws = wb.active
        ws.title = "Test cases"
        ws.append(["Case ID", "Scenario", "Client information", "Conversation history", "Product information",
                   "Output 1", "Output 2", "Output 3", "Version"])
        for r in runs:
            ws.append([r["case_id"], SCENARIOS[r["scenario"]], client_info(r), history(r), product_info(r), *outputs(r),
                       f"{r['meta']['version']}-{r['meta']['hash']}"])
        style(ws, [9, 14, 42, 42, 42, 40, 40, 40, 14])

        ws2 = wb.create_sheet("Checks & scores")
        ws2.append(["Case ID", "Edge case", "Mode", "Status", "Strategy", "Text", "Rule checks", "Repaired",
                    "品牌调性", "个性化", "得体与时机", "事实一致", "可直接发送", "Strategy match", "Veto", "Judge comment", "CA note"])
        for r in runs:
            j = {c["strategy_id"]: c for c in judge.get(r["case_id"], {}).get("candidates", [])}
            for c in r["candidates"] or [{"strategy_id": "—", "strategy_name": "", "text": "（不生成）", "checks": [], "passed": True, "repaired": False}]:
                s = j.get(c["strategy_id"], {})
                fails = "；".join(f"{k['rule']} {k['detail']}" for k in c["checks"] if not k["ok"]) or "全部通过"
                ws2.append([r["case_id"], r.get("edge") or "", r["mode"], r["status"], f"{c['strategy_id']} {c['strategy_name']}",
                            c["text"], fails, "是" if c.get("repaired") else "",
                            s.get("brand_voice"), s.get("personalization"), s.get("appropriateness"), s.get("factuality"),
                            s.get("sendable"), s.get("strategy_match"), s.get("veto"), s.get("comment") or s.get("veto_reason"),
                            "；".join(r.get("ca_note") or [])])
        style(ws2, [9, 18, 18, 10, 14, 50, 30, 8, 8, 8, 8, 8, 8, 8, 8, 40, 40])
        p = OUT / f"Test_Cases_{version}.xlsx"
    wb.save(p)
    print("saved", p)


if __name__ == "__main__":
    main(sys.argv[1], blind="--blind" in sys.argv)
