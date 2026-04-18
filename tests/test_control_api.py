from __future__ import annotations

import http.cookiejar
import json
import os
import socket
import subprocess
import tempfile
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path

from tests.mock_cf import MockCfServer, MockCfState


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def wait_http(url: str, timeout: float = 10.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=1.0) as resp:
                if resp.status == 200:
                    return
        except Exception:
            time.sleep(0.2)
    raise RuntimeError("server not ready")


class ControlApiTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        for d in ("config", "data", "logs"):
            (self.root / d).mkdir(parents=True, exist_ok=True)

        self.cf_port = free_port()
        self.cf_state = MockCfState(content="1.1.1.1")
        self.cf = MockCfServer("127.0.0.1", self.cf_port, self.cf_state)
        self.cf.start()

        (self.root / "config" / "cf_token").write_text("token\n", encoding="utf-8")
        (self.root / "data" / "state.json").write_text(json.dumps({"bestIp": "1.1.1.2"}), encoding="utf-8")

        collector_script = self.root / "fake_collector.py"
        collector_script.write_text(
            """
import json
import sys

def env(ok=True, code='OK', message='success', data=None):
    return {'ok': ok, 'code': code, 'message': message, 'data': data or {}, 'ts': '2026-01-01T00:00:00+08:00', 'traceId': 'test-trace'}

cmd = sys.argv[1] if len(sys.argv) > 1 else ''
if cmd == 'status':
    out = env(True, 'OK', 'success', {'state': {'bestIp': '1.1.1.2'}})
elif cmd == 'run-once':
    out = env(True, 'OK', 'success', {'bestIp': '1.1.1.2'})
elif cmd == 'history':
    out = env(True, 'OK', 'success', {'items': []})
elif cmd == 'validate-config':
    out = env(True, 'OK', 'success', {'config': 'ok'})
else:
    out = env(False, 'CONFIG_INVALID', 'bad cmd', {})
print(json.dumps(out, ensure_ascii=False))
sys.exit(0 if out['ok'] else 11)
""".strip(),
            encoding="utf-8",
        )

        (self.root / "config" / "collector.env").write_text(
            '\n'.join([
                'IP_VERSION="4"',
                f'STATE_FILE="{(self.root / "data" / "state.json").as_posix()}"',
            ])
            + '\n',
            encoding="utf-8",
        )

        (self.root / "config" / "ddns.env").write_text(
            '\n'.join([
                f'CF_API_BASE="http://127.0.0.1:{self.cf_port}/client/v4"',
                f'CF_API_TOKEN_FILE="{(self.root / "config" / "cf_token").as_posix()}"',
                'CF_ZONE_NAME="example.com"',
                'CF_RECORD_NAME="edge.example.com"',
                'CF_RECORD_TYPE="AUTO"',
                f'DNS_STATE_FILE="{(self.root / "data" / "dns_state.json").as_posix()}"',
                f'DDNS_HISTORY_FILE="{(self.root / "data" / "ddns_history.jsonl").as_posix()}"',
                f'DDNS_LOCK_FILE="{(self.root / "data" / "ddns.lock").as_posix()}"',
                f'DDNS_LOCK_META_FILE="{(self.root / "data" / "ddns.lock.meta.json").as_posix()}"',
            ])
            + '\n',
            encoding="utf-8",
        )

        self.api_port = free_port()
        (self.root / "config" / "webui.env").write_text(
            '\n'.join([
                'BIND_HOST="127.0.0.1"',
                f'BIND_PORT="{self.api_port}"',
                'ADMIN_USERNAME="admin"',
                'ADMIN_PASSWORD="pass123"',
                'SESSION_HOURS="12"',
                f'SCHEDULER_STATE_FILE="{(self.root / "data" / "scheduler_state.json").as_posix()}"',
                f'AUDIT_LOG_FILE="{(self.root / "data" / "audit_log.jsonl").as_posix()}"',
                f'API_LOG_FILE="{(self.root / "logs" / "control_api.log").as_posix()}"',
            ])
            + '\n',
            encoding="utf-8",
        )

        self.control_script = Path(__file__).resolve().parents[1] / "scripts" / "control_api.py"
        self.sync_script = Path(__file__).resolve().parents[1] / "scripts" / "sync_ddns.py"
        self.webui_dir = Path(__file__).resolve().parents[1] / "webui"

        env = os.environ.copy()
        env.update(
            {
                "COLLECTOR_CONFIG_FILE": str(self.root / "config" / "collector.env"),
                "DDNS_CONFIG_FILE": str(self.root / "config" / "ddns.env"),
                "WEBUI_CONFIG_FILE": str(self.root / "config" / "webui.env"),
                "COLLECTOR_SCRIPT": str(collector_script),
                "DDNS_SCRIPT": str(self.sync_script),
                "WEBUI_DIR": str(self.webui_dir),
            }
        )
        self.proc = subprocess.Popen(
            ["python", str(self.control_script), "--host", "127.0.0.1", "--port", str(self.api_port)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            text=True,
            env=env,
        )
        wait_http(f"http://127.0.0.1:{self.api_port}/api/v1/system/healthz")

        cj = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))
        self.csrf = ""

    def tearDown(self):
        self.proc.terminate()
        try:
            self.proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.proc.kill()
        self.cf.stop()
        self.tmp.cleanup()

    def req(self, path: str, method: str = "GET", body: dict | None = None, auth: bool = False):
        headers = {}
        data = None
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json"
        if auth and method != "GET":
            headers["X-CSRF-Token"] = self.csrf
        req = urllib.request.Request(f"http://127.0.0.1:{self.api_port}{path}", method=method, data=data, headers=headers)
        try:
            with self.opener.open(req, timeout=8) as resp:
                raw = resp.read().decode("utf-8")
                return resp.status, json.loads(raw)
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode("utf-8")
            return exc.code, json.loads(raw)

    def login(self):
        code, payload = self.req("/api/v1/auth/login", "POST", {"username": "admin", "password": "pass123"})
        self.assertEqual(code, 200)
        self.assertTrue(payload["ok"])
        self.csrf = payload["data"]["csrfToken"]

    def test_auth_and_core_endpoints(self):
        self.login()

        code, c_status = self.req("/api/v1/collector/status")
        self.assertEqual(code, 200)
        self.assertTrue(c_status["ok"])

        code, c_run = self.req("/api/v1/collector/run", "POST", {}, auth=True)
        self.assertEqual(code, 200)
        self.assertEqual(c_run["code"], "OK")

        code, d_sync = self.req("/api/v1/ddns/sync", "POST", {}, auth=True)
        self.assertEqual(code, 200)
        self.assertIn(d_sync["code"], ["OK", "DDNS_NOOP"])

        code, metrics = self.req("/api/v1/system/metrics")
        self.assertEqual(code, 200)
        self.assertTrue(metrics["ok"])
        self.assertGreaterEqual(metrics["data"]["metrics"]["collector_run_total"], 1)

    def test_schedule_enable_pause(self):
        self.login()
        code, out1 = self.req("/api/v1/collector/schedule/enable", "POST", {"intervalMinutes": 1}, auth=True)
        self.assertEqual(code, 200)
        self.assertTrue(out1["ok"])

        code, out2 = self.req("/api/v1/collector/schedule/pause", "POST", {}, auth=True)
        self.assertEqual(code, 200)
        self.assertTrue(out2["ok"])


if __name__ == "__main__":
    unittest.main()
