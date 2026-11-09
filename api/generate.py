"""Vercel Python 函数：POST /api/generate  body = case JSON → 三条候选。"""
import json
import sys
import traceback
from http.server import BaseHTTPRequestHandler
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from xuyu.pipeline import run  # noqa: E402
from xuyu.schemas import SCENARIOS  # noqa: E402


class handler(BaseHTTPRequestHandler):
    def _send(self, code, obj):
        body = json.dumps(obj, ensure_ascii=False, default=str).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        try:
            n = int(self.headers.get("Content-Length") or 0)
            case = json.loads(self.rfile.read(n) or b"{}")
            if case.get("scenario") not in SCENARIOS:
                return self._send(400, {"error": "scenario 必须是六个场景之一"})
            if not case.get("client_id") and not case.get("client"):
                return self._send(400, {"error": "需要 client_id 或手填 client"})
            case.setdefault("case_id", "web")
            r = run(case, save=False)
            r.pop("input", None)
            self._send(200, r)
        except Exception as e:
            traceback.print_exc()
            self._send(500, {"error": f"{type(e).__name__}: {str(e)[:300]}"})
