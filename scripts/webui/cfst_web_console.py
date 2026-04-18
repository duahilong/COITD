#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import shlex
import subprocess
import sys
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, List, Tuple
from urllib.parse import parse_qs, urlparse


SCRIPT_PATH = Path(__file__).resolve()
WEBUI_DIR = SCRIPT_PATH.parent
PROJECT_ROOT = SCRIPT_PATH.parents[2]
CFST_DIR = PROJECT_ROOT / "scripts" / "cfst"
CONTROLLER_PATH = CFST_DIR / "cfst_job_controller.py"
INDEX_PATH = WEBUI_DIR / "index.html"
SCHEDULE_BEGIN_MARKER = "# BEGIN CFST_WEB_SCHEDULE"
SCHEDULE_END_MARKER = "# END CFST_WEB_SCHEDULE"
SCHEDULE_FILE_NAME = "schedule.json"
SCHEDULE_SETUP_LOG_NAME = "schedule_setup.log"
SCHEDULE_RUN_LOG_NAME = "cron.log"
RUNS_DIR_NAME = "runs"
RUN_META_FILE_NAME = "meta.json"


def safe_json_loads(text: str) -> Dict[str, Any]:
    text = text.strip()
    if not text:
        return {}
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        lines = [line for line in text.splitlines() if line.strip()]
        if lines:
            return json.loads(lines[-1])
        raise


def run_controller(
    controller_path: Path,
    state_dir: Path,
    args: List[str],
    cwd: Path | None = None,
) -> Tuple[int, Dict[str, Any], str, str]:
    cmd = [sys.executable, str(controller_path), *args]
    if "--json" not in cmd:
        cmd.append("--json")
    if "--state-dir" not in cmd:
        cmd.extend(["--state-dir", str(state_dir)])

    proc = subprocess.run(
        cmd,
        cwd=str(cwd or PROJECT_ROOT),
        capture_output=True,
        text=True,
    )
    payload: Dict[str, Any] = {}
    if proc.stdout.strip():
        try:
            payload = safe_json_loads(proc.stdout)
        except Exception:
            payload = {"ok": False, "error": "invalid_json_from_controller", "stdout": proc.stdout}
    elif proc.stderr.strip():
        payload = {"ok": False, "error": proc.stderr.strip()}
    return proc.returncode, payload, proc.stdout, proc.stderr


def discover_configs(cfst_dir: Path) -> List[str]:
    configs: List[str] = []
    for ext in ("*.json", "*.jsonc"):
        for p in sorted(cfst_dir.glob(ext)):
            if p.is_file():
                configs.append(str(p.relative_to(PROJECT_ROOT)).replace("\\", "/"))
    return configs


def normalize_config(user_value: str) -> Path:
    raw = user_value.strip()
    if not raw:
        raise ValueError("config 不能为空")
    candidate = Path(raw)
    if not candidate.is_absolute():
        candidate = (PROJECT_ROOT / candidate).resolve()
    else:
        candidate = candidate.resolve()
    if not candidate.exists():
        raise FileNotFoundError(f"配置文件不存在: {candidate}")
    if not str(candidate).startswith(str(CFST_DIR.resolve())):
        raise ValueError("仅允许使用 scripts/cfst 目录下的配置文件")
    return candidate


def now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).astimezone().isoformat(timespec="seconds")


def schedule_file(state_dir: Path) -> Path:
    return state_dir / SCHEDULE_FILE_NAME


def schedule_setup_log_file(state_dir: Path) -> Path:
    return state_dir / SCHEDULE_SETUP_LOG_NAME


def schedule_run_log_file(state_dir: Path) -> Path:
    return state_dir / SCHEDULE_RUN_LOG_NAME


def append_line(path: Path, line: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(line.rstrip("\n") + "\n")


def append_schedule_setup_log(state_dir: Path, ok: bool, action: str, detail: str, extra: Dict[str, Any] | None = None) -> None:
    payload: Dict[str, Any] = {
        "time": now_iso(),
        "ok": ok,
        "action": action,
        "detail": detail,
    }
    if extra:
        payload.update(extra)
    append_line(schedule_setup_log_file(state_dir), json.dumps(payload, ensure_ascii=False))


def tail_text_lines(path: Path, lines: int = 120) -> List[str]:
    if not path.exists():
        return []
    max_lines = max(1, lines)
    from collections import deque

    with path.open("r", encoding="utf-8", errors="replace") as f:
        return list(deque((line.rstrip("\n") for line in f), maxlen=max_lines))


def run_meta_path(state_dir: Path, run_id: str) -> Path:
    return state_dir / RUNS_DIR_NAME / run_id / RUN_META_FILE_NAME


def read_crontab_lines() -> List[str]:
    proc = subprocess.run(["crontab", "-l"], capture_output=True, text=True)
    if proc.returncode == 0:
        return proc.stdout.splitlines()
    stderr = (proc.stderr or "").strip().lower()
    if "no crontab for" in stderr:
        return []
    raise RuntimeError(proc.stderr.strip() or "读取 crontab 失败")


def write_crontab_lines(lines: List[str]) -> None:
    payload = "\n".join(lines).rstrip("\n") + "\n"
    proc = subprocess.run(["crontab", "-"], input=payload, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or "写入 crontab 失败")


def replace_schedule_block(crontab_lines: List[str], schedule_line: str | None) -> List[str]:
    result: List[str] = []
    in_block = False
    for line in crontab_lines:
        if line.strip() == SCHEDULE_BEGIN_MARKER:
            in_block = True
            continue
        if line.strip() == SCHEDULE_END_MARKER:
            in_block = False
            continue
        if not in_block:
            result.append(line)
    while result and not result[-1].strip():
        result.pop()
    if schedule_line is not None:
        if result:
            result.append("")
        result.extend([SCHEDULE_BEGIN_MARKER, schedule_line, SCHEDULE_END_MARKER])
    return result


def get_schedule_block_line(crontab_lines: List[str]) -> str:
    in_block = False
    for line in crontab_lines:
        if line.strip() == SCHEDULE_BEGIN_MARKER:
            in_block = True
            continue
        if line.strip() == SCHEDULE_END_MARKER:
            in_block = False
            continue
        if in_block and line.strip():
            return line
    return ""


def parse_daily_time(value: str) -> Tuple[int, int]:
    raw = value.strip()
    if not raw:
        return 2, 0
    parts = raw.split(":")
    if len(parts) != 2:
        raise ValueError("daily_time 格式应为 HH:MM")
    hour = int(parts[0])
    minute = int(parts[1])
    if hour < 0 or hour > 23 or minute < 0 or minute > 59:
        raise ValueError("daily_time 不合法")
    return hour, minute


def build_schedule_spec(body: Dict[str, Any]) -> Dict[str, Any]:
    mode = str(body.get("mode", "")).strip() or "half_hourly"
    label = str(body.get("label", "")).strip() or "web-schedule"
    minute = int(body.get("minute", 0))
    if minute < 0 or minute > 59:
        raise ValueError("minute 需在 0-59")
    if mode == "every_minute":
        cron_expr = "* * * * *"
    elif mode == "daily":
        hour, minute = parse_daily_time(str(body.get("daily_time", "02:00")))
        cron_expr = f"{minute} {hour} * * *"
    elif mode == "hourly":
        cron_expr = f"{minute} * * * *"
    elif mode == "half_hourly":
        cron_expr = "*/30 * * * *"
    elif mode == "every_n_hours":
        interval = int(body.get("hour_interval", 2))
        if interval < 2 or interval > 23:
            raise ValueError("hour_interval 需在 2-23")
        cron_expr = f"{minute} */{interval} * * *"
    else:
        raise ValueError("mode 不支持")
    return {
        "mode": mode,
        "label": label,
        "minute": minute,
        "daily_time": str(body.get("daily_time", "02:00")),
        "hour_interval": int(body.get("hour_interval", 2)),
        "cron_expr": cron_expr,
    }


def mode_label(mode: str) -> str:
    mapping = {
        "every_minute": "每分钟运行一次",
        "half_hourly": "每半小时运行一次",
        "hourly": "每一小时运行一次",
        "daily": "每天运行一次",
        "every_n_hours": "每几个小时运行一次",
    }
    return mapping.get(mode, mode)


def build_schedule_command(config_path: Path, state_dir: Path, label: str) -> str:
    py = shlex.quote(sys.executable)
    controller = shlex.quote(str(CONTROLLER_PATH))
    runner = shlex.quote(str(CFST_DIR / "cfst_config_runner.py"))
    cfg = shlex.quote(str(config_path))
    state = shlex.quote(str(state_dir))
    cwd = shlex.quote(str(PROJECT_ROOT))
    run_log = shlex.quote(str(schedule_run_log_file(state_dir)))
    label_q = shlex.quote(label)
    return (
        f"{py} {controller} start -c {cfg} --runner {runner} --cwd {cwd} "
        f"--state-dir {state} --label {label_q} --if-busy skip --json >> {run_log} 2>&1"
    )


class CfstWebHandler(BaseHTTPRequestHandler):
    server_version = "CFSTWebConsole/0.1"

    @property
    def state_dir(self) -> Path:
        return self.server.state_dir  # type: ignore[attr-defined]

    @property
    def bind_host(self) -> str:
        return self.server.bind_host  # type: ignore[attr-defined]

    @property
    def bind_port(self) -> int:
        return self.server.bind_port  # type: ignore[attr-defined]

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
        raw = self.rfile.read(length).decode("utf-8")
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
            self._send_json({"ok": True, "status": "up", "host": self.bind_host, "port": self.bind_port})
            return

        if route == "/api/configs":
            self._send_json({"ok": True, "configs": discover_configs(CFST_DIR)})
            return

        if route == "/api/list":
            limit = int((query.get("limit", ["20"])[0] or "20").strip())
            code, payload, _, stderr = run_controller(
                CONTROLLER_PATH,
                self.state_dir,
                ["list", "--limit", str(limit)],
                cwd=PROJECT_ROOT,
            )
            if code != 0 and not payload.get("ok", False):
                self._send_json({"ok": False, "error": payload.get("error", stderr or "list_failed")}, status=500)
                return
            self._send_json(payload)
            return

        if route == "/api/status":
            run_id = (query.get("run_id", [""])[0] or "").strip()
            if not run_id:
                self._send_json({"ok": False, "error": "run_id is required"}, status=400)
                return
            if not run_meta_path(self.state_dir, run_id).exists():
                self._send_json(
                    {
                        "ok": False,
                        "error_code": "run_not_found",
                        "error": f"run not found: {run_id}",
                        "run_id": run_id,
                    },
                    status=404,
                )
                return
            code, payload, _, stderr = run_controller(
                CONTROLLER_PATH,
                self.state_dir,
                ["status", "--run-id", run_id],
                cwd=PROJECT_ROOT,
            )
            if code != 0 and not payload.get("ok", False):
                self._send_json({"ok": False, "error": payload.get("error", stderr or "status_failed")}, status=500)
                return
            self._send_json(payload)
            return

        if route == "/api/logs":
            run_id = (query.get("run_id", [""])[0] or "").strip()
            lines = int((query.get("lines", ["120"])[0] or "120").strip())
            if not run_id:
                self._send_json({"ok": False, "error": "run_id is required"}, status=400)
                return
            if not run_meta_path(self.state_dir, run_id).exists():
                self._send_json(
                    {
                        "ok": False,
                        "error_code": "run_not_found",
                        "error": f"run not found: {run_id}",
                        "run_id": run_id,
                    },
                    status=404,
                )
                return
            code, payload, _, stderr = run_controller(
                CONTROLLER_PATH,
                self.state_dir,
                ["logs", "--run-id", run_id, "--lines", str(lines)],
                cwd=PROJECT_ROOT,
            )
            if code != 0 and not payload.get("ok", False):
                self._send_json({"ok": False, "error": payload.get("error", stderr or "logs_failed")}, status=500)
                return
            self._send_json(payload)
            return

        if route == "/api/cron-template":
            config = (query.get("config", ["scripts/cfst/cfst_config.full.json"])[0] or "").strip()
            cron_expr = (query.get("cron_expr", ["*/30 * * * *"])[0] or "").strip()
            label = (query.get("label", ["cron"])[0] or "cron").strip()
            cfg = normalize_config(config)
            code, payload, _, stderr = run_controller(
                CONTROLLER_PATH,
                self.state_dir,
                [
                    "cron-template",
                    "-c",
                    str(cfg),
                    "--cron-expr",
                    cron_expr,
                    "--label",
                    label,
                ],
                cwd=PROJECT_ROOT,
            )
            if code != 0 and not payload.get("ok", False):
                self._send_json({"ok": False, "error": payload.get("error", stderr or "cron_template_failed")}, status=500)
                return
            self._send_json(payload)
            return

        if route == "/api/schedule/status":
            try:
                lines = read_crontab_lines()
                block_line = get_schedule_block_line(lines)
            except Exception as exc:
                self._send_json({"ok": False, "error": str(exc)}, status=500)
                return

            sch_file = schedule_file(self.state_dir)
            saved: Dict[str, Any] = {}
            if sch_file.exists():
                try:
                    saved = json.loads(sch_file.read_text(encoding="utf-8"))
                except Exception:
                    saved = {}
            self._send_json(
                {
                    "ok": True,
                    "enabled": bool(block_line),
                    "schedule": saved,
                    "schedule_mode_label": mode_label(str(saved.get("mode", ""))) if saved else "",
                    "crontab_line": block_line,
                    "setup_log": str(schedule_setup_log_file(self.state_dir)),
                    "run_log": str(schedule_run_log_file(self.state_dir)),
                }
            )
            return

        if route == "/api/schedule/logs":
            lines = int((query.get("lines", ["80"])[0] or "80").strip())
            setup_lines = tail_text_lines(schedule_setup_log_file(self.state_dir), lines=lines)
            run_lines = tail_text_lines(schedule_run_log_file(self.state_dir), lines=lines)
            self._send_json(
                {
                    "ok": True,
                    "setup_log_file": str(schedule_setup_log_file(self.state_dir)),
                    "run_log_file": str(schedule_run_log_file(self.state_dir)),
                    "setup_lines": setup_lines,
                    "run_lines": run_lines,
                }
            )
            return

        self._send_json({"ok": False, "error": "not_found"}, status=404)

    def do_POST(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        route = parsed.path
        try:
            body = self._read_json_body()
        except json.JSONDecodeError:
            self._send_json({"ok": False, "error": "invalid_json_body"}, status=400)
            return

        if route == "/api/start":
            config_raw = str(body.get("config", "")).strip()
            label = str(body.get("label", "")).strip()
            try:
                cfg = normalize_config(config_raw)
            except Exception as exc:
                self._send_json({"ok": False, "error": str(exc)}, status=400)
                return

            args = ["start", "-c", str(cfg), "--cwd", str(PROJECT_ROOT)]
            if label:
                args.extend(["--label", label])

            code, payload, _, stderr = run_controller(CONTROLLER_PATH, self.state_dir, args, cwd=PROJECT_ROOT)
            if code != 0 and not payload.get("ok", False):
                if payload.get("error_code") in {"active_run_exists", "start_lock_busy"}:
                    self._send_json(payload, status=409)
                    return
                self._send_json({"ok": False, "error": payload.get("error", stderr or "start_failed")}, status=500)
                return
            self._send_json(payload)
            return

        if route == "/api/stop":
            run_id = str(body.get("run_id", "")).strip()
            timeout_sec = int(body.get("timeout_sec", 8))
            if not run_id:
                self._send_json({"ok": False, "error": "run_id is required"}, status=400)
                return
            code, payload, _, stderr = run_controller(
                CONTROLLER_PATH,
                self.state_dir,
                ["stop", "--run-id", run_id, "--timeout-sec", str(timeout_sec)],
                cwd=PROJECT_ROOT,
            )
            if code != 0 and not payload.get("ok", False):
                self._send_json({"ok": False, "error": payload.get("error", stderr or "stop_failed")}, status=500)
                return
            self._send_json(payload)
            return

        if route == "/api/schedule/setup":
            config_raw = str(body.get("config", "")).strip()
            try:
                cfg = normalize_config(config_raw)
                spec = build_schedule_spec(body)
                cron_expr = spec["cron_expr"]
                command = build_schedule_command(cfg, self.state_dir, spec["label"])
                schedule_line = f"{cron_expr} {command}"

                old_lines = read_crontab_lines()
                new_lines = replace_schedule_block(old_lines, schedule_line)
                write_crontab_lines(new_lines)

                payload: Dict[str, Any] = {
                    "ok": True,
                    "enabled": True,
                    "message": "定时任务设置成功，已写入 Linux crontab。",
                    "configured_at": now_iso(),
                    "config": str(cfg),
                    "mode": spec["mode"],
                    "mode_label": mode_label(spec["mode"]),
                    "cron_expr": cron_expr,
                    "command": command,
                    "crontab_line": schedule_line,
                    "label": spec["label"],
                    "minute": spec["minute"],
                    "daily_time": spec["daily_time"],
                    "hour_interval": spec["hour_interval"],
                    "setup_log": str(schedule_setup_log_file(self.state_dir)),
                    "run_log": str(schedule_run_log_file(self.state_dir)),
                }
                schedule_file(self.state_dir).write_text(
                    json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8",
                )
                append_schedule_setup_log(self.state_dir, True, "setup", "schedule configured", payload)
                self._send_json(payload)
            except Exception as exc:
                append_schedule_setup_log(
                    self.state_dir,
                    False,
                    "setup",
                    str(exc),
                    {"config": config_raw, "request": body},
                )
                self._send_json({"ok": False, "error": str(exc)}, status=500)
            return

        if route == "/api/schedule/clear":
            try:
                old_lines = read_crontab_lines()
                new_lines = replace_schedule_block(old_lines, None)
                write_crontab_lines(new_lines)
                payload = {
                    "ok": True,
                    "enabled": False,
                    "configured_at": now_iso(),
                    "message": "定时任务已清除。",
                    "setup_log": str(schedule_setup_log_file(self.state_dir)),
                    "run_log": str(schedule_run_log_file(self.state_dir)),
                }
                schedule_file(self.state_dir).write_text(
                    json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8",
                )
                append_schedule_setup_log(self.state_dir, True, "clear", "schedule cleared", payload)
                self._send_json(payload)
            except Exception as exc:
                append_schedule_setup_log(self.state_dir, False, "clear", str(exc))
                self._send_json({"ok": False, "error": str(exc)}, status=500)
            return

        self._send_json({"ok": False, "error": "not_found"}, status=404)

    def log_message(self, fmt: str, *args: Any) -> None:
        # 输出简洁访问日志到服务进程 stdout，便于 tail
        message = "%s - - [%s] %s\n" % (
            self.address_string(),
            self.log_date_time_string(),
            fmt % args,
        )
        sys.stdout.write(message)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="CFST 简易 Web 控制台")
    parser.add_argument("--host", default="0.0.0.0", help="监听地址")
    parser.add_argument("--port", type=int, default=8088, help="监听端口")
    parser.add_argument(
        "--state-dir",
        default=str(PROJECT_ROOT / ".cfst_jobs_web"),
        help="任务状态目录（传给 cfst_job_controller.py）",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if not CONTROLLER_PATH.exists():
        print(f"[ERROR] controller not found: {CONTROLLER_PATH}", file=sys.stderr)
        return 1
    if not INDEX_PATH.exists():
        print(f"[ERROR] index.html not found: {INDEX_PATH}", file=sys.stderr)
        return 1

    state_dir = Path(args.state_dir).expanduser().resolve()
    state_dir.mkdir(parents=True, exist_ok=True)

    server = ThreadingHTTPServer((args.host, args.port), CfstWebHandler)
    server.state_dir = state_dir  # type: ignore[attr-defined]
    server.bind_host = args.host  # type: ignore[attr-defined]
    server.bind_port = args.port  # type: ignore[attr-defined]

    print(f"[INFO] CFST Web Console listening on http://{args.host}:{args.port}")
    print(f"[INFO] project_root={PROJECT_ROOT}")
    print(f"[INFO] state_dir={state_dir}")
    try:
        server.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        print("\n[INFO] shutdown requested")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
