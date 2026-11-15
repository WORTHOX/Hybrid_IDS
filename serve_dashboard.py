#!/usr/bin/env python3
"""Serve the HNIDS frontend dashboard and proxy to the FastAPI backend.

Usage:
    python serve_dashboard.py [--port 3000] [--api http://127.0.0.1:8000]

The frontend is served on the specified port (default 3000).
The API backend should be running separately on port 8000.
"""
from __future__ import annotations

import argparse
import http.server
import json
import os
import socketserver
import urllib.request
import urllib.error
from pathlib import Path

FRONTEND_DIR = Path(__file__).parent / "frontend"
EXAMPLES_DIR = Path(__file__).parent / "examples"


class DashboardHandler(http.server.SimpleHTTPRequestHandler):
    """Serve static files from the frontend directory, with examples fallback."""

    def __init__(self, *args: object, **kwargs: object) -> None:
        super().__init__(*args, directory=str(FRONTEND_DIR), **kwargs)  # type: ignore[arg-type]

    def do_GET(self) -> None:
        # Serve example files from the examples directory
        if self.path.startswith("/examples/"):
            filename = self.path.split("/examples/")[1]
            filepath = EXAMPLES_DIR / filename
            if filepath.is_file() and filepath.suffix == ".json":
                content = filepath.read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(content)))
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                self.wfile.write(content)
                return
            self.send_error(404)
            return

        super().do_GET()

    def end_headers(self) -> None:
        # Add CORS headers and cache control
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def log_message(self, format: str, *args: object) -> None:
        # Quieter logging
        if "/healthz" not in str(args[0]) and "/readyz" not in str(args[0]):
            super().log_message(format, *args)


def main() -> None:
    parser = argparse.ArgumentParser(description="HNIDS Dashboard Server")
    parser.add_argument("--port", type=int, default=3000, help="Port for dashboard (default: 3000)")
    parser.add_argument("--api", default="http://127.0.0.1:8000", help="API backend URL")
    args = parser.parse_args()

    # Check if frontend exists
    if not (FRONTEND_DIR / "index.html").is_file():
        print(f"ERROR: Frontend not found at {FRONTEND_DIR}")
        return

    print(f"╔══════════════════════════════════════════════╗")
    print(f"║  HNIDS DASHBOARD                             ║")
    print(f"╠══════════════════════════════════════════════╣")
    print(f"║  Dashboard:  http://127.0.0.1:{args.port:<5}           ║")
    print(f"║  API:        {args.api:<32} ║")
    print(f"╚══════════════════════════════════════════════╝")

    with socketserver.TCPServer(("", args.port), DashboardHandler) as httpd:
        httpd.allow_reuse_address = True
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\nDashboard stopped.")


if __name__ == "__main__":
    main()
