"""批量跑用例：.venv/bin/python eval/run_batch.py v1 [--only TC-01,TC-02] [--workers 4]
结果写到 runs/<version>/<case_id>.json。"""
import argparse
import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("version")
    ap.add_argument("--only")
    ap.add_argument("--workers", type=int, default=4)
    a = ap.parse_args()
    os.environ["ENGINE_VERSION"] = a.version
    from xuyu.pipeline import kb, run  # 读取环境变量后再导入

    cases = yaml.safe_load((ROOT / "eval" / "cases.yaml").read_text(encoding="utf-8"))["cases"]
    if a.only:
        keep = set(a.only.split(","))
        cases = [c for c in cases if c["case_id"] in keep]
    out = ROOT / "runs" / a.version
    out.mkdir(parents=True, exist_ok=True)
    kb()

    def one(case):
        t0 = time.time()
        try:
            r = run({k: v for k, v in case.items() if k not in ("expect", "edge")}, save=False)
            r["expect"], r["edge"] = case.get("expect"), case.get("edge")
            (out / f"{case['case_id']}.json").write_text(json.dumps(r, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
            bad = sum(not c["passed"] for c in r["candidates"])
            return f"{case['case_id']} {r['status']:9s} {r['mode']:22s} 未通过 {bad} 条  {time.time() - t0:.0f}s"
        except Exception as e:  # 单条失败不影响整批
            return f"{case['case_id']} ERROR {type(e).__name__}: {str(e)[:200]}"

    with ThreadPoolExecutor(a.workers) as ex:
        for line in ex.map(one, cases):
            print(line, flush=True)


if __name__ == "__main__":
    main()
