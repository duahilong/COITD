from __future__ import annotations

import json
import socket
import subprocess
import tempfile
import unittest
from pathlib import Path

from tests.mock_cf import MockCfServer, MockCfState


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class SyncDdnsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "config").mkdir(parents=True, exist_ok=True)
        (self.root / "data").mkdir(parents=True, exist_ok=True)

        port = free_port()
        self.cf_state = MockCfState(content="1.1.1.1")
        self.cf = MockCfServer("127.0.0.1", port, self.cf_state)
        self.cf.start()
        self.api_base = f"http://127.0.0.1:{port}/client/v4"

        (self.root / "config" / "cf_token").write_text("test-token\n", encoding="utf-8")
        (self.root / "data" / "state.json").write_text(json.dumps({"bestIp": "1.1.1.2"}), encoding="utf-8")

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
                f'CF_API_BASE="{self.api_base}"',
                f'CF_API_TOKEN_FILE="{(self.root / "config" / "cf_token").as_posix()}"',
                'CF_ZONE_NAME="example.com"',
                'CF_RECORD_NAME="edge.example.com"',
                'CF_RECORD_TYPE="AUTO"',
                'CF_TTL="120"',
                'CF_PROXIED="false"',
                'DDNS_MAX_RETRIES="3"',
                'DDNS_RETRY_BASE_SEC="1"',
                f'DNS_STATE_FILE="{(self.root / "data" / "dns_state.json").as_posix()}"',
                f'DDNS_HISTORY_FILE="{(self.root / "data" / "ddns_history.jsonl").as_posix()}"',
                f'DDNS_LOCK_FILE="{(self.root / "data" / "ddns.lock").as_posix()}"',
                f'DDNS_LOCK_META_FILE="{(self.root / "data" / "ddns.lock.meta.json").as_posix()}"',
            ])
            + '\n',
            encoding="utf-8",
        )

        self.script = Path(__file__).resolve().parents[1] / "scripts" / "sync_ddns.py"

    def tearDown(self):
        self.cf.stop()
        self.tmp.cleanup()

    def run_sync(self, *args: str):
        cmd = [
            "python",
            str(self.script),
            *args,
            "--json",
            "--collector-config",
            str(self.root / "config" / "collector.env"),
            "--ddns-config",
            str(self.root / "config" / "ddns.env"),
        ]
        p = subprocess.run(cmd, capture_output=True, text=True, check=False)
        self.assertTrue(p.stdout.strip(), p.stderr)
        out = json.loads(p.stdout.strip().splitlines()[-1])
        return p.returncode, out

    def test_sync_then_noop(self):
        rc1, out1 = self.run_sync("sync")
        self.assertEqual(rc1, 0)
        self.assertEqual(out1["code"], "OK")
        self.assertEqual(self.cf_state.content, "1.1.1.2")

        rc2, out2 = self.run_sync("sync")
        self.assertEqual(rc2, 0)
        self.assertEqual(out2["code"], "DDNS_NOOP")

        history_path = self.root / "data" / "ddns_history.jsonl"
        lines = history_path.read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(lines), 2)

    def test_retry_on_429(self):
        self.cf_state.fail_patch_429_times = 1
        rc, out = self.run_sync("sync")
        self.assertEqual(rc, 0)
        self.assertEqual(out["code"], "OK")
        self.assertGreaterEqual(self.cf_state.patch_count, 2)

    def test_mismatch_record_type(self):
        (self.root / "config" / "ddns.env").write_text(
            '\n'.join([
                f'CF_API_BASE="{self.api_base}"',
                f'CF_API_TOKEN_FILE="{(self.root / "config" / "cf_token").as_posix()}"',
                'CF_ZONE_NAME="example.com"',
                'CF_RECORD_NAME="edge.example.com"',
                'CF_RECORD_TYPE="AAAA"',
            ])
            + '\n',
            encoding="utf-8",
        )
        rc, out = self.run_sync("sync")
        self.assertNotEqual(rc, 0)
        self.assertEqual(out["code"], "CONFIG_INVALID")


if __name__ == "__main__":
    unittest.main()
