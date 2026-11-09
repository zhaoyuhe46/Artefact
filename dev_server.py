"""本地预览：.venv/bin/python dev_server.py → http://localhost:8000
静态页来自 public/，/api/meta 与 /api/generate 复用 Vercel 的同名 handler。"""
import importlib.util
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def _load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "api" / f"{name}.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m.handler


API = {"/api/meta": _load("meta"), "/api/generate": _load("generate"), "/api/admin": _load("admin")}


class Dev(SimpleHTTPRequestHandler):
    _send = API["/api/generate"]._send

    def __init__(self, *a, **k):
        super().__init__(*a, directory=str(ROOT / "public"), **k)

    def _api(self, method):
        h = API.get(self.path.split("?")[0])
        if not h:
            return False
        getattr(h, method)(self)
        return True

    def do_GET(self):
        if not self._api("do_GET"):
            super().do_GET()

    def do_POST(self):
        if not self._api("do_POST"):
            self.send_error(404)


if __name__ == "__main__":
    print("http://localhost:8000")
    ThreadingHTTPServer(("127.0.0.1", 8000), Dev).serve_forever()
