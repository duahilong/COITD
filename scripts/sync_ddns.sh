#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"

if command -v python3 >/dev/null 2>&1; then
  PY_BIN="python3"
elif command -v python >/dev/null 2>&1; then
  PY_BIN="python"
else
  echo "python interpreter not found (python3/python)" >&2
  exit 127
fi

exec "${PY_BIN}" "${SCRIPT_DIR}/sync_ddns.py" "$@" \
  --collector-config "${ROOT_DIR}/config/collector.env" \
  --ddns-config "${ROOT_DIR}/config/ddns.env"
