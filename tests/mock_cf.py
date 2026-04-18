from __future__ import annotations

import json
import threading
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse


@dataclass
class MockCfState:
    zone_name: str = "example.com"
    record_name: str = "edge.example.com"
    record_type: str = "A"
    content: str = "1.1.1.1"
    zone_id: str = "zone-1"
    record_id: str = "record-1"
    fail_patch_429_times: int = 0
    patch_count: int = 0


class MockCfHandler(BaseHTTPRequestHandler):
    server: "MockCfServer"

    def log_message(self, format, *args):
        return

    def _send(self, status: int, payload: dict):
        raw = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def _ok(self, result):
        self._send(200, {"success": True, "errors": [], "result": result})

    def _err(self, status: int, msg: str):
        self._send(status, {"success": False, "errors": [{"message": msg}], "result": None})

    def do_GET(self):
        state = self.server.state
        parsed = urlparse(self.path)
        path = parsed.path

        if path.endswith("/zones"):
            q = parse_qs(parsed.query)
            name = (q.get("name") or [""])[0]
            if name == state.zone_name:
                return self._ok([{"id": state.zone_id, "name": state.zone_name}])
            return self._ok([])

        if path.endswith(f"/zones/{state.zone_id}/dns_records"):
            q = parse_qs(parsed.query)
            rec_type = (q.get("type") or [""])[0]
            rec_name = (q.get("name") or [""])[0]
            if rec_type == state.record_type and rec_name == state.record_name:
                return self._ok([
                    {
                        "id": state.record_id,
                        "type": state.record_type,
                        "name": state.record_name,
                        "content": state.content,
                    }
                ])
            return self._ok([])

        if path.endswith(f"/zones/{state.zone_id}/dns_records/{state.record_id}"):
            return self._ok(
                {
                    "id": state.record_id,
                    "type": state.record_type,
                    "name": state.record_name,
                    "content": state.content,
                }
            )

        return self._err(404, "not found")

    def do_PATCH(self):
        state = self.server.state
        if not self.path.endswith(f"/zones/{state.zone_id}/dns_records/{state.record_id}"):
            return self._err(404, "not found")

        state.patch_count += 1
        if state.fail_patch_429_times > 0:
            state.fail_patch_429_times -= 1
            return self._err(429, "rate limited")

        length = int(self.headers.get("Content-Length", "0") or "0")
        body = self.rfile.read(length).decode("utf-8")
        payload = json.loads(body or "{}")
        state.content = payload.get("content", state.content)
        state.record_type = payload.get("type", state.record_type)
        return self._ok(
            {
                "id": state.record_id,
                "type": state.record_type,
                "name": state.record_name,
                "content": state.content,
            }
        )


class MockCfServer(ThreadingHTTPServer):
    def __init__(self, host: str, port: int, state: MockCfState):
        super().__init__((host, port), MockCfHandler)
        self.state = state
        self.thread = threading.Thread(target=self.serve_forever, daemon=True)

    def start(self):
        self.thread.start()

    def stop(self):
        self.shutdown()
        self.server_close()
        self.thread.join(timeout=2)
