#!/usr/bin/env python3
"""Serve the HNIDS dashboard and proxy API calls to the backend.

This avoids modifying the friend's FastAPI code — all CORS is handled here.

Usage:
    python serve_dashboard.py [--port 3000] [--api http://127.0.0.1:8000]
"""
from __future__ import annotations

import argparse
import http.server
import json
import socketserver
import urllib.request
import urllib.error
from pathlib import Path

FRONTEND_DIR = Path(__file__).parent / "frontend"
EXAMPLES_DIR = Path(__file__).parent / "examples"
API_BASE = "http://127.0.0.1:8000"


class DashboardHandler(http.server.SimpleHTTPRequestHandler):
    """Serves frontend static files and proxies /api/* to the FastAPI backend."""

    def __init__(self, *args: object, **kwargs: object) -> None:
        super().__init__(*args, directory=str(FRONTEND_DIR), **kwargs)  # type: ignore[arg-type]

    def do_OPTIONS(self) -> None:
        """Handle CORS preflight."""
        self.send_response(204)
        self._cors_headers()
        self.end_headers()

    def do_GET(self) -> None:
        if self.path.startswith("/api/"):
            self._proxy("GET")
        elif self.path in ("/docs", "/openapi.json", "/redoc") or self.path.startswith("/docs"):
            self._proxy("GET", override_path=self.path)
        elif self.path.startswith("/examples/"):
            self._serve_example()
        else:
            super().do_GET()

    def do_POST(self) -> None:
        if self.path.startswith("/api/"):
            self._proxy("POST")
        else:
            self.send_error(405)

    def _proxy(self, method: str, override_path: str | None = None) -> None:
        """Proxy /api/* to the backend, stripping the /api prefix, or direct paths like /docs."""
        backend_path = override_path if override_path is not None else self.path[4:]  # Remove /api prefix
        url = f"{API_BASE}{backend_path}"

        headers = {}
        for key in ("content-type", "x-api-key", "accept"):
            val = self.headers.get(key)
            if val:
                headers[key] = val

        body = None
        if method == "POST":
            length = int(self.headers.get("content-length", 0))
            body = self.rfile.read(length) if length else None

        try:
            req = urllib.request.Request(url, data=body, headers=headers, method=method)
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = resp.read()
                self.send_response(resp.status)
                ct = resp.headers.get("content-type", "application/json")
                self.send_header("Content-Type", ct)
                self.send_header("Content-Length", str(len(data)))
                self._cors_headers()
                self.end_headers()
                self.wfile.write(data)
        except urllib.error.HTTPError as e:
            data = e.read()
            self.send_response(e.code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self._cors_headers()
            self.end_headers()
            self.wfile.write(data)
        except Exception as e:
            err = json.dumps({"error": {"code": "PROXY_ERROR", "message": str(e)}}).encode()
            self.send_response(502)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(err)))
            self._cors_headers()
            self.end_headers()
            self.wfile.write(err)

    def _serve_example(self) -> None:
        filename = self.path.split("/examples/")[-1]
        filepath = EXAMPLES_DIR / filename
        if filepath.is_file() and filepath.suffix == ".json":
            content = filepath.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(content)))
            self._cors_headers()
            self.end_headers()
            self.wfile.write(content)
        else:
            self.send_error(404)

    def _cors_headers(self) -> None:
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, X-Api-Key")

    def end_headers(self) -> None:
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def log_message(self, format: str, *args: object) -> None:
        path = str(args[0]) if args else ""
        if "/healthz" not in path and "/readyz" not in path:
            super().log_message(format, *args)


def main() -> None:
    parser = argparse.ArgumentParser(description="HNIDS Dashboard")
    parser.add_argument("--port", type=int, default=3000)
    parser.add_argument("--api", default="http://127.0.0.1:8000")
    args = parser.parse_args()

    global API_BASE
    API_BASE = args.api

    if not (FRONTEND_DIR / "index.html").is_file():
        print(f"ERROR: Frontend not found at {FRONTEND_DIR}")
        return

    print(f"\n  HNIDS Dashboard")
    print(f"  ───────────────────────────────")
    print(f"  Dashboard  http://127.0.0.1:{args.port}")
    print(f"  API proxy  /api/* → {args.api}")
    print(f"  ───────────────────────────────\n")

    with socketserver.TCPServer(("", args.port), DashboardHandler) as httpd:
        httpd.allow_reuse_address = True
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\nStopped.")


if __name__ == "__main__":
    main()
