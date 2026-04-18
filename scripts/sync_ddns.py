#!/usr/bin/env python
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from phase2_common import (
    EXIT_CODES,
    EnvelopeError,
    SimpleLock,
    append_jsonl,
    atomic_write_json,
    detect_ip_type,
    envelope,
    int_env,
    new_trace_id,
    now_iso,
    parse_env_file,
    tail_jsonl,
)


def _json_request(method: str, url: str, token: str, payload: Optional[Dict[str, Any]], timeout: int = 15) -> Tuple[int, Dict[str, Any]]:
    body = None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        url=url,
        method=method,
        data=body,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "User-Agent": "coitd-sync-ddns/2.0",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            status = resp.status
            raw = resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        status = exc.code
        raw = exc.read().decode("utf-8", errors="replace")
    except urllib.error.URLError as exc:
        raise EnvelopeError("CF_API_5XX", f"cloudflare api unavailable: {exc}", 502, EXIT_CODES["UNKNOWN_ERROR"]) from exc

    try:
        data = json.loads(raw) if raw else {}
    except json.JSONDecodeError:
        data = {"success": False, "errors": [{"message": raw[:200]}]}
    return status, data


def _request_with_retry(method: str, url: str, token: str, payload: Optional[Dict[str, Any]], retries: int, base_sec: int) -> Tuple[int, Dict[str, Any]]:
    attempt = 0
    while True:
        status, data = _json_request(method, url, token, payload)
        if status in (429,) or status >= 500:
            attempt += 1
            if attempt > retries:
                return status, data
            time.sleep(base_sec * (2 ** (attempt - 1)))
            continue
        return status, data


def _cf_error(status: int, data: Dict[str, Any]) -> EnvelopeError:
    errors = data.get("errors") if isinstance(data, dict) else None
    msg = "cloudflare api error"
    if isinstance(errors, list) and errors:
        first = errors[0]
        if isinstance(first, dict) and first.get("message"):
            msg = first["message"]
    if status == 429:
        return EnvelopeError("CF_API_429", msg, 429, EXIT_CODES["UNKNOWN_ERROR"], {"httpStatus": status})
    if status >= 500:
        return EnvelopeError("CF_API_5XX", msg, 502, EXIT_CODES["UNKNOWN_ERROR"], {"httpStatus": status})
    return EnvelopeError("CF_API_4XX", msg, 502, EXIT_CODES["UNKNOWN_ERROR"], {"httpStatus": status})


class DdnsConfig:
    def __init__(self, root: Path, collector_config: Path, ddns_config: Path):
        self.root = root
        self.collector_config = collector_config
        self.ddns_config = ddns_config
        
        def _resolve_path(raw: Optional[str], default: Path) -> Path:
            if raw is None or str(raw).strip() == "":
                return default
            p = Path(str(raw).strip())
            if not p.is_absolute():
                p = (root / p).resolve()
            return p

        collector_env = parse_env_file(collector_config)
        ddns_env = parse_env_file(ddns_config)

        self.ip_version = collector_env.get("IP_VERSION", "4")
        if self.ip_version not in {"4", "6"}:
            raise EnvelopeError("CONFIG_INVALID", "IP_VERSION must be 4 or 6", 400, EXIT_CODES["CONFIG_INVALID"])

        self.state_file = _resolve_path(collector_env.get("STATE_FILE"), root / "data" / "state.json")

        self.api_base = ddns_env.get("CF_API_BASE", "https://api.cloudflare.com/client/v4").rstrip("/")
        self.token_file = _resolve_path(ddns_env.get("CF_API_TOKEN_FILE"), root / "config" / "cf_token")
        self.zone_name = ddns_env.get("CF_ZONE_NAME", "").strip()
        self.record_name = ddns_env.get("CF_RECORD_NAME", "").strip()
        self.record_type = ddns_env.get("CF_RECORD_TYPE", "AUTO").strip().upper() or "AUTO"
        self.ttl = int_env(ddns_env.get("CF_TTL"), 120)
        self.proxied = ddns_env.get("CF_PROXIED", "false").strip().lower() == "true"
        self.max_retries = int_env(ddns_env.get("DDNS_MAX_RETRIES"), 3)
        self.retry_base = int_env(ddns_env.get("DDNS_RETRY_BASE_SEC"), 2)

        self.dns_state_file = _resolve_path(ddns_env.get("DNS_STATE_FILE"), root / "data" / "dns_state.json")
        self.ddns_history_file = _resolve_path(ddns_env.get("DDNS_HISTORY_FILE"), root / "data" / "ddns_history.jsonl")
        self.ddns_lock_file = _resolve_path(ddns_env.get("DDNS_LOCK_FILE"), root / "data" / "ddns.lock")
        self.ddns_lock_meta = _resolve_path(ddns_env.get("DDNS_LOCK_META_FILE"), root / "data" / "ddns.lock.meta.json")
        self.ddns_schema_version = ddns_env.get("DDNS_SCHEMA_VERSION", "2.0")
        self.ddns_history_max = int_env(ddns_env.get("DDNS_HISTORY_MAX_LINES"), 100000)

    def validate(self) -> Dict[str, Any]:
        errors = []
        if not self.zone_name:
            errors.append("CF_ZONE_NAME is required")
        if not self.record_name:
            errors.append("CF_RECORD_NAME is required")
        if self.record_type not in {"AUTO", "A", "AAAA"}:
            errors.append("CF_RECORD_TYPE must be AUTO/A/AAAA")
        if self.ttl < 60 and self.ttl != 1:
            errors.append("CF_TTL must be 1 or >= 60")
        if self.max_retries < 0:
            errors.append("DDNS_MAX_RETRIES must be >= 0")
        if self.retry_base <= 0:
            errors.append("DDNS_RETRY_BASE_SEC must be > 0")
        if not self.token_file.exists():
            errors.append(f"token file not found: {self.token_file}")
        if not self.state_file.exists():
            errors.append(f"collector state file not found: {self.state_file}")
        if self.record_type in {"A", "AAAA"}:
            expected = "A" if self.ip_version == "4" else "AAAA"
            if self.record_type != expected:
                errors.append(f"CF_RECORD_TYPE {self.record_type} mismatch with IP_VERSION={self.ip_version}")
        ok = len(errors) == 0
        data = {
            "collectorConfigFile": str(self.collector_config),
            "ddnsConfigFile": str(self.ddns_config),
            "errors": errors,
        }
        if not ok:
            raise EnvelopeError("CONFIG_INVALID", "ddns config validation failed", 400, EXIT_CODES["CONFIG_INVALID"], data)
        return data


class DdnsSyncService:
    def __init__(self, cfg: DdnsConfig, trace_id: str):
        self.cfg = cfg
        self.trace_id = trace_id

    def _token(self) -> str:
        token = self.cfg.token_file.read_text(encoding="utf-8").strip()
        if not token:
            raise EnvelopeError("CONFIG_INVALID", "token file is empty", 400, EXIT_CODES["CONFIG_INVALID"])
        return token

    def _load_best_ip(self) -> str:
        if not self.cfg.state_file.exists():
            raise EnvelopeError("RESULT_NOT_FOUND", "collector state file not found", 424, EXIT_CODES["RESULT_NOT_FOUND"])
        try:
            state = json.loads(self.cfg.state_file.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise EnvelopeError("RESULT_PARSE_ERROR", "collector state file invalid json", 500, EXIT_CODES["RESULT_PARSE_ERROR"]) from exc
        best_ip = (state.get("bestIp") or "").strip()
        if not best_ip:
            raise EnvelopeError("RESULT_NOT_FOUND", "bestIp is empty", 424, EXIT_CODES["RESULT_NOT_FOUND"])
        return best_ip

    def _resolve_record_type(self, ip: str) -> str:
        ip_type = detect_ip_type(ip)
        if self.cfg.record_type == "AUTO":
            return ip_type
        if self.cfg.record_type != ip_type:
            raise EnvelopeError(
                "CONFIG_INVALID",
                f"record type {self.cfg.record_type} mismatch with ip {ip}",
                400,
                EXIT_CODES["CONFIG_INVALID"],
                {"recordType": self.cfg.record_type, "bestIpType": ip_type},
            )
        return self.cfg.record_type

    def _find_zone_and_record(self, token: str, record_type: str) -> Dict[str, Any]:
        zone_url = f"{self.cfg.api_base}/zones?name={urllib.parse.quote(self.cfg.zone_name)}"
        status, zone_resp = _request_with_retry("GET", zone_url, token, None, self.cfg.max_retries, self.cfg.retry_base)
        if status >= 400 or not zone_resp.get("success"):
            raise _cf_error(status, zone_resp)
        zone_items = zone_resp.get("result") or []
        if not zone_items:
            raise EnvelopeError("DNS_RECORD_NOT_FOUND", "zone not found", 404, EXIT_CODES["UNKNOWN_ERROR"], {"zoneName": self.cfg.zone_name})
        zone_id = zone_items[0].get("id")

        record_url = (
            f"{self.cfg.api_base}/zones/{zone_id}/dns_records"
            f"?type={record_type}&name={urllib.parse.quote(self.cfg.record_name)}"
        )
        status, rec_resp = _request_with_retry("GET", record_url, token, None, self.cfg.max_retries, self.cfg.retry_base)
        if status >= 400 or not rec_resp.get("success"):
            raise _cf_error(status, rec_resp)
        rec_items = rec_resp.get("result") or []
        if not rec_items:
            raise EnvelopeError(
                "DNS_RECORD_NOT_FOUND",
                "dns record not found",
                404,
                EXIT_CODES["UNKNOWN_ERROR"],
                {"recordName": self.cfg.record_name, "recordType": record_type},
            )
        record = rec_items[0]
        return {
            "zoneId": zone_id,
            "recordId": record.get("id"),
            "currentDnsIp": record.get("content", ""),
            "recordType": record.get("type", record_type),
        }

    def _update_record(self, token: str, zone_id: str, record_id: str, record_type: str, to_ip: str) -> None:
        patch_url = f"{self.cfg.api_base}/zones/{zone_id}/dns_records/{record_id}"
        payload = {
            "type": record_type,
            "name": self.cfg.record_name,
            "content": to_ip,
            "ttl": self.cfg.ttl,
            "proxied": self.cfg.proxied,
        }
        status, patch_resp = _request_with_retry("PATCH", patch_url, token, payload, self.cfg.max_retries, self.cfg.retry_base)
        if status >= 400 or not patch_resp.get("success"):
            raise _cf_error(status, patch_resp)

        verify_url = f"{self.cfg.api_base}/zones/{zone_id}/dns_records/{record_id}"
        status, verify_resp = _request_with_retry("GET", verify_url, token, None, self.cfg.max_retries, self.cfg.retry_base)
        if status >= 400 or not verify_resp.get("success"):
            raise _cf_error(status, verify_resp)
        actual = ((verify_resp.get("result") or {}).get("content") or "").strip()
        if actual != to_ip:
            raise EnvelopeError(
                "DNS_VERIFY_FAILED",
                "dns record verify failed",
                502,
                EXIT_CODES["UNKNOWN_ERROR"],
                {"expected": to_ip, "actual": actual},
            )

    def _persist_state(self, payload: Dict[str, Any]) -> None:
        atomic_write_json(self.cfg.dns_state_file, payload)

    def _append_history(self, payload: Dict[str, Any]) -> None:
        append_jsonl(self.cfg.ddns_history_file, payload)
        # Best-effort trim: avoid hard fail on history trim
        try:
            if self.cfg.ddns_history_file.exists():
                lines = self.cfg.ddns_history_file.read_text(encoding="utf-8").splitlines()
                if len(lines) > self.cfg.ddns_history_max:
                    kept = lines[-self.cfg.ddns_history_max :]
                    self.cfg.ddns_history_file.write_text("\n".join(kept) + "\n", encoding="utf-8")
        except OSError:
            pass

    def sync(self, override_ip: Optional[str] = None, action: str = "SYNC") -> Dict[str, Any]:
        target_ip = override_ip.strip() if override_ip else self._load_best_ip()
        record_type = self._resolve_record_type(target_ip)
        token = self._token()

        with SimpleLock(self.cfg.ddns_lock_file, self.cfg.ddns_lock_meta, self.trace_id, "sync"):
            record_meta = self._find_zone_and_record(token, record_type)
            current = (record_meta.get("currentDnsIp") or "").strip()
            zone_id = record_meta["zoneId"]
            record_id = record_meta["recordId"]

            run_at = now_iso()
            if current == target_ip:
                state = {
                    "schemaVersion": self.cfg.ddns_schema_version,
                    "zoneName": self.cfg.zone_name,
                    "recordName": self.cfg.record_name,
                    "recordType": record_type,
                    "zoneId": zone_id,
                    "recordId": record_id,
                    "currentDnsIp": current,
                    "lastSyncAt": run_at,
                    "lastSyncStatus": "success",
                    "lastErrorCode": "",
                    "lastGoodIp": current,
                }
                self._persist_state(state)
                history = {
                    "ts": run_at,
                    "traceId": self.trace_id,
                    "action": "NOOP",
                    "fromIp": current,
                    "toIp": target_ip,
                    "result": "noop",
                    "httpStatus": 200,
                }
                self._append_history(history)
                return {
                    "code": "DDNS_NOOP",
                    "message": "ip unchanged, skip update",
                    "data": {
                        "zoneName": self.cfg.zone_name,
                        "recordName": self.cfg.record_name,
                        "recordType": record_type,
                        "zoneId": zone_id,
                        "recordId": record_id,
                        "fromIp": current,
                        "toIp": target_ip,
                    },
                }

            self._update_record(token, zone_id, record_id, record_type, target_ip)

            state = {
                "schemaVersion": self.cfg.ddns_schema_version,
                "zoneName": self.cfg.zone_name,
                "recordName": self.cfg.record_name,
                "recordType": record_type,
                "zoneId": zone_id,
                "recordId": record_id,
                "currentDnsIp": target_ip,
                "lastSyncAt": run_at,
                "lastSyncStatus": "success",
                "lastErrorCode": "",
                "lastGoodIp": target_ip,
            }
            self._persist_state(state)
            history = {
                "ts": run_at,
                "traceId": self.trace_id,
                "action": action,
                "fromIp": current,
                "toIp": target_ip,
                "result": "success",
                "httpStatus": 200,
            }
            self._append_history(history)
            return {
                "code": "OK",
                "message": "success",
                "data": {
                    "zoneName": self.cfg.zone_name,
                    "recordName": self.cfg.record_name,
                    "recordType": record_type,
                    "zoneId": zone_id,
                    "recordId": record_id,
                    "fromIp": current,
                    "toIp": target_ip,
                },
            }

    def status(self) -> Dict[str, Any]:
        if self.cfg.dns_state_file.exists():
            try:
                state = json.loads(self.cfg.dns_state_file.read_text(encoding="utf-8"))
            except json.JSONDecodeError as exc:
                raise EnvelopeError("STATE_WRITE_ERROR", "dns_state.json invalid json", 500, EXIT_CODES["STATE_WRITE_ERROR"]) from exc
        else:
            state = {
                "schemaVersion": self.cfg.ddns_schema_version,
                "zoneName": self.cfg.zone_name,
                "recordName": self.cfg.record_name,
                "recordType": "",
                "zoneId": "",
                "recordId": "",
                "currentDnsIp": "",
                "lastSyncAt": "",
                "lastSyncStatus": "",
                "lastErrorCode": "",
                "lastGoodIp": "",
            }
        lock_meta = None
        if self.cfg.ddns_lock_meta.exists():
            try:
                lock_meta = json.loads(self.cfg.ddns_lock_meta.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                lock_meta = None
        return {
            "state": state,
            "lockMeta": lock_meta,
            "files": {
                "dnsStateFile": str(self.cfg.dns_state_file),
                "ddnsHistoryFile": str(self.cfg.ddns_history_file),
            },
        }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("command", choices=["sync", "status", "history", "validate-config", "version", "rollback"])
    parser.add_argument("--json", action="store_true", default=True)
    parser.add_argument("--plain", action="store_true")
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument("--collector-config", default=os.environ.get("COLLECTOR_CONFIG_FILE", "config/collector.env"))
    parser.add_argument("--ddns-config", default=os.environ.get("DDNS_CONFIG_FILE", "config/ddns.env"))
    parser.add_argument("--ip", default="")
    return parser.parse_args()


def _print_result(payload: Dict[str, Any], plain: bool) -> None:
    if plain:
        print(f"ok={payload['ok']} code={payload['code']} message={payload['message']}")
        print(json.dumps(payload.get("data") or {}, ensure_ascii=False, indent=2))
    else:
        print(json.dumps(payload, ensure_ascii=False))


def main() -> int:
    args = parse_args()
    trace_id = new_trace_id("ddns")
    root = Path(__file__).resolve().parent.parent
    collector_cfg = Path(args.collector_config)
    ddns_cfg = Path(args.ddns_config)
    if not collector_cfg.is_absolute():
        collector_cfg = (root / collector_cfg).resolve()
    if not ddns_cfg.is_absolute():
        ddns_cfg = (root / ddns_cfg).resolve()

    try:
        cfg = DdnsConfig(root=root, collector_config=collector_cfg, ddns_config=ddns_cfg)
        service = DdnsSyncService(cfg, trace_id)

        if args.command == "validate-config":
            data = cfg.validate()
            out = envelope(True, "OK", "success", data, trace_id)
            _print_result(out, args.plain)
            return EXIT_CODES["OK"]

        cfg.validate()

        if args.command == "status":
            out = envelope(True, "OK", "success", service.status(), trace_id)
            _print_result(out, args.plain)
            return EXIT_CODES["OK"]

        if args.command == "history":
            if args.limit < 1 or args.limit > 1000:
                raise EnvelopeError("CONFIG_INVALID", "--limit must be integer in [1,1000]", 400, EXIT_CODES["CONFIG_INVALID"])
            items = tail_jsonl(cfg.ddns_history_file, args.limit)
            data = {"limit": args.limit, "count": len(items), "items": items}
            out = envelope(True, "OK", "success", data, trace_id)
            _print_result(out, args.plain)
            return EXIT_CODES["OK"]

        if args.command == "version":
            data = {
                "scriptVersion": "2.0.0",
                "schemaVersion": cfg.ddns_schema_version,
                "collectorConfigFile": str(collector_cfg),
                "ddnsConfigFile": str(ddns_cfg),
            }
            out = envelope(True, "OK", "success", data, trace_id)
            _print_result(out, args.plain)
            return EXIT_CODES["OK"]

        if args.command == "rollback":
            if not args.ip.strip():
                raise EnvelopeError("CONFIG_INVALID", "rollback requires --ip", 400, EXIT_CODES["CONFIG_INVALID"])
            result = service.sync(override_ip=args.ip.strip(), action="ROLLBACK")
            out = envelope(result["code"] == "OK", result["code"], result["message"], result["data"], trace_id)
            _print_result(out, args.plain)
            return EXIT_CODES["OK"] if out["ok"] else EXIT_CODES["UNKNOWN_ERROR"]

        # sync
        result = service.sync()
        out = envelope(result["code"] in {"OK", "DDNS_NOOP"}, result["code"], result["message"], result["data"], trace_id)
        _print_result(out, args.plain)
        return EXIT_CODES["OK"] if out["ok"] else EXIT_CODES["UNKNOWN_ERROR"]

    except EnvelopeError as err:
        out = envelope(False, err.code, err.message, err.data, trace_id)
        _print_result(out, args.plain)
        return err.exit_code
    except Exception as err:  # pragma: no cover
        out = envelope(False, "UNKNOWN_ERROR", str(err), {}, trace_id)
        _print_result(out, args.plain)
        return EXIT_CODES["UNKNOWN_ERROR"]


if __name__ == "__main__":
    raise SystemExit(main())
