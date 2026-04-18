#!/usr/bin/env python
from __future__ import annotations

import argparse
import hmac
import json
import os
import secrets
import subprocess
import sys
import threading
import time
import urllib.parse
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from http import HTTPStatus
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from phase2_common import (
    EnvelopeError,
    append_jsonl,
    atomic_write_json,
    envelope,
    new_trace_id,
    now_iso,
    parse_env_file,
    tail_jsonl,
    update_env_file,
)


def code_to_http(code: str, ok: bool) -> int:
    if ok:
        return 200
    mapping = {
        "LOCKED": 409,
        "CONFIG_INVALID": 400,
        "EXEC_TIMEOUT": 504,
        "RESULT_NOT_FOUND": 424,
        "CF_API_429": 429,
        "CF_API_5XX": 502,
        "CF_API_4XX": 502,
        "DNS_RECORD_NOT_FOUND": 404,
        "DNS_VERIFY_FAILED": 502,
        "STATE_WRITE_ERROR": 500,
        "UNKNOWN_ERROR": 500,
    }
    return mapping.get(code, 500)


def safe_json_loads(text: str) -> Optional[Dict[str, Any]]:
    try:
        obj = json.loads(text)
        return obj if isinstance(obj, dict) else None
    except json.JSONDecodeError:
        return None


def parse_subprocess_json(stdout: str) -> Optional[Dict[str, Any]]:
    lines = [line.strip() for line in stdout.splitlines() if line.strip()]
    for line in reversed(lines):
        obj = safe_json_loads(line)
        if obj and {"ok", "code", "message", "data", "ts", "traceId"}.issubset(set(obj.keys())):
            return obj
    return None


def tail_lines(path: Path, limit: int) -> List[str]:
    if not path.exists() or limit <= 0:
        return []
    return path.read_text(encoding="utf-8", errors="replace").splitlines()[-limit:]


@dataclass
class Session:
    sid: str
    user: str
    csrf: str
    expire_at: datetime


@dataclass
class JobConfig:
    enabled: bool = False
    interval_minutes: int = 20
    next_run_at: str = ""
    last_run_at: str = ""
    last_code: str = ""
    running: bool = False


@dataclass
class AppConfig:
    root: Path
    collector_config: Path
    ddns_config: Path
    webui_config: Path
    collector_script: Path
    ddns_script: Path
    webui_dir: Path
    data_dir: Path
    logs_dir: Path
    scheduler_state_file: Path
    audit_log_file: Path
    api_log_file: Path
    bind_host: str
    bind_port: int
    admin_username: str
    admin_password: str
    session_hours: int
    bash_bin: str


class SchedulerManager:
    def __init__(self, state_file: Path, run_job: Callable[[str], Dict[str, Any]], log_fn: Callable[[str, Dict[str, Any]], None]):
        self.state_file = state_file
        self.run_job = run_job
        self.log_fn = log_fn
        self.lock = threading.RLock()
        self.stop_event = threading.Event()
        self.jobs: Dict[str, JobConfig] = {
            "collector_job": JobConfig(enabled=False, interval_minutes=20),
            "ddns_sync_job": JobConfig(enabled=False, interval_minutes=20),
        }
        self._load_state()
        self.thread = threading.Thread(target=self._loop, daemon=True, name="scheduler")
        self.thread.start()

    def _load_state(self) -> None:
        if not self.state_file.exists():
            self._persist_state()
            return
        try:
            data = json.loads(self.state_file.read_text(encoding="utf-8"))
            for name, job in self.jobs.items():
                raw = data.get(name) or {}
                job.enabled = bool(raw.get("enabled", job.enabled))
                job.interval_minutes = int(raw.get("intervalMinutes", job.interval_minutes))
                job.next_run_at = str(raw.get("nextRunAt", ""))
                job.last_run_at = str(raw.get("lastRunAt", ""))
                job.last_code = str(raw.get("lastCode", ""))
        except Exception:
            self._persist_state()

    def _persist_state(self) -> None:
        payload = {
            name: {
                "enabled": job.enabled,
                "intervalMinutes": job.interval_minutes,
                "nextRunAt": job.next_run_at,
                "lastRunAt": job.last_run_at,
                "lastCode": job.last_code,
            }
            for name, job in self.jobs.items()
        }
        atomic_write_json(self.state_file, payload)

    def _calc_next(self, minutes: int) -> str:
        return (datetime.now(timezone.utc).astimezone() + timedelta(minutes=minutes)).isoformat(timespec="seconds")

    def _due(self, run_at: str) -> bool:
        if not run_at:
            return True
        try:
            target = datetime.fromisoformat(run_at)
        except ValueError:
            return True
        return datetime.now(target.tzinfo) >= target

    def _execute(self, name: str) -> None:
        with self.lock:
            job = self.jobs[name]
            if job.running:
                return
            job.running = True
        try:
            result = self.run_job(name)
            code = result.get("code", "UNKNOWN_ERROR")
            self.log_fn(f"scheduler.{name}", result)
        except Exception as err:
            code = "UNKNOWN_ERROR"
            self.log_fn(f"scheduler.{name}", {"ok": False, "code": code, "message": str(err), "data": {}})
        with self.lock:
            job = self.jobs[name]
            job.running = False
            job.last_run_at = now_iso()
            job.last_code = code
            job.next_run_at = self._calc_next(job.interval_minutes) if job.enabled else ""
            self._persist_state()

    def _loop(self) -> None:
        while not self.stop_event.wait(1.0):
            due_jobs: List[str] = []
            with self.lock:
                for name, job in self.jobs.items():
                    if not job.enabled or job.running:
                        continue
                    if self._due(job.next_run_at):
                        due_jobs.append(name)
            for name in due_jobs:
                threading.Thread(target=self._execute, args=(name,), daemon=True, name=f"job-{name}").start()

    def shutdown(self) -> None:
        self.stop_event.set()
        self.thread.join(timeout=2)

    def enable(self, name: str, interval_minutes: int) -> Dict[str, Any]:
        if interval_minutes < 1:
            raise EnvelopeError("CONFIG_INVALID", "intervalMinutes must be >= 1", 400)
        with self.lock:
            job = self.jobs[name]
            job.enabled = True
            job.interval_minutes = interval_minutes
            job.next_run_at = self._calc_next(interval_minutes)
            self._persist_state()
            return self.status()[name]

    def pause(self, name: str) -> Dict[str, Any]:
        with self.lock:
            job = self.jobs[name]
            job.enabled = False
            job.next_run_at = ""
            self._persist_state()
            return self.status()[name]

    def status(self) -> Dict[str, Any]:
        with self.lock:
            return {
                name: {
                    "enabled": job.enabled,
                    "intervalMinutes": job.interval_minutes,
                    "nextRunAt": job.next_run_at,
                    "lastRunAt": job.last_run_at,
                    "lastCode": job.last_code,
                    "running": job.running,
                }
                for name, job in self.jobs.items()
            }


class AppState:
    def __init__(self, cfg: AppConfig):
        self.cfg = cfg
        self.sessions: Dict[str, Session] = {}
        self.sessions_lock = threading.Lock()
        self.metrics_lock = threading.Lock()
        self.metrics: Dict[str, int] = {
            "collector_run_total": 0,
            "collector_run_success_total": 0,
            "collector_run_failed_total": 0,
            "ddns_sync_total": 0,
            "ddns_sync_success_total": 0,
            "ddns_sync_failed_total": 0,
            "ddns_noop_total": 0,
            "audit_event_total": 0,
        }
        self.scheduler = SchedulerManager(
            state_file=cfg.scheduler_state_file,
            run_job=self._run_scheduled_job,
            log_fn=self._audit,
        )

    def close(self) -> None:
        self.scheduler.shutdown()

    def _log_api(self, message: str, extra: Optional[Dict[str, Any]] = None) -> None:
        self.cfg.api_log_file.parent.mkdir(parents=True, exist_ok=True)
        line = {
            "ts": now_iso(),
            "message": message,
            "extra": extra or {},
        }
        with self.cfg.api_log_file.open("a", encoding="utf-8") as fp:
            fp.write(json.dumps(line, ensure_ascii=False) + "\n")

    def _audit(self, action: str, result: Dict[str, Any], user: str = "system", trace_id: str = "") -> None:
        item = {
            "ts": now_iso(),
            "user": user,
            "action": action,
            "result": "success" if result.get("ok") else "failed",
            "code": result.get("code", "UNKNOWN_ERROR"),
            "traceId": trace_id or result.get("traceId", ""),
        }
        append_jsonl(self.cfg.audit_log_file, item)
        with self.metrics_lock:
            self.metrics["audit_event_total"] += 1

    def _run_command(self, cmd: List[str], env: Optional[Dict[str, str]] = None, timeout: int = 180) -> Dict[str, Any]:
        merged_env = os.environ.copy()
        if env:
            merged_env.update(env)
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, env=merged_env)
        except subprocess.TimeoutExpired as exc:
            return envelope(
                False,
                "EXEC_TIMEOUT",
                f"subprocess execution timed out after {timeout} seconds",
                {"command": cmd, "timeoutSec": timeout, "stdout": (exc.stdout or "")[-1000:], "stderr": (exc.stderr or "")[-1000:]},
                new_trace_id("api"),
            )
        parsed = parse_subprocess_json(proc.stdout)
        if parsed:
            return parsed
        err_data = {
            "command": cmd,
            "exitCode": proc.returncode,
            "stdout": proc.stdout[-2000:],
            "stderr": proc.stderr[-2000:],
        }
        return envelope(False, "UNKNOWN_ERROR", "subprocess output is not valid envelope", err_data, new_trace_id("api"))

    def _collector_cmd(self, args: List[str]) -> Dict[str, Any]:
        script = self.cfg.collector_script
        if script.suffix == ".sh":
            cmd = [self.cfg.bash_bin, str(script), *args]
        elif script.suffix == ".py":
            cmd = [sys.executable, str(script), *args]
        else:
            cmd = [str(script), *args]
        timeout_sec = 1200 if "run-once" in args else 120
        return self._run_command(cmd, env={"CONFIG_FILE": str(self.cfg.collector_config)}, timeout=timeout_sec)

    def _ddns_cmd(self, args: List[str]) -> Dict[str, Any]:
        cmd = [sys.executable, str(self.cfg.ddns_script), *args, "--collector-config", str(self.cfg.collector_config), "--ddns-config", str(self.cfg.ddns_config)]
        return self._run_command(cmd)

    def _run_scheduled_job(self, job_name: str) -> Dict[str, Any]:
        if job_name == "collector_job":
            out = self.collector_run("scheduler")
        elif job_name == "ddns_sync_job":
            out = self.ddns_sync("scheduler")
        else:
            out = envelope(False, "UNKNOWN_ERROR", f"unknown job: {job_name}", {}, new_trace_id("api"))
        return out

    def _track_metrics(self, kind: str, result: Dict[str, Any]) -> None:
        ok = bool(result.get("ok"))
        code = result.get("code", "")
        with self.metrics_lock:
            if kind == "collector":
                self.metrics["collector_run_total"] += 1
                self.metrics["collector_run_success_total" if ok else "collector_run_failed_total"] += 1
            elif kind == "ddns":
                self.metrics["ddns_sync_total"] += 1
                self.metrics["ddns_sync_success_total" if ok else "ddns_sync_failed_total"] += 1
                if code == "DDNS_NOOP":
                    self.metrics["ddns_noop_total"] += 1

    def collector_status(self) -> Dict[str, Any]:
        return self._collector_cmd(["status", "--json"])

    def collector_run(self, user: str) -> Dict[str, Any]:
        out = self._collector_cmd(["run-once", "--json"])
        self._track_metrics("collector", out)
        self._audit("collector.run", out, user=user)
        return out

    def collector_history(self, limit: int) -> Dict[str, Any]:
        return self._collector_cmd(["history", "--limit", str(limit), "--json"])

    def collector_validate(self) -> Dict[str, Any]:
        return self._collector_cmd(["validate-config", "--json"])

    def ddns_status(self) -> Dict[str, Any]:
        return self._ddns_cmd(["status", "--json"])

    def ddns_sync(self, user: str) -> Dict[str, Any]:
        out = self._ddns_cmd(["sync", "--json"])
        self._track_metrics("ddns", out)
        self._audit("ddns.sync", out, user=user)
        return out

    def ddns_history(self, limit: int) -> Dict[str, Any]:
        return self._ddns_cmd(["history", "--limit", str(limit), "--json"])

    def ddns_validate(self) -> Dict[str, Any]:
        return self._ddns_cmd(["validate-config", "--json"])

    def ddns_rollback(self, user: str, target_ip: str) -> Dict[str, Any]:
        out = self._ddns_cmd(["rollback", "--ip", target_ip, "--json"])
        self._track_metrics("ddns", out)
        self._audit("ddns.rollback", out, user=user)
        return out

    def metrics_snapshot(self) -> Dict[str, int]:
        with self.metrics_lock:
            return dict(self.metrics)

    def save_collector_config(self, updates: Dict[str, str], user: str) -> Dict[str, Any]:
        update_env_file(self.cfg.collector_config, updates)
        out = self.collector_validate()
        self._audit("collector.config.save", out, user=user)
        return out

    def save_ddns_config(self, updates: Dict[str, str], user: str) -> Dict[str, Any]:
        update_env_file(self.cfg.ddns_config, updates)
        out = self.ddns_validate()
        self._audit("ddns.config.save", out, user=user)
        return out

    def login(self, username: str, password: str) -> Session:
        if not (hmac.compare_digest(username, self.cfg.admin_username) and hmac.compare_digest(password, self.cfg.admin_password)):
            raise EnvelopeError("CONFIG_INVALID", "invalid username or password", 401)
        sid = secrets.token_urlsafe(24)
        csrf = secrets.token_urlsafe(24)
        expire_at = datetime.now(timezone.utc) + timedelta(hours=self.cfg.session_hours)
        session = Session(sid=sid, user=username, csrf=csrf, expire_at=expire_at)
        with self.sessions_lock:
            self.sessions[sid] = session
        return session

    def get_session(self, sid: str) -> Optional[Session]:
        if not sid:
            return None
        with self.sessions_lock:
            s = self.sessions.get(sid)
            if not s:
                return None
            if datetime.now(timezone.utc) >= s.expire_at:
                self.sessions.pop(sid, None)
                return None
            return s

    def logout(self, sid: str) -> None:
        with self.sessions_lock:
            self.sessions.pop(sid, None)


class ApiHandler(BaseHTTPRequestHandler):
    server: "CoitdServer"

    def log_message(self, fmt: str, *args: Any) -> None:
        self.server.state._log_api(fmt % args)

    def _trace(self) -> str:
        incoming = self.headers.get("X-Trace-Id", "").strip()
        return incoming or new_trace_id("api")

    def _json_body(self) -> Dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0") or "0")
        if length <= 0:
            return {}
        raw = self.rfile.read(length).decode("utf-8", errors="replace")
        if not raw.strip():
            return {}
        data = json.loads(raw)
        if not isinstance(data, dict):
            raise EnvelopeError("CONFIG_INVALID", "request body must be json object", 400)
        return data

    def _write(self, status: int, payload: Dict[str, Any], headers: Optional[Dict[str, str]] = None) -> None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        if headers:
            for key, value in headers.items():
                self.send_header(key, value)
        self.end_headers()
        self.wfile.write(data)

    def _write_file(self, path: Path, content_type: str) -> None:
        raw = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def _cookie_sid(self) -> str:
        raw = self.headers.get("Cookie", "")
        cookie = SimpleCookie()
        cookie.load(raw)
        node = cookie.get("coitd_sid")
        return node.value if node else ""

    def _require_session(self, write: bool, trace_id: str) -> Session:
        sid = self._cookie_sid()
        session = self.server.state.get_session(sid)
        if not session:
            raise EnvelopeError("CONFIG_INVALID", "authentication required", 401)
        if write:
            csrf = self.headers.get("X-CSRF-Token", "")
            if not hmac.compare_digest(csrf, session.csrf):
                raise EnvelopeError("CONFIG_INVALID", "csrf token invalid", 403)
        return session

    def do_GET(self) -> None:
        trace_id = self._trace()
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        try:
            if path == "/":
                return self._write_file(self.server.state.cfg.webui_dir / "index.html", "text/html; charset=utf-8")
            if path.startswith("/assets/"):
                target = (self.server.state.cfg.webui_dir / path.lstrip("/")).resolve()
                if self.server.state.cfg.webui_dir.resolve() not in target.parents:
                    self.send_error(404)
                    return
                if not target.exists():
                    self.send_error(404)
                    return
                ctype = "application/javascript; charset=utf-8" if target.suffix == ".js" else "text/css; charset=utf-8"
                return self._write_file(target, ctype)

            if path == "/api/v1/system/healthz":
                data = {
                    "status": "ok",
                    "scheduler": self.server.state.scheduler.status(),
                    "time": now_iso(),
                }
                payload = envelope(True, "OK", "success", data, trace_id)
                return self._write(200, payload)

            session = self._require_session(write=False, trace_id=trace_id)

            if path == "/api/v1/auth/me":
                data = {
                    "user": session.user,
                    "csrfToken": session.csrf,
                    "sessionExpireAt": session.expire_at.isoformat(timespec="seconds"),
                }
                payload = envelope(True, "OK", "success", data, trace_id)
                return self._write(200, payload)

            if path == "/api/v1/collector/status":
                out = self.server.state.collector_status()
                return self._write(code_to_http(out["code"], out["ok"]), out)
            if path == "/api/v1/collector/history":
                q = urllib.parse.parse_qs(parsed.query)
                limit = int((q.get("limit") or ["50"])[0])
                out = self.server.state.collector_history(limit)
                return self._write(code_to_http(out["code"], out["ok"]), out)

            if path == "/api/v1/ddns/status":
                out = self.server.state.ddns_status()
                return self._write(code_to_http(out["code"], out["ok"]), out)
            if path == "/api/v1/ddns/history":
                q = urllib.parse.parse_qs(parsed.query)
                limit = int((q.get("limit") or ["50"])[0])
                out = self.server.state.ddns_history(limit)
                return self._write(code_to_http(out["code"], out["ok"]), out)

            if path == "/api/v1/system/metrics":
                data = {"metrics": self.server.state.metrics_snapshot()}
                payload = envelope(True, "OK", "success", data, trace_id)
                return self._write(200, payload)

            if path == "/api/v1/system/logs":
                q = urllib.parse.parse_qs(parsed.query)
                name = (q.get("name") or ["app"])[0]
                tail = int((q.get("tail") or ["200"])[0])
                path_map = {
                    "app": self.server.state.cfg.logs_dir / "app.log",
                    "cfst": self.server.state.cfg.logs_dir / "app.log.cfst",
                    "api": self.server.state.cfg.api_log_file,
                    "audit": self.server.state.cfg.audit_log_file,
                }
                target = path_map.get(name)
                if not target:
                    raise EnvelopeError("CONFIG_INVALID", "unknown log name", 400)
                lines = tail_lines(target, max(1, min(tail, 2000)))
                payload = envelope(True, "OK", "success", {"name": name, "tail": len(lines), "lines": lines}, trace_id)
                return self._write(200, payload)

            if path == "/api/v1/system/audit":
                q = urllib.parse.parse_qs(parsed.query)
                limit = int((q.get("limit") or ["100"])[0])
                items = tail_jsonl(self.server.state.cfg.audit_log_file, max(1, min(limit, 1000)))
                payload = envelope(True, "OK", "success", {"limit": limit, "count": len(items), "items": items}, trace_id)
                return self._write(200, payload)

            payload = envelope(False, "CONFIG_INVALID", "route not found", {"path": path}, trace_id)
            return self._write(404, payload)

        except EnvelopeError as err:
            payload = envelope(False, err.code, err.message, err.data, trace_id)
            return self._write(err.http_status, payload)
        except Exception as err:
            payload = envelope(False, "UNKNOWN_ERROR", str(err), {}, trace_id)
            return self._write(500, payload)

    def do_POST(self) -> None:
        trace_id = self._trace()
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        try:
            if path == "/api/v1/auth/login":
                body = self._json_body()
                username = str(body.get("username", ""))
                password = str(body.get("password", ""))
                session = self.server.state.login(username, password)
                payload = envelope(
                    True,
                    "OK",
                    "success",
                    {
                        "user": session.user,
                        "csrfToken": session.csrf,
                        "sessionExpireAt": session.expire_at.isoformat(timespec="seconds"),
                    },
                    trace_id,
                )
                headers = {
                    "Set-Cookie": f"coitd_sid={session.sid}; HttpOnly; SameSite=Lax; Path=/",
                }
                return self._write(200, payload, headers=headers)

            if path == "/api/v1/auth/logout":
                sid = self._cookie_sid()
                self.server.state.logout(sid)
                payload = envelope(True, "OK", "success", {}, trace_id)
                headers = {"Set-Cookie": "coitd_sid=; Max-Age=0; Path=/; HttpOnly; SameSite=Lax"}
                return self._write(200, payload, headers=headers)

            session = self._require_session(write=True, trace_id=trace_id)

            if path == "/api/v1/collector/run":
                out = self.server.state.collector_run(session.user)
                return self._write(code_to_http(out["code"], out["ok"]), out)
            if path == "/api/v1/collector/config/validate":
                out = self.server.state.collector_validate()
                return self._write(code_to_http(out["code"], out["ok"]), out)
            if path == "/api/v1/collector/config/save":
                body = self._json_body()
                updates = body.get("updates") or {}
                if not isinstance(updates, dict):
                    raise EnvelopeError("CONFIG_INVALID", "updates must be object", 400)
                safe_updates = {str(k): str(v) for k, v in updates.items()}
                out = self.server.state.save_collector_config(safe_updates, session.user)
                return self._write(code_to_http(out["code"], out["ok"]), out)
            if path == "/api/v1/collector/schedule/enable":
                body = self._json_body()
                interval = int(body.get("intervalMinutes", 20))
                data = self.server.state.scheduler.enable("collector_job", interval)
                payload = envelope(True, "OK", "success", data, trace_id)
                self.server.state._audit("collector.schedule.enable", payload, user=session.user, trace_id=trace_id)
                return self._write(200, payload)
            if path == "/api/v1/collector/schedule/pause":
                data = self.server.state.scheduler.pause("collector_job")
                payload = envelope(True, "OK", "success", data, trace_id)
                self.server.state._audit("collector.schedule.pause", payload, user=session.user, trace_id=trace_id)
                return self._write(200, payload)

            if path == "/api/v1/ddns/sync":
                out = self.server.state.ddns_sync(session.user)
                return self._write(code_to_http(out["code"], out["ok"]), out)
            if path == "/api/v1/ddns/rollback":
                body = self._json_body()
                ip = str(body.get("targetIp", "")).strip()
                if not ip:
                    raise EnvelopeError("CONFIG_INVALID", "targetIp is required", 400)
                out = self.server.state.ddns_rollback(session.user, ip)
                return self._write(code_to_http(out["code"], out["ok"]), out)
            if path == "/api/v1/ddns/config/validate":
                out = self.server.state.ddns_validate()
                return self._write(code_to_http(out["code"], out["ok"]), out)
            if path == "/api/v1/ddns/config/save":
                body = self._json_body()
                updates = body.get("updates") or {}
                if not isinstance(updates, dict):
                    raise EnvelopeError("CONFIG_INVALID", "updates must be object", 400)
                safe_updates = {str(k): str(v) for k, v in updates.items()}
                out = self.server.state.save_ddns_config(safe_updates, session.user)
                return self._write(code_to_http(out["code"], out["ok"]), out)
            if path == "/api/v1/ddns/schedule/enable":
                body = self._json_body()
                interval = int(body.get("intervalMinutes", 20))
                data = self.server.state.scheduler.enable("ddns_sync_job", interval)
                payload = envelope(True, "OK", "success", data, trace_id)
                self.server.state._audit("ddns.schedule.enable", payload, user=session.user, trace_id=trace_id)
                return self._write(200, payload)
            if path == "/api/v1/ddns/schedule/pause":
                data = self.server.state.scheduler.pause("ddns_sync_job")
                payload = envelope(True, "OK", "success", data, trace_id)
                self.server.state._audit("ddns.schedule.pause", payload, user=session.user, trace_id=trace_id)
                return self._write(200, payload)

            payload = envelope(False, "CONFIG_INVALID", "route not found", {"path": path}, trace_id)
            return self._write(404, payload)

        except EnvelopeError as err:
            payload = envelope(False, err.code, err.message, err.data, trace_id)
            return self._write(err.http_status, payload)
        except Exception as err:
            payload = envelope(False, "UNKNOWN_ERROR", str(err), {}, trace_id)
            return self._write(500, payload)


class CoitdServer(ThreadingHTTPServer):
    def __init__(self, server_address: Tuple[str, int], RequestHandlerClass: type[BaseHTTPRequestHandler], state: AppState):
        super().__init__(server_address, RequestHandlerClass)
        self.state = state


def build_config(root: Path) -> AppConfig:
    collector_cfg = Path(os.environ.get("COLLECTOR_CONFIG_FILE", "config/collector.env"))
    ddns_cfg = Path(os.environ.get("DDNS_CONFIG_FILE", "config/ddns.env"))
    webui_cfg = Path(os.environ.get("WEBUI_CONFIG_FILE", "config/webui.env"))
    collector_script = Path(os.environ.get("COLLECTOR_SCRIPT", "scripts/run_once.sh"))
    ddns_script = Path(os.environ.get("DDNS_SCRIPT", "scripts/sync_ddns.py"))
    webui_dir = Path(os.environ.get("WEBUI_DIR", "webui"))

    if not collector_cfg.is_absolute():
        collector_cfg = (root / collector_cfg).resolve()
    if not ddns_cfg.is_absolute():
        ddns_cfg = (root / ddns_cfg).resolve()
    if not webui_cfg.is_absolute():
        webui_cfg = (root / webui_cfg).resolve()
    if not collector_script.is_absolute():
        collector_script = (root / collector_script).resolve()
    if not ddns_script.is_absolute():
        ddns_script = (root / ddns_script).resolve()
    if not webui_dir.is_absolute():
        webui_dir = (root / webui_dir).resolve()

    webui_env = parse_env_file(webui_cfg) if webui_cfg.exists() else {}

    data_dir = root / "data"
    logs_dir = root / "logs"

    def _resolve_path(raw: Any, default: Path) -> Path:
        if raw is None or str(raw).strip() == "":
            return default
        p = Path(str(raw).strip())
        if not p.is_absolute():
            p = (root / p).resolve()
        return p

    return AppConfig(
        root=root,
        collector_config=collector_cfg,
        ddns_config=ddns_cfg,
        webui_config=webui_cfg,
        collector_script=collector_script,
        ddns_script=ddns_script,
        webui_dir=webui_dir,
        data_dir=data_dir,
        logs_dir=logs_dir,
        scheduler_state_file=_resolve_path(webui_env.get("SCHEDULER_STATE_FILE"), data_dir / "scheduler_state.json"),
        audit_log_file=_resolve_path(webui_env.get("AUDIT_LOG_FILE"), data_dir / "audit_log.jsonl"),
        api_log_file=_resolve_path(webui_env.get("API_LOG_FILE"), logs_dir / "control_api.log"),
        bind_host=webui_env.get("BIND_HOST", "127.0.0.1"),
        bind_port=int(webui_env.get("BIND_PORT", "18080")),
        admin_username=webui_env.get("ADMIN_USERNAME", "admin"),
        admin_password=webui_env.get("ADMIN_PASSWORD", "admin123456"),
        session_hours=int(webui_env.get("SESSION_HOURS", "12")),
        bash_bin=webui_env.get("BASH_BIN", "bash"),
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="")
    parser.add_argument("--port", type=int, default=0)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    root = Path(__file__).resolve().parent.parent
    cfg = build_config(root)
    host = args.host or cfg.bind_host
    port = args.port or cfg.bind_port

    state = AppState(cfg)
    server = CoitdServer((host, port), ApiHandler, state)

    print(json.dumps({"ok": True, "code": "OK", "message": "control-api started", "data": {"host": host, "port": port}, "ts": now_iso(), "traceId": new_trace_id("api")}))
    try:
        server.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        pass
    finally:
        state.close()
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
