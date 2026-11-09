"""Vercel Python 函数：GET /api/meta → 客户（含画像与对话历史）、产品、活动，供网页下拉框使用。"""
import json
import sys
from http.server import BaseHTTPRequestHandler
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from xuyu import admin  # noqa: E402
from xuyu.pipeline import kb  # noqa: E402


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        try:
            body = json.dumps(admin.snapshot(kb()), ensure_ascii=False, default=str).encode()
            code = 200
        except Exception as e:
            body = json.dumps({"error": f"{type(e).__name__}: {str(e)[:300]}"}, ensure_ascii=False).encode()
            code = 500
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)
