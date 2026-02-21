"""Minimal mock MCP server for tests: tools list and optional args echo."""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any


class MockMCPHandler(BaseHTTPRequestHandler):
    """HTTP handler: GET /tools returns tools list, POST /call echoes args."""

    def log_message(self, format: str, *args: Any) -> None:
        pass

    def do_GET(self) -> None:
        if self.path == "/tools" or self.path == "/tools/":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(
                json.dumps(
                    {"tools": [{"name": "test_tool", "description": "A test tool"}]}
                ).encode()
            )
            return
        self.send_response(404)
        self.end_headers()

    def do_POST(self) -> None:
        if self.path == "/call" or self.path == "/call/":
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length).decode("utf-8") if length else "{}"
            try:
                data = json.loads(body) if body else {}
            except json.JSONDecodeError:
                data = {}
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(
                json.dumps({"ok": True, "args_received": data.get("args", {})}).encode()
            )
            return
        self.send_response(404)
        self.end_headers()


def run_mock_mcp_server(host: str = "127.0.0.1", port: int = 0) -> tuple[HTTPServer, int]:
    """Start mock MCP server in a thread; return (server, port)."""
    server = HTTPServer((host, port), MockMCPHandler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, port
