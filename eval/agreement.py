"""人工盲评 vs LLM judge 的一致率。
.venv/bin/python eval/agreement.py v1   读取 deliverables/Blind_Review_v1.xlsx（G 列已填 good / acceptable / unacceptable）"""
import json
import sys
from collections import Counter
from pathlib import Path

from openpyxl import load_workbook

ROOT = Path(__file__).resolve().parent.parent
DIMS = ["brand_voice", "personalization", "appropriateness", "factuality", "sendable"]


def judge_label(j, passed):
    if j.get("veto"):
        return "unacceptable"
    if passed and all(j.get(k, 0) >= 4 for k in DIMS):
        return "good"
    return "acceptable"


def main(v):
    key = {k["review_id"]: k for k in json.loads((ROOT / "deliverables" / f"Blind_Review_{v}_key.json").read_text(encoding="utf-8"))}
    ws = load_workbook(ROOT / "deliverables" / f"Blind_Review_{v}.xlsx").active
    pairs = []
    for row in ws.iter_rows(min_row=2, values_only=True):
        rid, human = row[0], (row[6] or "").strip().lower()
        if not rid or human not in ("good", "acceptable", "unacceptable"):
            continue
        k = key[rid]
        r = json.loads((ROOT / "runs" / v / f"{k['case_id']}.json").read_text(encoding="utf-8"))
        jd = json.loads((ROOT / "runs" / v / "judge" / f"{k['case_id']}.json").read_text(encoding="utf-8"))
        c = next(x for x in r["candidates"] if x["strategy_id"] == k["strategy_id"])
        j = next(x for x in jd["candidates"] if x["strategy_id"] == k["strategy_id"])
        pairs.append((rid, k["case_id"], human, judge_label(j, c["passed"]), (row[7] or "").strip(), j.get("comment", "")))
    n = len(pairs)
    exact = sum(h == j for _, _, h, j, *_ in pairs)
    bin_ = sum((h == "unacceptable") == (j == "unacceptable") for _, _, h, j, *_ in pairs)
    print(f"已评 {n} 条；三档完全一致 {exact}/{n} = {exact / max(n, 1):.0%}；'是否不合格'一致 {bin_}/{n} = {bin_ / max(n, 1):.0%}")
    print("人工分布", Counter(p[2] for p in pairs), "judge 分布", Counter(p[3] for p in pairs))
    for rid, cid, h, j, why, jc in pairs:
        if h != j:
            print(f"- {rid} {cid}: 人工 {h} / judge {j}｜人工：{why}｜judge：{jc[:80]}")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "v1")
