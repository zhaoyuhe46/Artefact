"""Vercel Python 函数：POST /api/admin  {action: update | replace_list | message | reset, ...}，写入 Neon 后返回。"""
import json
import sys
import traceback
from http.server import BaseHTTPRequestHandler
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from xuyu import admin  # noqa: E402
from xuyu.pipeline import kb  # noqa: E402


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
            self._send(200, admin.handle(kb(), json.loads(self.rfile.read(n) or b"{}")))
        except (KeyError, ValueError) as e:
            self._send(400, {"error": str(e)})
        except Exception as e:
            traceback.print_exc()
            self._send(500, {"error": f"{type(e).__name__}: {str(e)[:300]}"})
