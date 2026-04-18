from __future__ import annotations

import hashlib
import ipaddress
import json
import os
import random
import re
import string
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple


class EnvelopeError(Exception):
    def __init__(self, code: str, message: str, http_status: int = 500, exit_code: int = 16, data: Optional[Dict[str, Any]] = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.http_status = http_status
        self.exit_code = exit_code
        self.data = data or {}


EXIT_CODES = {
    "OK": 0,
    "LOCKED": 10,
    "CONFIG_INVALID": 11,
    "EXEC_TIMEOUT": 12,
    "RESULT_NOT_FOUND": 13,
    "RESULT_PARSE_ERROR": 14,
    "STATE_WRITE_ERROR": 15,
    "UNKNOWN_ERROR": 16,
}


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def new_trace_id(prefix: str) -> str:
    stamp = datetime.now().strftime("%Y%m%d%H%M%S")
    suffix = "".join(random.choice(string.ascii_lowercase + string.digits) for _ in range(4))
    return f"{prefix}-{stamp}-{suffix}"


def envelope(ok: bool, code: str, message: str, data: Optional[Dict[str, Any]], trace_id: str) -> Dict[str, Any]:
    return {
        "ok": ok,
        "code": code,
        "message": message,
        "data": data or {},
        "ts": now_iso(),
        "traceId": trace_id,
    }


def atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + f".tmp.{os.getpid()}")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def atomic_write_json(path: Path, payload: Dict[str, Any]) -> None:
    atomic_write_text(path, json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n")


def append_jsonl(path: Path, item: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(item, ensure_ascii=False, separators=(",", ":")) + "\n"
    with path.open("a", encoding="utf-8") as fp:
        fp.write(line)


def tail_jsonl(path: Path, limit: int) -> List[Dict[str, Any]]:
    if not path.exists() or limit <= 0:
        return []
    lines = path.read_text(encoding="utf-8").splitlines()
    out: List[Dict[str, Any]] = []
    for line in lines[-limit:]:
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


def trim_jsonl(path: Path, max_lines: int) -> Tuple[int, int]:
    if not path.exists() or max_lines <= 0:
        return (0, 0)
    lines = path.read_text(encoding="utf-8").splitlines()
    before = len(lines)
    if before <= max_lines:
        return (before, before)
    kept = lines[-max_lines:]
    atomic_write_text(path, "\n".join(kept) + "\n")
    return (before, len(kept))


def parse_env_file(path: Path) -> Dict[str, str]:
    if not path.exists():
        raise EnvelopeError("CONFIG_INVALID", f"config file not found: {path}", 400, EXIT_CODES["CONFIG_INVALID"])
    data: Dict[str, str] = {}
    vars_ctx: Dict[str, str] = {"BASE_DIR": str(path.parent.parent)}
    var_pattern = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}|\$([A-Za-z_][A-Za-z0-9_]*)")

    def expand_vars(raw_value: str) -> str:
        def repl(match: re.Match[str]) -> str:
            key = match.group(1) or match.group(2) or ""
            if key in vars_ctx:
                return vars_ctx[key]
            return os.environ.get(key, "")

        return var_pattern.sub(repl, raw_value)

    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, val = line.split("=", 1)
        key = key.strip()
        val = val.strip()
        if key == "BASE_DIR" and "$(" in val:
            val = str(path.parent.parent)
            data[key] = val
            vars_ctx[key] = val
            continue
        if val.startswith('"') or val.startswith("'"):
            quote = val[0]
            end = val.find(quote, 1)
            if end != -1:
                val = val[1:end]
            else:
                val = val[1:]
        else:
            val = val.split("#", 1)[0].strip()
        val = expand_vars(val)
        data[key] = val
        vars_ctx[key] = val
    return data


def update_env_file(path: Path, updates: Dict[str, str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines: List[str] = []
    existing: Dict[str, int] = {}
    if path.exists():
        lines = path.read_text(encoding="utf-8").splitlines()
        for idx, raw in enumerate(lines):
            stripped = raw.strip()
            if not stripped or stripped.startswith("#") or "=" not in raw:
                continue
            key = raw.split("=", 1)[0].strip()
            existing[key] = idx
    for key, value in updates.items():
        if not re.match(r"^[A-Z0-9_]+$", key):
            raise EnvelopeError("CONFIG_INVALID", f"invalid env key: {key}", 400, EXIT_CODES["CONFIG_INVALID"])
        line = f'{key}="{value}"'
        if key in existing:
            lines[existing[key]] = line
        else:
            lines.append(line)
    atomic_write_text(path, "\n".join(lines).rstrip() + "\n")


def bool_env(v: str, default: bool = False) -> bool:
    if v is None:
        return default
    return v.strip().lower() in {"1", "true", "yes", "on"}


def int_env(v: Optional[str], default: int) -> int:
    if v is None or str(v).strip() == "":
        return default
    try:
        return int(str(v).strip())
    except ValueError as exc:
        raise EnvelopeError("CONFIG_INVALID", f"invalid integer value: {v}", 400, EXIT_CODES["CONFIG_INVALID"]) from exc


def detect_ip_type(ip: str) -> str:
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError as exc:
        raise EnvelopeError("RESULT_PARSE_ERROR", f"invalid ip address: {ip}", 400, EXIT_CODES["RESULT_PARSE_ERROR"]) from exc
    return "A" if addr.version == 4 else "AAAA"


def sha256_text(text: str) -> str:
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


@dataclass
class SimpleLock:
    path: Path
    meta_path: Path
    trace_id: str
    command: str
    _locked: bool = False

    def acquire(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            fd = os.open(str(self.path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.close(fd)
            self._locked = True
        except FileExistsError as exc:
            raise EnvelopeError("LOCKED", "another run is in progress", 409, EXIT_CODES["LOCKED"]) from exc
        meta = {
            "pid": os.getpid(),
            "startAt": now_iso(),
            "traceId": self.trace_id,
            "command": self.command,
        }
        atomic_write_json(self.meta_path, meta)

    def release(self) -> None:
        if self._locked:
            if self.meta_path.exists():
                self.meta_path.unlink(missing_ok=True)
            self.path.unlink(missing_ok=True)
            self._locked = False

    def __enter__(self) -> "SimpleLock":
        self.acquire()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.release()
