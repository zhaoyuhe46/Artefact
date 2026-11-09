"""命令行：.venv/bin/python cli.py --client C02 --scenario price_objection [--msg "客户最新消息"] [--product XY-B02]
或：.venv/bin/python cli.py --case path/to/case.json"""
import argparse
import json
import sys

from xuyu.pipeline import run
from xuyu.schemas import SCENARIOS


def show(r):
    print(f"\n== {r['case_id']} | {SCENARIOS.get(r['scenario'])} | 模式 {r['mode']} | {r['status']} | "
          f"{r['meta']['seconds']}s | {r['meta']['version']}-{r['meta']['hash']}")
    if r.get("retrieval"):
        rt = r["retrieval"]
        print(f"检索：焦点 {rt.get('focus_sku')}；事实 {[x['ref'] for x in rt.get('facts', [])]}；"
              f"品牌片段 {[(x['ref'], x['score']) for x in rt.get('rag', [])]}")
    for c in r["candidates"]:
        flag = "✓" if c["passed"] else "✗"
        print(f"\n[{c['strategy_id']} {c['strategy_name']}] {flag}{' (重写过)' if c['repaired'] else ''}\n{c['text']}\n  理由：{c['rationale']}  refs={c['refs']}")
        bad = [f"{k['rule']} {k['detail']}" for k in c["checks"] if not k["ok"]]
        if bad:
            print("  未通过：" + "；".join(bad))
    if r["ca_note"]:
        print("\n给 CA 的提示：\n- " + "\n- ".join(r["ca_note"]))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--case")
    ap.add_argument("--client")
    ap.add_argument("--scenario", choices=list(SCENARIOS))
    ap.add_argument("--today", default="2026-11-09")
    ap.add_argument("--msg", help="客户最新一条消息（追加到对话历史）")
    ap.add_argument("--product", action="append")
    ap.add_argument("--context", help="JSON，如 '{\"event_id\":\"EVT:CARE-1114\"}'")
    a = ap.parse_args()
    if a.case:
        case = json.load(open(a.case, encoding="utf-8"))
    elif a.client and a.scenario:
        case = {"case_id": f"{a.client}-{a.scenario}", "client_id": a.client, "scenario": a.scenario, "today": a.today,
                "latest_message": a.msg, "products": a.product or [],
                "scenario_context": json.loads(a.context) if a.context else {}}
    else:
        sys.exit("需要 --case，或 --client 加 --scenario")
    show(run(case))
