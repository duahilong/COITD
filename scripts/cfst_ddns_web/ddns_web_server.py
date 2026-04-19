#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

import argparse
import datetime as dt
import json
import subprocess
from collections import deque
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, List
from urllib.parse import parse_qs, urlparse


SCRIPT_PATH = Path(__file__).resolve()
WEB_ROOT = SCRIPT_PATH.parent
INDEX_PATH = WEB_ROOT / "index.html"


def now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).astimezone().isoformat(timespec="seconds")


def run_cmd(args: List[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, capture_output=True, text=True, check=False)


def human_left(seconds: int) -> str:
    if seconds <= 0:
        return "0s"
    parts: List[str] = []
    days, rem = divmod(seconds, 86400)
    hours, rem = divmod(rem, 3600)
    minutes, sec = divmod(rem, 60)
    if days:
        parts.append(f"{days}d")
    if hours:
        parts.append(f"{hours}h")
    if minutes:
        parts.append(f"{minutes}m")
    if sec or not parts:
        parts.append(f"{sec}s")
    return " ".join(parts)


def safe_load_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def tail_lines(path: Path, lines: int) -> List[str]:
    if not path.exists():
        return []
    max_lines = max(1, lines)
    with path.open("r", encoding="utf-8", errors="replace") as f:
        return list(deque((line.rstrip("\n") for line in f), maxlen=max_lines))


def parse_show_kv(text: str) -> Dict[str, str]:
    result: Dict[str, str] = {}
    for line in text.splitlines():
        if "=" not in line:
            continue
        k, v = line.split("=", 1)
        result[k.strip()] = v.strip()
    return result


def us_to_iso(us: int) -> str:
    if us <= 0:
        return ""
    return dt.datetime.fromtimestamp(us / 1_000_000, tz=dt.timezone.utc).astimezone().isoformat(timespec="seconds")


def timer_info(timer_name: str) -> Dict[str, Any]:
    info: Dict[str, Any] = {
        "name": timer_name,
        "active_state": "",
        "sub_state": "",
        "result": "",
        "unit_file_state": "",
        "last_run_at": "",
        "next_run_at": "",
        "left_seconds": None,
        "left_human": "",
        "unit": "",
        "activates": "",
        "errors": [],
    }

    show_proc = run_cmd(["systemctl", "show", timer_name, "-p", "ActiveState", "-p", "SubState", "-p", "Result", "-p", "UnitFileState", "-p", "LastTriggerUSec", "-p", "Unit", "-p", "Triggers"])
    if show_proc.returncode == 0:
        kv = parse_show_kv(show_proc.stdout)
        info["active_state"] = kv.get("ActiveState", "")
        info["sub_state"] = kv.get("SubState", "")
        info["result"] = kv.get("Result", "")
        info["unit_file_state"] = kv.get("UnitFileState", "")
        info["last_run_at"] = kv.get("LastTriggerUSec", "")
        info["unit"] = kv.get("Unit", "")
        info["activates"] = kv.get("Triggers", "")
    else:
        info["errors"].append((show_proc.stderr or "systemctl show failed").strip())

    json_proc = run_cmd(["systemctl", "list-timers", "--all", "--no-legend", "--no-pager", "--output=json", timer_name])
    if json_proc.returncode == 0 and json_proc.stdout.strip():
        try:
            arr = json.loads(json_proc.stdout)
            if isinstance(arr, list) and arr:
                row = arr[0]
                next_us = int(row.get("next", 0) or 0)
                last_us = int(row.get("last", 0) or 0)
                if next_us > 0:
                    info["next_run_at"] = us_to_iso(next_us)
                    now_us = int(dt.datetime.now(dt.timezone.utc).timestamp() * 1_000_000)
                    left_sec = max(0, int((next_us - now_us) / 1_000_000))
                    info["left_seconds"] = left_sec
                    info["left_human"] = human_left(left_sec)
                if last_us > 0:
                    info["last_run_at"] = us_to_iso(last_us)
                if not info["activates"]:
                    info["activates"] = str(row.get("activates", ""))
        except Exception:
            info["errors"].append("failed to parse systemctl list-timers json output")
    else:
        info["errors"].append((json_proc.stderr or "systemctl list-timers failed").strip())

    return info


def service_info(service_name: str) -> Dict[str, Any]:
    info: Dict[str, Any] = {
        "name": service_name,
        "active_state": "",
        "sub_state": "",
        "result": "",
        "exec_main_status": "",
        "exec_main_code": "",
        "active_enter_at": "",
        "active_exit_at": "",
        "state_change_at": "",
        "errors": [],
    }
    proc = run_cmd(
        [
            "systemctl",
            "show",
            service_name,
            "-p",
            "ActiveState",
            "-p",
            "SubState",
            "-p",
            "Result",
            "-p",
            "ExecMainStatus",
            "-p",
            "ExecMainCode",
            "-p",
            "ActiveEnterTimestamp",
            "-p",
            "ActiveExitTimestamp",
            "-p",
            "StateChangeTimestamp",
            "-p",
            "UnitFileState",
        ]
    )
    if proc.returncode == 0:
        kv = parse_show_kv(proc.stdout)
        info["active_state"] = kv.get("ActiveState", "")
        info["sub_state"] = kv.get("SubState", "")
        info["result"] = kv.get("Result", "")
        info["exec_main_status"] = kv.get("ExecMainStatus", "")
        info["exec_main_code"] = kv.get("ExecMainCode", "")
        info["active_enter_at"] = kv.get("ActiveEnterTimestamp", "")
        info["active_exit_at"] = kv.get("ActiveExitTimestamp", "")
        info["state_change_at"] = kv.get("StateChangeTimestamp", "")
        info["unit_file_state"] = kv.get("UnitFileState", "")
    else:
        info["errors"].append((proc.stderr or "systemctl show service failed").strip())
    return info


def read_history(history_dir: Path, limit: int) -> List[Dict[str, Any]]:
    if not history_dir.exists():
        return []
    rows: List[Dict[str, Any]] = []
    files = sorted(history_dir.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    for p in files[: max(1, limit)]:
        data = safe_load_json(p, {})
        if not isinstance(data, dict):
            continue
        rows.append(
            {
                "run_id": data.get("run_id", p.stem),
                "status": data.get("status", ""),
                "exit_code": data.get("exit_code", ""),
                "started_at": data.get("started_at", ""),
                "finished_at": data.get("finished_at", ""),
                "duration_seconds": data.get("duration_seconds", ""),
                "selected_ips": data.get("selected_ips", []),
                "ip_changed": data.get("ip_changed", False),
                "ddns_success": data.get("ddns", {}).get("success_count", 0),
                "ddns_failed": data.get("ddns", {}).get("failed_count", 0),
                "file": str(p),
            }
        )
    return rows


class DDNSWebHandler(BaseHTTPRequestHandler):
    server_version = "CFSTDDNSWeb/0.1"

    @property
    def timer_name(self) -> str:
        return self.server.timer_name  # type: ignore[attr-defined]

    @property
    def service_name(self) -> str:
        return self.server.service_name  # type: ignore[attr-defined]

    @property
    def state_dir(self) -> Path:
        return self.server.state_dir  # type: ignore[attr-defined]

    @property
    def latest_file(self) -> Path:
        return self.server.latest_file  # type: ignore[attr-defined]

    @property
    def history_dir(self) -> Path:
        return self.server.history_dir  # type: ignore[attr-defined]

    @property
    def run_log_file(self) -> Path:
        return self.server.run_log_file  # type: ignore[attr-defined]

    def _send_json(self, payload: Dict[str, Any], status: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _send_text(self, text: str, status: int = 200, ctype: str = "text/plain; charset=utf-8") -> None:
        body = text.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _read_json_body(self) -> Dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0") or "0")
        if length <= 0:
            return {}
        raw = self.rfile.read(length).decode("utf-8", errors="replace")
        return json.loads(raw) if raw.strip() else {}

    def do_OPTIONS(self) -> None:  # noqa: N802
        self.send_response(HTTPStatus.NO_CONTENT)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET,POST,OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        route = parsed.path
        query = parse_qs(parsed.query)

        if route == "/":
            if not INDEX_PATH.exists():
                self._send_text("index.html not found", status=500)
                return
            self._send_text(INDEX_PATH.read_text(encoding="utf-8"), ctype="text/html; charset=utf-8")
            return

        if route == "/healthz":
            self._send_json({"ok": True, "status": "up", "time": now_iso()})
            return

        if route == "/api/dashboard":
            limit = int((query.get("limit", ["20"])[0] or "20").strip())
            latest = safe_load_json(self.latest_file, {})
            history = read_history(self.history_dir, limit=limit)
            self._send_json(
                {
                    "ok": True,
                    "time": now_iso(),
                    "timer": timer_info(self.timer_name),
                    "service": service_info(self.service_name),
                    "latest": latest if isinstance(latest, dict) else {},
                    "history": history,
                    "paths": {
                        "latest_file": str(self.latest_file),
                        "history_dir": str(self.history_dir),
                        "run_log_file": str(self.run_log_file),
                    },
                }
            )
            return

        if route == "/api/latest":
            latest = safe_load_json(self.latest_file, {})
            self._send_json({"ok": True, "time": now_iso(), "latest": latest if isinstance(latest, dict) else {}})
            return

        if route == "/api/history":
            limit = int((query.get("limit", ["20"])[0] or "20").strip())
            self._send_json({"ok": True, "time": now_iso(), "history": read_history(self.history_dir, limit=limit)})
            return

        if route == "/api/logs":
            lines = int((query.get("lines", ["120"])[0] or "120").strip())
            self._send_json(
                {
                    "ok": True,
                    "time": now_iso(),
                    "path": str(self.run_log_file),
                    "lines": tail_lines(self.run_log_file, lines=lines),
                }
            )
            return

        self._send_json({"ok": False, "error": "not_found"}, status=404)

    def do_POST(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        route = parsed.path

        if route == "/api/run-now":
            proc = run_cmd(["systemctl", "start", self.service_name])
            if proc.returncode != 0:
                self._send_json(
                    {
                        "ok": False,
                        "error": (proc.stderr or "failed to start service").strip(),
                        "service": self.service_name,
                    },
                    status=500,
                )
                return
            self._send_json(
                {
                    "ok": True,
                    "message": "service started",
                    "service": self.service_name,
                    "time": now_iso(),
                    "service_status": service_info(self.service_name),
                    "timer_status": timer_info(self.timer_name),
                }
            )
            return

        self._send_json({"ok": False, "error": "not_found"}, status=404)

    def log_message(self, fmt: str, *args: Any) -> None:
        return


def main() -> int:
    parser = argparse.ArgumentParser(description="DDNS dedicated monitor web server")
    parser.add_argument("--host", default="0.0.0.0", help="bind host")
    parser.add_argument("--port", type=int, default=8091, help="bind port")
    parser.add_argument("--timer-name", default="cfst-ddns.timer", help="systemd timer unit name")
    parser.add_argument("--service-name", default="cfst-ddns.service", help="systemd service unit name")
    parser.add_argument("--state-dir", default="/root/coitd/scripts/cfst_ddns/state", help="summary state directory")
    parser.add_argument("--run-log-file", default="/root/coitd/scripts/cfst_ddns/logs/cfst_ddns_run.log", help="run log file path")
    args = parser.parse_args()

    state_dir = Path(args.state_dir).resolve()
    server = ThreadingHTTPServer((args.host, args.port), DDNSWebHandler)
    server.bind_host = args.host  # type: ignore[attr-defined]
    server.bind_port = args.port  # type: ignore[attr-defined]
    server.timer_name = args.timer_name  # type: ignore[attr-defined]
    server.service_name = args.service_name  # type: ignore[attr-defined]
    server.state_dir = state_dir  # type: ignore[attr-defined]
    server.latest_file = state_dir / "latest.json"  # type: ignore[attr-defined]
    server.history_dir = state_dir / "history"  # type: ignore[attr-defined]
    server.run_log_file = Path(args.run_log_file).resolve()  # type: ignore[attr-defined]

    print(f"[DDNS_WEB] listening on http://{args.host}:{args.port}")
    print(f"[DDNS_WEB] timer={args.timer_name} service={args.service_name}")
    print(f"[DDNS_WEB] summary={server.latest_file} history={server.history_dir}")
    server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

