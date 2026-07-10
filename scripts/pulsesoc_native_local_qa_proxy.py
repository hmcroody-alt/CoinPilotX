#!/usr/bin/env python3
"""Local-only CORS/session proxy for PulseSoc native web QA.

This proxy is intentionally separate from production Flask routes. It forwards
local Expo web requests to a local PulseSoc backend while preserving cookies for
browser-based authenticated QA.
"""

from __future__ import annotations

import argparse
import http.client
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit


HOP_BY_HOP_HEADERS = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailers",
    "transfer-encoding",
    "upgrade",
}


class QaProxyHandler(BaseHTTPRequestHandler):
    backend_url = "http://127.0.0.1:5107"
    allowed_origin = "http://127.0.0.1:8094"

    def do_OPTIONS(self) -> None:  # noqa: N802
        self.send_response(204)
        self._send_cors_headers()
        self.end_headers()

    def do_GET(self) -> None:  # noqa: N802
        self._proxy()

    def do_HEAD(self) -> None:  # noqa: N802
        self._proxy(head_only=True)

    def do_POST(self) -> None:  # noqa: N802
        self._proxy()

    def do_PUT(self) -> None:  # noqa: N802
        self._proxy()

    def do_PATCH(self) -> None:  # noqa: N802
        self._proxy()

    def do_DELETE(self) -> None:  # noqa: N802
        self._proxy()

    def log_message(self, fmt: str, *args: object) -> None:
        print(f"[pulsesoc-native-qa-proxy] {self.address_string()} - {fmt % args}", flush=True)

    def _proxy(self, head_only: bool = False) -> None:
        backend = urlsplit(self.backend_url)
        connection_cls = http.client.HTTPSConnection if backend.scheme == "https" else http.client.HTTPConnection
        port = backend.port or (443 if backend.scheme == "https" else 80)
        path = self.path or "/"
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length) if length else None

        headers = {
            key: value
            for key, value in self.headers.items()
            if key.lower() not in HOP_BY_HOP_HEADERS and key.lower() != "host"
        }
        headers["Host"] = backend.netloc
        headers["X-Forwarded-Host"] = self.headers.get("Host", "")
        headers["X-Forwarded-Proto"] = "http"

        conn = connection_cls(backend.hostname or "127.0.0.1", port, timeout=30)
        try:
            conn.request(self.command, path, body=body, headers=headers)
            response = conn.getresponse()
            payload = response.read()
            self.send_response(response.status, response.reason)
            self._send_cors_headers()
            for key, value in response.getheaders():
                lower = key.lower()
                if lower in HOP_BY_HOP_HEADERS:
                    continue
                if lower in {"access-control-allow-origin", "access-control-allow-credentials"}:
                    continue
                self.send_header(key, value)
            self.end_headers()
            if not head_only:
                self.wfile.write(payload)
        except Exception as exc:  # pragma: no cover - runtime-only guard
            self.send_response(502)
            self._send_cors_headers()
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            message = str(exc).replace('"', "'")
            self.wfile.write(f'{{"ok":false,"error":"qa_proxy_failed","message":"{message}"}}'.encode("utf-8"))
        finally:
            conn.close()

    def _send_cors_headers(self) -> None:
        origin = self.headers.get("Origin") or self.allowed_origin
        if origin in {self.allowed_origin, "http://localhost:8094"}:
            self.send_header("Access-Control-Allow-Origin", origin)
        else:
            self.send_header("Access-Control-Allow-Origin", self.allowed_origin)
        self.send_header("Access-Control-Allow-Credentials", "true")
        self.send_header("Access-Control-Allow-Methods", "GET,POST,PUT,PATCH,DELETE,OPTIONS")
        self.send_header(
            "Access-Control-Allow-Headers",
            "Content-Type, Authorization, X-Requested-With, X-PulseSoc-QA",
        )


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the PulseSoc native local QA proxy.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=5108)
    parser.add_argument("--backend", default="http://127.0.0.1:5107")
    parser.add_argument("--origin", default="http://127.0.0.1:8094")
    args = parser.parse_args()

    QaProxyHandler.backend_url = args.backend.rstrip("/")
    QaProxyHandler.allowed_origin = args.origin.rstrip("/")

    server = ThreadingHTTPServer((args.host, args.port), QaProxyHandler)
    print(
        f"PulseSoc native local QA proxy listening on http://{args.host}:{args.port} -> {QaProxyHandler.backend_url}",
        flush=True,
    )
    server.serve_forever()


if __name__ == "__main__":
    main()
