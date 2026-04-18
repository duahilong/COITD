#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
CFST 浠诲姟鎺у埗鍣紙涓哄悗缁?Web 鎺у埗灞傚仛閫傞厤锛?
鑳藉姏锛?1) start: 鍚庡彴鍚姩涓€娆?CFST 浠诲姟
2) status: 鏌ヨ浠诲姟鐘舵€?3) stop: 鍋滄浠诲姟锛堜紭鍏?SIGTERM锛岃秴鏃跺悗 SIGKILL锛?4) logs: 璇诲彇鏃ュ織灏鹃儴
5) list: 鍒楀嚭鍘嗗彶浠诲姟
6) cron-template: 鐢熸垚 crontab 妯℃澘
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import secrets
import signal
import string
import subprocess
import sys
import time
from contextlib import contextmanager
from collections import deque
from pathlib import Path
from typing import Any, Dict, Iterator, List

try:
    import fcntl  # type: ignore[attr-defined]
except Exception:  # pragma: no cover
    fcntl = None  # type: ignore[assignment]


DEFAULT_STATE_DIR = ".cfst_jobs"
RUNS_DIR_NAME = "runs"
META_FILE_NAME = "meta.json"
LOG_FILE_NAME = "run.log"
SUMMARY_FILE_NAME = "summary.json"
START_LOCK_FILE_NAME = "start.lock"
VALID_RUN_ID = set(string.ascii_letters + string.digits + "._-")
ACTIVE_STATUSES = {"starting", "running", "stopping"}


class StartLockBusyError(RuntimeError):
    pass


def now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).astimezone().isoformat(timespec="seconds")


def normalize_state_dir(path_str: str) -> Path:
    return Path(path_str).expanduser().resolve()


def json_dump(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def json_load(path: Path) -> Dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"鏂囦欢涓嶅瓨鍦? {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def ensure_run_id(run_id: str) -> str:
    if not run_id:
        raise ValueError("run_id 涓嶈兘涓虹┖")
    if any(ch not in VALID_RUN_ID for ch in run_id):
        raise ValueError("run_id 浠呭厑璁稿瓧姣嶃€佹暟瀛椼€佺偣銆佷笅鍒掔嚎銆佷腑鍒掔嚎")
    return run_id


def generate_run_id() -> str:
    return dt.datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + secrets.token_hex(3)


def runs_root(state_dir: Path) -> Path:
    return state_dir / RUNS_DIR_NAME


def run_dir(state_dir: Path, run_id: str) -> Path:
    return runs_root(state_dir) / run_id


def meta_path(state_dir: Path, run_id: str) -> Path:
    return run_dir(state_dir, run_id) / META_FILE_NAME


def log_path(state_dir: Path, run_id: str) -> Path:
    return run_dir(state_dir, run_id) / LOG_FILE_NAME


def summary_path(state_dir: Path, run_id: str) -> Path:
    return run_dir(state_dir, run_id) / SUMMARY_FILE_NAME


def start_lock_path(state_dir: Path) -> Path:
    return state_dir / START_LOCK_FILE_NAME


@contextmanager
def acquire_start_lock(state_dir: Path) -> Iterator[None]:
    """
    Serialize concurrent `start` calls across processes.

    On POSIX, this uses flock on a lock file.
    On platforms without fcntl, it falls back to O_EXCL file creation.
    """
    state_dir.mkdir(parents=True, exist_ok=True)
    lpath = start_lock_path(state_dir)

    if fcntl is not None:
        fd = os.open(lpath, os.O_CREAT | os.O_RDWR, 0o644)
        try:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise StartLockBusyError("start lock is busy") from exc
            os.ftruncate(fd, 0)
            os.write(fd, f"{os.getpid()}\n".encode("utf-8"))
            yield
        finally:
            try:
                fcntl.flock(fd, fcntl.LOCK_UN)
            finally:
                os.close(fd)
        return

    fd = -1
    created = False
    for _ in range(2):
        try:
            fd = os.open(lpath, os.O_CREAT | os.O_EXCL | os.O_RDWR, 0o644)
            created = True
            break
        except FileExistsError:
            stale = True
            try:
                text = lpath.read_text(encoding="utf-8").strip()
                owner_pid = int(text) if text else 0
                stale = not (owner_pid > 0 and pid_is_alive(owner_pid))
            except Exception:
                stale = False
            if not stale:
                raise StartLockBusyError("start lock is busy")
            try:
                lpath.unlink()
            except OSError as exc:
                raise StartLockBusyError("start lock is busy") from exc

    if not created or fd < 0:
        raise StartLockBusyError("start lock is busy")

    try:
        os.write(fd, f"{os.getpid()}\n".encode("utf-8"))
        yield
    finally:
        os.close(fd)
        try:
            lpath.unlink()
        except OSError:
            pass


def pid_is_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def stop_process_group(pid: int, timeout_sec: int) -> Dict[str, Any]:
    if pid <= 0:
        return {"sent": False, "signal": None, "killed": False}
    result = {"sent": False, "signal": "SIGTERM", "killed": False}
    try:
        if hasattr(os, "killpg"):
            os.killpg(pid, signal.SIGTERM)
        else:
            os.kill(pid, signal.SIGTERM)
        result["sent"] = True
    except ProcessLookupError:
        return result

    deadline = time.time() + max(timeout_sec, 1)
    while time.time() < deadline:
        if not pid_is_alive(pid):
            return result
        time.sleep(0.2)

    if pid_is_alive(pid):
        try:
            if hasattr(os, "killpg"):
                os.killpg(pid, signal.SIGKILL)
            else:
                os.kill(pid, signal.SIGKILL)
            result["killed"] = True
            result["signal"] = "SIGKILL"
        except ProcessLookupError:
            pass
    return result


def tail_lines(path: Path, lines: int) -> List[str]:
    if not path.exists():
        return []
    max_lines = max(lines, 1)
    with path.open("r", encoding="utf-8", errors="replace") as f:
        return list(deque(f, maxlen=max_lines))


def read_meta(state_dir: Path, run_id: str) -> Dict[str, Any]:
    return json_load(meta_path(state_dir, run_id))


def write_meta(state_dir: Path, run_id: str, payload: Dict[str, Any]) -> None:
    json_dump(meta_path(state_dir, run_id), payload)


def find_active_run(state_dir: Path) -> Dict[str, Any] | None:
    root = runs_root(state_dir)
    root.mkdir(parents=True, exist_ok=True)
    active: Dict[str, Any] | None = None

    for entry in root.iterdir():
        if not entry.is_dir():
            continue
        mpath = entry / META_FILE_NAME
        if not mpath.exists():
            continue
        try:
            meta = json_load(mpath)
        except Exception:
            continue

        status = str(meta.get("status", "")).strip()
        if status not in ACTIVE_STATUSES:
            continue
        pid = int(meta.get("pid") or 0)
        alive = pid_is_alive(pid)
        if not alive:
            # Heal stale status to avoid a dead process remaining in active status.
            if status == "stopping" or meta.get("stop_requested_at"):
                meta["status"] = "stopped"
            else:
                meta["status"] = "unknown_stopped"
            if not meta.get("finished_at"):
                meta["finished_at"] = now_iso()
            json_dump(mpath, meta)
            continue

        item = {
            "run_id": meta.get("run_id", entry.name),
            "status": status,
            "pid": pid,
            "created_at": meta.get("created_at", ""),
        }
        if active is None or str(item["created_at"]) > str(active.get("created_at", "")):
            active = item
    return active


def emit(payload: Dict[str, Any], as_json: bool) -> None:
    if as_json:
        text = json.dumps(payload, ensure_ascii=False)
        try:
            print(text)
        except UnicodeEncodeError:
            print(json.dumps(payload, ensure_ascii=True))
        return
    for k, v in payload.items():
        print(f"{k}={v}")


def emit_start_busy(
    args: argparse.Namespace,
    message: str,
    *,
    error_code: str,
    active: Dict[str, Any] | None = None,
) -> int:
    payload: Dict[str, Any] = {
        "ok": False,
        "action": "start",
        "error_code": error_code,
        "message": message,
    }
    if active:
        payload.update(
            {
                "active_run_id": active.get("run_id", ""),
                "active_status": active.get("status", ""),
                "active_pid": active.get("pid", 0),
            }
        )
    if args.if_busy == "skip":
        payload.update({"ok": True, "skipped": True})
        emit(payload, args.json)
        return 0
    emit(payload, args.json)
    return 1


def cmd_start(args: argparse.Namespace) -> int:
    state_dir = normalize_state_dir(args.state_dir)
    config_path = Path(args.config).expanduser().resolve()
    runner_path = Path(args.runner).expanduser().resolve()
    cwd_path = Path(args.cwd).expanduser().resolve()
    try:
        with acquire_start_lock(state_dir):
            active = find_active_run(state_dir)
            if active:
                return emit_start_busy(
                    args,
                    message=f"宸叉湁浠诲姟杩愯涓? {active['run_id']}",
                    error_code="active_run_exists",
                    active=active,
                )

            run_id = ensure_run_id(args.run_id or generate_run_id())
            single_run_dir = run_dir(state_dir, run_id)
            if single_run_dir.exists():
                raise FileExistsError(f"浠诲姟宸插瓨鍦? {single_run_dir}")
            single_run_dir.mkdir(parents=True, exist_ok=False)

            meta = {
                "run_id": run_id,
                "label": args.label,
                "status": "starting",
                "created_at": now_iso(),
                "started_at": "",
                "finished_at": "",
                "pid": 0,
                "exit_code": None,
                "stop_requested_at": "",
                "config_path": str(config_path),
                "runner_path": str(runner_path),
                "cwd": str(cwd_path),
                "state_dir": str(state_dir),
                "log_file": str(log_path(state_dir, run_id)),
                "summary_file": str(summary_path(state_dir, run_id)),
            }
            write_meta(state_dir, run_id, meta)

            cmd = [
                sys.executable,
                str(Path(__file__).resolve()),
                "_worker",
                "--state-dir",
                str(state_dir),
                "--run-id",
                run_id,
                "--config",
                str(config_path),
                "--runner",
                str(runner_path),
                "--cwd",
                str(cwd_path),
            ]
            proc = subprocess.Popen(
                cmd,
                cwd=str(cwd_path),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )

            meta["status"] = "running"
            meta["started_at"] = now_iso()
            meta["pid"] = int(proc.pid)
            write_meta(state_dir, run_id, meta)

            payload = {
                "ok": True,
                "action": "start",
                "run_id": run_id,
                "status": "running",
                "pid": proc.pid,
                "log_file": meta["log_file"],
                "summary_file": meta["summary_file"],
            }
            emit(payload, args.json)
            return 0
    except StartLockBusyError:
        return emit_start_busy(
            args,
            message="鍚姩閿佸凡琚崰鐢紝璇风◢鍚庨噸璇曘€?",
            error_code="start_lock_busy",
        )


def cmd_worker(args: argparse.Namespace) -> int:
    state_dir = normalize_state_dir(args.state_dir)
    run_id = ensure_run_id(args.run_id)
    config_path = Path(args.config).expanduser().resolve()
    runner_path = Path(args.runner).expanduser().resolve()
    cwd_path = Path(args.cwd).expanduser().resolve()
    mpath = meta_path(state_dir, run_id)

    meta = read_meta(state_dir, run_id)
    meta["status"] = "running"
    if not meta.get("started_at"):
        meta["started_at"] = now_iso()
    write_meta(state_dir, run_id, meta)

    lpath = log_path(state_dir, run_id)
    spath = summary_path(state_dir, run_id)
    cmd = [
        sys.executable,
        str(runner_path),
        "-c",
        str(config_path),
        "--summary-json",
        str(spath),
        "--print-summary-json",
    ]

    start_mono = time.monotonic()
    with lpath.open("a", encoding="utf-8") as lf:
        lf.write(f"[CONTROLLER] run_id={run_id}\n")
        lf.write(f"[CONTROLLER] started_at={now_iso()}\n")
        lf.write(f"[CONTROLLER] command={' '.join(cmd)}\n")
        lf.flush()
        proc = subprocess.Popen(
            cmd,
            cwd=str(cwd_path),
            stdin=subprocess.DEVNULL,
            stdout=lf,
            stderr=subprocess.STDOUT,
            start_new_session=False,
        )
        exit_code = proc.wait()
        duration = round(time.monotonic() - start_mono, 3)
        lf.write(f"[CONTROLLER] finished_at={now_iso()} exit_code={exit_code} duration={duration}s\n")

    meta = json_load(mpath)
    meta["exit_code"] = int(exit_code)
    meta["finished_at"] = now_iso()
    if meta.get("stop_requested_at"):
        meta["status"] = "stopped"
    else:
        meta["status"] = "success" if exit_code == 0 else "failed"
    write_meta(state_dir, run_id, meta)
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    state_dir = normalize_state_dir(args.state_dir)
    run_id = ensure_run_id(args.run_id)
    meta = read_meta(state_dir, run_id)
    pid = int(meta.get("pid") or 0)
    alive = pid_is_alive(pid)
    current_status = meta.get("status", "unknown")
    if current_status in {"starting", "running", "stopping"} and not alive and meta.get("exit_code") is None:
        if current_status == "stopping" or meta.get("stop_requested_at"):
            current_status = "stopped"
        else:
            current_status = "unknown_stopped"
        # Heal stale status to avoid a dead process remaining in active status.
        meta["status"] = current_status
        if not meta.get("finished_at"):
            meta["finished_at"] = now_iso()
        write_meta(state_dir, run_id, meta)

    payload: Dict[str, Any] = {
        "ok": True,
        "action": "status",
        "run_id": run_id,
        "status": current_status,
        "pid": pid,
        "pid_alive": alive,
        "created_at": meta.get("created_at", ""),
        "started_at": meta.get("started_at", ""),
        "finished_at": meta.get("finished_at", ""),
        "exit_code": meta.get("exit_code"),
        "log_file": meta.get("log_file", ""),
        "summary_file": meta.get("summary_file", ""),
        "stop_requested_at": meta.get("stop_requested_at", ""),
    }
    spath = Path(meta.get("summary_file", ""))
    if spath.exists():
        try:
            payload["summary"] = json_load(spath)
        except Exception as exc:  # pragma: no cover
            payload["summary_error"] = str(exc)

    emit(payload, args.json)
    return 0


def cmd_stop(args: argparse.Namespace) -> int:
    state_dir = normalize_state_dir(args.state_dir)
    run_id = ensure_run_id(args.run_id)
    meta = read_meta(state_dir, run_id)
    pid = int(meta.get("pid") or 0)

    if meta.get("status") not in {"running", "starting"} and not pid_is_alive(pid):
        payload = {
            "ok": True,
            "action": "stop",
            "run_id": run_id,
            "status": meta.get("status", "unknown"),
            "message": "浠诲姟宸茬粨鏉燂紝鏃犻渶鍋滄",
        }
        emit(payload, args.json)
        return 0

    meta["stop_requested_at"] = now_iso()
    if meta.get("status") in {"running", "starting"}:
        meta["status"] = "stopping"
    write_meta(state_dir, run_id, meta)

    stop_result = stop_process_group(pid, timeout_sec=args.timeout_sec)
    payload = {
        "ok": True,
        "action": "stop",
        "run_id": run_id,
        "pid": pid,
        "sent": stop_result["sent"],
        "final_signal": stop_result["signal"],
        "forced_kill": stop_result["killed"],
    }
    emit(payload, args.json)
    return 0


def cmd_logs(args: argparse.Namespace) -> int:
    state_dir = normalize_state_dir(args.state_dir)
    run_id = ensure_run_id(args.run_id)
    meta = read_meta(state_dir, run_id)
    lpath = Path(meta.get("log_file", ""))
    lines = tail_lines(lpath, args.lines)

    if args.json:
        payload = {
            "ok": True,
            "action": "logs",
            "run_id": run_id,
            "log_file": str(lpath),
            "lines": [line.rstrip("\n") for line in lines],
        }
        emit(payload, True)
        return 0

    for line in lines:
        print(line, end="")
    return 0


def cmd_list(args: argparse.Namespace) -> int:
    state_dir = normalize_state_dir(args.state_dir)
    root = runs_root(state_dir)
    root.mkdir(parents=True, exist_ok=True)

    records: List[Dict[str, Any]] = []
    for entry in root.iterdir():
        if not entry.is_dir():
            continue
        mpath = entry / META_FILE_NAME
        if not mpath.exists():
            continue
        try:
            meta = json_load(mpath)
        except Exception:
            continue
        records.append(
            {
                "run_id": meta.get("run_id", entry.name),
                "label": meta.get("label", ""),
                "status": meta.get("status", "unknown"),
                "created_at": meta.get("created_at", ""),
                "started_at": meta.get("started_at", ""),
                "finished_at": meta.get("finished_at", ""),
                "exit_code": meta.get("exit_code"),
            }
        )

    records.sort(key=lambda x: x.get("created_at", ""), reverse=True)
    if args.limit > 0:
        records = records[: args.limit]

    if args.json:
        emit({"ok": True, "action": "list", "runs": records}, True)
        return 0

    for item in records:
        print(
            f"{item['run_id']}  status={item['status']}  exit_code={item['exit_code']}  "
            f"created_at={item['created_at']}"
        )
    return 0


def cmd_cron_template(args: argparse.Namespace) -> int:
    controller_path = Path(__file__).resolve()
    config_path = Path(args.config).expanduser().resolve()
    state_dir = normalize_state_dir(args.state_dir)
    expr = args.cron_expr.strip()
    label = args.label.strip() if args.label else "cron"
    cron_line = (
        f"{expr} python3 {controller_path} start -c {config_path} "
        f"--state-dir {state_dir} --label {label} --if-busy skip --json >> {state_dir}/cron.log 2>&1"
    )

    payload = {
        "ok": True,
        "action": "cron-template",
        "cron": cron_line,
    }
    emit(payload, args.json)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="CFST job controller (web friendly)")
    sub = parser.add_subparsers(dest="command", required=True)

    p_start = sub.add_parser("start", help="Start a background job")
    p_start.add_argument("-c", "--config", required=True, help="Path to config file")
    p_start.add_argument(
        "--runner",
        default="scripts/cfst/cfst_config_runner.py",
        help="Path to runner script",
    )
    p_start.add_argument("--state-dir", default=DEFAULT_STATE_DIR, help="State directory")
    p_start.add_argument("--cwd", default=".", help="Working directory")
    p_start.add_argument("--run-id", default="", help="Optional run id")
    p_start.add_argument("--label", default="", help="Optional run label")
    p_start.add_argument(
        "--if-busy",
        default="fail",
        choices=["fail", "skip"],
        help="Behavior when an active run exists: fail or skip",
    )
    p_start.add_argument("--json", action="store_true", help="Emit JSON payload")
    p_start.set_defaults(func=cmd_start)

    p_status = sub.add_parser("status", help="Show run status")
    p_status.add_argument("--state-dir", default=DEFAULT_STATE_DIR, help="State directory")
    p_status.add_argument("--run-id", required=True, help="Run id")
    p_status.add_argument("--json", action="store_true", help="Emit JSON payload")
    p_status.set_defaults(func=cmd_status)

    p_stop = sub.add_parser("stop", help="Stop a run")
    p_stop.add_argument("--state-dir", default=DEFAULT_STATE_DIR, help="State directory")
    p_stop.add_argument("--run-id", required=True, help="Run id")
    p_stop.add_argument("--timeout-sec", type=int, default=8, help="Force-kill timeout seconds")
    p_stop.add_argument("--json", action="store_true", help="Emit JSON payload")
    p_stop.set_defaults(func=cmd_stop)

    p_logs = sub.add_parser("logs", help="Show log tail")
    p_logs.add_argument("--state-dir", default=DEFAULT_STATE_DIR, help="State directory")
    p_logs.add_argument("--run-id", required=True, help="Run id")
    p_logs.add_argument("--lines", type=int, default=120, help="Tail line count")
    p_logs.add_argument("--json", action="store_true", help="Emit JSON payload")
    p_logs.set_defaults(func=cmd_logs)

    p_list = sub.add_parser("list", help="List runs")
    p_list.add_argument("--state-dir", default=DEFAULT_STATE_DIR, help="State directory")
    p_list.add_argument("--limit", type=int, default=20, help="Max records")
    p_list.add_argument("--json", action="store_true", help="Emit JSON payload")
    p_list.set_defaults(func=cmd_list)

    p_cron = sub.add_parser("cron-template", help="Generate crontab template")
    p_cron.add_argument("-c", "--config", required=True, help="Path to config file")
    p_cron.add_argument("--state-dir", default=DEFAULT_STATE_DIR, help="State directory")
    p_cron.add_argument(
        "--cron-expr",
        default="*/30 * * * *",
        help="Cron expression, e.g. */30 * * * *",
    )
    p_cron.add_argument("--label", default="cron", help="Run label")
    p_cron.add_argument("--json", action="store_true", help="Emit JSON payload")
    p_cron.set_defaults(func=cmd_cron_template)

    p_worker = sub.add_parser("_worker", help=argparse.SUPPRESS)
    p_worker.add_argument("--state-dir", required=True)
    p_worker.add_argument("--run-id", required=True)
    p_worker.add_argument("--config", required=True)
    p_worker.add_argument("--runner", required=True)
    p_worker.add_argument("--cwd", required=True)
    p_worker.set_defaults(func=cmd_worker)

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    try:
        return int(args.func(args))
    except Exception as exc:
        if getattr(args, "json", False):
            emit({"ok": False, "error": str(exc)}, True)
            return 1
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())



