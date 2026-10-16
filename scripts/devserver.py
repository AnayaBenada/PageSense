"""Dev helper server (port 8765). Not part of the shipped extension.

1. POST /snapshot  {name, url, title, text}  → backend/eval/pages/<name>.json
   Lets a browser run extension/extractor.js on a live page and save exactly
   what the extension would send, so eval datasets are reproducible.
2. GET  /harness?page=<name>  → extension/sidepanel.html with a chrome.* stub,
   so the side-panel UI can be exercised against the real backend in a normal tab.
3. Static files from the repo root.

Run: python scripts/devserver.py
"""
import json
import re
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).resolve().parent.parent
PAGES = ROOT / "backend" / "eval" / "pages"
PORT = 8765


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(ROOT), **kwargs)

    def end_headers(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Allow-Private-Network", "true")
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def do_OPTIONS(self):
        self.send_response(204)
        self.end_headers()

    def do_POST(self):
        if urlparse(self.path).path != "/snapshot":
            return self.send_error(404)
        raw = self.rfile.read(int(self.headers["Content-Length"])).decode()
        if self.headers.get("Content-Type", "").startswith("application/x-www-form-urlencoded"):
            # Form-POST navigation: Chrome blocks fetch() from public sites to localhost.
            raw = parse_qs(raw)["payload"][0]
        body = json.loads(raw)
        name = re.sub(r"[^a-z0-9_-]", "", body.get("name", "").lower())
        if not name or not body.get("text"):
            return self.send_error(400, "name and text required")
        PAGES.mkdir(parents=True, exist_ok=True)
        snap = {k: body[k] for k in ("url", "title", "text")}
        (PAGES / f"{name}.json").write_text(json.dumps(snap, indent=1, ensure_ascii=False))
        self._json({"saved": f"backend/eval/pages/{name}.json", "chars": len(snap["text"])})

    def do_GET(self):
        url = urlparse(self.path)
        if url.path == "/harness":
            page = parse_qs(url.query).get("page", [""])[0]
            html = (ROOT / "extension" / "sidepanel.html").read_text()
            html = html.replace('href="', 'href="/extension/').replace('src="', 'src="/extension/')
            stub = f'<script>window.HARNESS_PAGE = {json.dumps(page)};</script><script src="/scripts/chrome_stub.js"></script>'
            html = html.replace('<script src="/extension/markdown.js">', stub + '<script src="/extension/markdown.js">')
            data = html.encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return
        super().do_GET()

    def _json(self, obj):
        data = json.dumps(obj).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


if __name__ == "__main__":
    print(f"devserver on http://localhost:{PORT}")
    ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
