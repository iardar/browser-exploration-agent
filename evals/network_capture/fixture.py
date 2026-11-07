"""Serve deterministic local pages; starting this server is an explicit operator action."""

import argparse
import hashlib
import html
import json
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

from .dataset import ROOT, load_case, load_dataset, load_requests


def fixture_fingerprint() -> str:
    """Identify the served fixture implementation and oracle without changing case inputs."""
    digest = hashlib.sha256()
    for name in ("fixture.py", "dataset.py", "probe.js", "cases.json", "requests.csv"):
        digest.update(name.encode() + b"\0" + (ROOT / name).read_bytes())
    return digest.hexdigest()


# Identify the implementation loaded by this server, not edited files it has not reloaded.
_SERVING_FINGERPRINT = fixture_fingerprint()


def fixture_response(path: str) -> tuple[int, str, bytes]:
    """Build a response without sockets, so fixture contents can be verified offline."""
    if path == "/favicon.ico":
        return 204, "image/x-icon", b""
    probe = re.fullmatch(r"/__eval__/(seed|check|traffic)/([a-f0-9]{32})", path)
    if probe:
        mode, token = probe.groups()
        if mode == "traffic":
            return 200, "text/plain", token.encode()
        settings = json.dumps({"mode": mode, "token": token, "fingerprint": _SERVING_FINGERPRINT})
        body = (
            '<!doctype html><meta charset="utf-8"><h1 id="status">Isolation pending</h1>'
            f"<script>window.probe={settings};{(ROOT / 'probe.js').read_text()}</script>"
        )
        return 200, "text/html; charset=utf-8", body.encode()
    for case in load_dataset()["cases"]:
        requests = load_requests(case["id"])
        if path == case["path"]:
            # Keep the oracle's healthy/failing labels out of the agent's page context.
            title = html.escape(f"Network capture fixture {case['id']}")
            first = html.escape(requests[1].path, quote=True)
            body = (
                '<!doctype html><html lang="en"><head><meta charset="utf-8">'
                f"<title>{title}</title></head><body><h1>{title}</h1>"
                "<p>Controlled network-capture fixture. No external resources or controls.</p>"
                f'<script src="{first}"></script>'
                '<h2 id="ready">Network fixture ready</h2></body></html>'
            )
            return 200, "text/html; charset=utf-8", body.encode()
        for index, request in enumerate(requests[1:], 1):
            if path != request.path:
                continue
            if request.status != 200:
                return request.status, "text/javascript; charset=utf-8", b"// Seeded HTTP error.\n"
            following = requests[index + 1 :]
            if following and following[0].status == 200:
                following = following[:1]
            # Parser-inserted, same-origin scripts form a chain. Later URLs are
            # discovered only after earlier scripts load; failures stay at the tail.
            tags = "".join(f'<script src="{item.path}"></script>' for item in following)
            body = f"document.write({json.dumps(tags)});\n" if tags else "// Last request.\n"
            return 200, "text/javascript; charset=utf-8", body.encode()
    return 404, "text/plain; charset=utf-8", b"Unknown fixture route.\n"


class FixtureHandler(BaseHTTPRequestHandler):
    """Serve only the dataset routes and disable cache reuse between trials."""

    def do_GET(self) -> None:
        """Respond to one GET; the fixture has no mutation endpoints."""
        status, content_type, body = fixture_response(urlsplit(self.path).path)
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:
        """Keep request logging out of the fixture console."""


def main() -> None:
    """Run the fixture alongside an independently configured, restricted MCP browser."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bind", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8766)
    args = parser.parse_args()
    print(f"Network fixture listening on {args.bind}:{args.port}", flush=True)
    for case in load_dataset()["cases"]:
        print(f"  {case['id']}: {load_case(case['id'])['path']}", flush=True)
    with ThreadingHTTPServer((args.bind, args.port), FixtureHandler) as server:
        server.serve_forever()


if __name__ == "__main__":
    main()
