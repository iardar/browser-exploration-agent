"""Serve deterministic local pages; starting this server is an explicit operator action."""

import argparse
import html
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

from .dataset import load_case, load_dataset, load_requests


def fixture_response(path: str) -> tuple[int, str, bytes]:
    """Build a response without sockets, so fixture contents can be verified offline."""
    if path == "/favicon.ico":
        return 204, "image/x-icon", b""
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
