#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import argparse
import csv
import datetime as dt
import json
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Tuple


FLAG_KEYS = {"httping", "dd", "allip", "debug", "v", "h"}

# 与 `cfst -h` 对齐的参数集合（用于拼写校验）
KNOWN_KEYS = {
    "n",
    "t",
    "dn",
    "dt",
    "tp",
    "url",
    "httping",
    "httping-code",
    "cfcolo",
    "tl",
    "tll",
    "tlr",
    "sl",
    "p",
    "f",
    "ip",
    "o",
    "dd",
    "allip",
    "debug",
    "v",
    "h",
}

# 允许配置文件使用下划线风格写法
KEY_ALIASES = {
    "httping_code": "httping-code",
}


def now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).astimezone().isoformat(timespec="seconds")


def write_json_file(path: Path, data: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
    path.write_text(payload, encoding="utf-8")


def load_config(config_path: Path) -> Dict[str, Any]:
    if not config_path.exists():
        raise FileNotFoundError(f"配置文件不存在: {config_path}")
    # 使用 utf-8-sig 兼容带 BOM 的配置文件
    raw_text = config_path.read_text(encoding="utf-8-sig")
    try:
        return json.loads(raw_text)
    except json.JSONDecodeError:
        # 兼容 jsonc（允许 // 与 /* */ 注释）
        return json.loads(strip_json_comments(raw_text))


def strip_json_comments(text: str) -> str:
    result: List[str] = []
    i = 0
    in_string = False
    escaped = False
    in_line_comment = False
    in_block_comment = False
    length = len(text)

    while i < length:
        ch = text[i]
        nxt = text[i + 1] if i + 1 < length else ""

        if in_line_comment:
            if ch == "\n":
                in_line_comment = False
                result.append(ch)
            i += 1
            continue

        if in_block_comment:
            if ch == "*" and nxt == "/":
                in_block_comment = False
                i += 2
            else:
                i += 1
            continue

        if in_string:
            result.append(ch)
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
            i += 1
            continue

        if ch == '"':
            in_string = True
            result.append(ch)
            i += 1
            continue

        if ch == "/" and nxt == "/":
            in_line_comment = True
            i += 2
            continue

        if ch == "/" and nxt == "*":
            in_block_comment = True
            i += 2
            continue

        result.append(ch)
        i += 1

    return "".join(result)


def _normalize_option_key(key: str) -> str:
    normalized = str(key).strip().lstrip("-").replace("_", "-").lower()
    return KEY_ALIASES.get(normalized, normalized)


def build_cfst_command(config: Dict[str, Any], base_dir: Path) -> Tuple[List[str], Path, Path]:
    cfst_path = Path(config.get("cfst_path", "./cfst")).expanduser()
    if not cfst_path.is_absolute():
        cfst_path = (base_dir / cfst_path).resolve()

    workdir = Path(config.get("workdir", ".")).expanduser()
    if not workdir.is_absolute():
        workdir = (base_dir / workdir).resolve()

    result_file = Path(config.get("result_file", "result.csv"))
    if result_file.is_absolute():
        result_path = result_file
    else:
        result_path = (workdir / result_file).resolve()

    options = config.get("options", {})
    if not isinstance(options, dict):
        raise ValueError("配置项 options 必须是对象")
    strict = bool(config.get("strict_known_options", True))

    cmd: List[str] = [str(cfst_path)]
    normalized_options = {_normalize_option_key(k): v for k, v in options.items()}

    for raw_key, raw_value in options.items():
        key = _normalize_option_key(raw_key)
        if strict and key not in KNOWN_KEYS:
            raise ValueError(f"未知参数: {raw_key}（标准化后: {key}）")
        if key in FLAG_KEYS:
            if bool(raw_value):
                cmd.append(f"-{key}")
            continue
        if raw_value is None:
            continue
        if isinstance(raw_value, str) and raw_value == "" and key != "o":
            continue
        cmd.extend([f"-{key}", str(raw_value)])

    # 强制写入 result_file，避免命令和解析结果文件不一致
    if "o" not in normalized_options or normalized_options.get("o") is None:
        cmd.extend(["-o", str(result_path)])

    return cmd, workdir, result_path


def parse_top_ips(result_path: Path, count: int) -> List[str]:
    if not result_path.exists():
        raise FileNotFoundError(f"测速结果文件不存在: {result_path}")

    if count <= 0:
        count = 1

    with result_path.open("r", encoding="utf-8", newline="") as f:
        reader = csv.reader(f)
        next(reader, None)  # skip header
        ips: List[str] = []
        for row in reader:
            if not row or len(row) == 0:
                continue
            ip = row[0].strip()
            if ip:
                ips.append(ip)
            if len(ips) >= count:
                break

        if not ips:
            raise RuntimeError(f"结果文件为空或无有效 IP: {result_path}")
        return ips


def main() -> int:
    parser = argparse.ArgumentParser(description="使用配置文件启动 CFST 并提取最佳 IP")
    parser.add_argument(
        "-c",
        "--config",
        default="scripts/cfst/cfst_config.full.json",
        help="配置文件路径，默认: scripts/cfst/cfst_config.full.json",
    )
    parser.add_argument(
        "--summary-json",
        default="",
        help="将执行结果摘要写入 JSON 文件（用于 Web/API 对接）",
    )
    parser.add_argument(
        "--print-summary-json",
        action="store_true",
        help="在 stdout 打印一行 SUMMARY_JSON=...（便于上层进程采集）",
    )
    args = parser.parse_args()

    config_path = Path(args.config).resolve()
    base_dir = config_path.parent
    started_at = now_iso()
    started_mono = time.monotonic()
    summary_path = Path(args.summary_json).resolve() if args.summary_json else None

    summary: Dict[str, Any] = {
        "status": "failed",
        "exit_code": 1,
        "config_path": str(config_path),
        "started_at": started_at,
    }
    workdir: Path | None = None
    result_path: Path | None = None
    best_ip_path: Path | None = None

    try:
        config = load_config(config_path)
        cmd, workdir, result_path = build_cfst_command(config, base_dir)
        best_ip_count = int(config.get("best_ip_count", 1))
        if best_ip_count <= 0:
            best_ip_count = 1

        print(f"[INFO] 配置文件: {config_path}")
        print(f"[INFO] 工作目录: {workdir}")
        print(f"[INFO] 结果文件: {result_path}")
        print(f"[INFO] BEST_IP 数量: {best_ip_count}")
        print(f"[INFO] 执行命令: {' '.join(cmd)}")

        workdir.mkdir(parents=True, exist_ok=True)
        if result_path.exists():
            result_path.unlink()

        subprocess.run(cmd, cwd=str(workdir), check=True)
        top_ips = parse_top_ips(result_path, best_ip_count)
        print(f"BEST_IP={top_ips[0]}")
        if len(top_ips) > 1:
            print(f"BEST_IP_LIST={','.join(top_ips)}")

        best_ip_file = config.get("best_ip_file", "")
        if best_ip_file:
            best_ip_path = Path(best_ip_file)
            if not best_ip_path.is_absolute():
                best_ip_path = (workdir / best_ip_path).resolve()
            best_ip_path.write_text("\n".join(top_ips) + "\n", encoding="utf-8")
            print(f"[INFO] 已写入最佳 IP 文件: {best_ip_path}")

        summary = {
            "status": "success",
            "exit_code": 0,
            "config_path": str(config_path),
            "started_at": started_at,
            "finished_at": now_iso(),
            "duration_seconds": round(time.monotonic() - started_mono, 3),
            "workdir": str(workdir),
            "result_file": str(result_path),
            "best_ip_count": best_ip_count,
            "best_ip": top_ips[0],
            "best_ip_list": top_ips,
            "best_ip_file": str(best_ip_path) if best_ip_path else "",
            "command": cmd,
        }
        if summary_path:
            write_json_file(summary_path, summary)
            print(f"[INFO] 已写入执行摘要: {summary_path}")
        if args.print_summary_json:
            print("SUMMARY_JSON=" + json.dumps(summary, ensure_ascii=False))
        return 0
    except Exception as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        summary.update(
            {
                "status": "failed",
                "exit_code": 1,
                "error": str(exc),
                "finished_at": now_iso(),
                "duration_seconds": round(time.monotonic() - started_mono, 3),
            }
        )
        if workdir is not None:
            summary["workdir"] = str(workdir)
        if result_path is not None:
            summary["result_file"] = str(result_path)
        if best_ip_path is not None:
            summary["best_ip_file"] = str(best_ip_path)
        if isinstance(exc, subprocess.CalledProcessError):
            summary["exit_code"] = int(exc.returncode)

        if summary_path:
            write_json_file(summary_path, summary)
            print(f"[INFO] 已写入执行摘要: {summary_path}", file=sys.stderr)
        if args.print_summary_json:
            print("SUMMARY_JSON=" + json.dumps(summary, ensure_ascii=False), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
