#!/usr/bin/env bash
set -euo pipefail

PATH=/bin:/sbin:/usr/bin:/usr/sbin:/usr/local/bin:/usr/local/sbin:~/bin
export PATH

# --------------------------------------------------------------
# Project: CloudflareSpeedTest -> Aliyun DDNS updater
# Version: 2.2.0
# Desc   : Run CFST, pick top N IPs, update AliDNS records.
# --------------------------------------------------------------

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONFIG_FILE="cfst_ddns.conf"
RESULT_FILE="result_ddns.txt"
LINE_RAW=""
PUSH_IP_COUNT_RAW=""
PUSH_IP_COUNT=1
ENABLE_SUMMARY_RAW=""
ENABLE_SUMMARY=1
SUMMARY_DIR_RAW=""
CONFIG_RECORD_ID=""
RUN_ID="$(date '+%Y%m%d-%H%M%S')-$(od -An -N3 -tx1 /dev/urandom | tr -d ' \n')"
STARTED_AT="$(date -Iseconds)"
START_EPOCH="$(date +%s)"
SUMMARY_DIR="${SCRIPT_DIR}/state"
SUMMARY_HISTORY_DIR="${SUMMARY_DIR}/history"
SUMMARY_LATEST_FILE="${SUMMARY_DIR}/latest.json"
SUMMARY_RUN_FILE="${SUMMARY_HISTORY_DIR}/${RUN_ID}.json"
DDNS_SUMMARY_TSV="$(mktemp -t cfst_ddns_ddns_XXXXXX.tsv)"
LAST_ERROR=""
LAST_ERROR_CODE=""
LAST_ERROR_MESSAGE=""
declare -a LINE_LIST=()
declare -a CONTENT_LIST=()
declare -a CURRENT_RECORD_IDS=()

log() {
  printf '[INFO] %s\n' "$*"
}

err() {
  printf '[ERROR] %s\n' "$*" >&2
}

trim_ws() {
  local s="$1"
  s="${s#"${s%%[![:space:]]*}"}"
  s="${s%"${s##*[![:space:]]}"}"
  printf '%s' "${s}"
}

in_array() {
  local needle="$1"
  shift
  local item
  for item in "$@"; do
    if [[ "${item}" == "${needle}" ]]; then
      return 0
    fi
  done
  return 1
}

get_conf() {
  local key="$1"
  local value
  value="$(grep -E "^${key}=" "${CONFIG_FILE}" | tail -n1 | cut -d'=' -f2- || true)"
  value="${value#"${value%%[![:space:]]*}"}"
  value="${value%"${value##*[![:space:]]}"}"
  printf '%s' "${value}"
}

require_conf() {
  local key="$1"
  local value="$2"
  if [[ -z "${value}" ]]; then
    LAST_ERROR="Missing config key: ${key}"
    err "Missing config key: ${key}"
    exit 1
  fi
}

parse_bool() {
  local raw
  raw="$(trim_ws "$1")"
  raw="${raw,,}"
  case "${raw}" in
    ""|"1"|"true"|"yes"|"on") return 0 ;;
    "0"|"false"|"no"|"off") return 1 ;;
    *)
      LAST_ERROR="Invalid boolean value: ${1}"
      err "Invalid boolean value: ${1}"
      exit 1
      ;;
  esac
}

sanitize_tsv_field() {
  local s="$1"
  s="${s//$'\t'/ }"
  s="${s//$'\n'/ }"
  printf '%s' "${s}"
}

append_ddns_summary_row() {
  local line="$1"
  local rank="$2"
  local total="$3"
  local ip="$4"
  local action="$5"
  local record_id="$6"
  local ok="$7"
  local request_id="$8"
  local error_code="$9"
  local error_message="${10}"

  error_message="$(sanitize_tsv_field "${error_message}")"
  printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n' \
    "${line}" \
    "${rank}" \
    "${total}" \
    "${ip}" \
    "${action}" \
    "${record_id}" \
    "${ok}" \
    "${request_id}" \
    "${error_code}" \
    "${error_message}" >> "${DDNS_SUMMARY_TSV}"
}

parse_summary_config() {
  if parse_bool "${ENABLE_SUMMARY_RAW:-true}"; then
    ENABLE_SUMMARY=1
  else
    ENABLE_SUMMARY=0
  fi

  if [[ -n "${SUMMARY_DIR_RAW}" ]]; then
    if [[ "${SUMMARY_DIR_RAW}" = /* ]]; then
      SUMMARY_DIR="${SUMMARY_DIR_RAW}"
    else
      SUMMARY_DIR="${SCRIPT_DIR}/${SUMMARY_DIR_RAW}"
    fi
  else
    SUMMARY_DIR="${SCRIPT_DIR}/state"
  fi

  SUMMARY_HISTORY_DIR="${SUMMARY_DIR}/history"
  SUMMARY_LATEST_FILE="${SUMMARY_DIR}/latest.json"
  SUMMARY_RUN_FILE="${SUMMARY_HISTORY_DIR}/${RUN_ID}.json"

  if (( ENABLE_SUMMARY == 1 )); then
    mkdir -p "${SUMMARY_HISTORY_DIR}"
    log "Summary enabled. Latest=${SUMMARY_LATEST_FILE}"
  else
    log "Summary disabled by config."
  fi
}

write_run_summary() {
  local exit_code="$1"
  if (( ENABLE_SUMMARY != 1 )); then
    return 0
  fi

  local finished_at
  local duration_seconds
  local status
  local line_csv
  local selected_csv

  finished_at="$(date -Iseconds)"
  duration_seconds=$(( $(date +%s) - START_EPOCH ))
  if (( exit_code == 0 )); then
    if (( ${#CONTENT_LIST[@]} > 0 )); then
      status="success"
    else
      status="skipped"
    fi
  else
    status="failed"
  fi

  if [[ -z "${LAST_ERROR}" && ${exit_code} -ne 0 ]]; then
    LAST_ERROR="script exited with code ${exit_code}"
  fi

  line_csv="$(IFS=,; echo "${LINE_LIST[*]-}")"
  selected_csv="$(IFS=,; echo "${CONTENT_LIST[*]-}")"

  SUM_RUN_ID="${RUN_ID}" \
  SUM_STARTED_AT="${STARTED_AT}" \
  SUM_FINISHED_AT="${finished_at}" \
  SUM_DURATION_SECONDS="${duration_seconds}" \
  SUM_STATUS="${status}" \
  SUM_EXIT_CODE="${exit_code}" \
  SUM_DOMAIN_NAME="${DOMAIN_NAME:-}" \
  SUM_RR="${RR:-}" \
  SUM_TYPE="${TYPE:-}" \
  SUM_TTL="${TTL:-}" \
  SUM_PUSH_IP_COUNT="${PUSH_IP_COUNT:-1}" \
  SUM_LINE_CSV="${line_csv}" \
  SUM_SELECTED_CSV="${selected_csv}" \
  SUM_LAST_ERROR="${LAST_ERROR}" \
  SUM_LAST_ERROR_CODE="${LAST_ERROR_CODE}" \
  SUM_LAST_ERROR_MESSAGE="${LAST_ERROR_MESSAGE}" \
  SUM_LATEST_FILE="${SUMMARY_LATEST_FILE}" \
  SUM_RUN_FILE="${SUMMARY_RUN_FILE}" \
  python3 - "${DDNS_SUMMARY_TSV}" <<'PY'
import json
import os
import sys
from pathlib import Path


def split_csv(value: str):
    value = (value or "").strip()
    if not value:
        return []
    return [x for x in value.split(",") if x]


def to_int(value: str, default: int = 0):
    try:
        return int(str(value).strip())
    except Exception:
        return default


tsv_path = Path(sys.argv[1])
latest_file = Path(os.environ.get("SUM_LATEST_FILE", "")).resolve()
run_file = Path(os.environ.get("SUM_RUN_FILE", "")).resolve()

selected_ips = split_csv(os.environ.get("SUM_SELECTED_CSV", ""))
line_list = split_csv(os.environ.get("SUM_LINE_CSV", ""))
push_ip_count = to_int(os.environ.get("SUM_PUSH_IP_COUNT", "1"), 1)
exit_code = to_int(os.environ.get("SUM_EXIT_CODE", "1"), 1)

previous_ips = []
previous_run_id = ""
if latest_file.exists():
    try:
        prev = json.loads(latest_file.read_text(encoding="utf-8"))
        if isinstance(prev.get("selected_ips"), list):
            previous_ips = [str(x) for x in prev.get("selected_ips", [])]
        previous_run_id = str(prev.get("run_id", ""))
    except Exception:
        previous_ips = []
        previous_run_id = ""

operations = []
if tsv_path.exists():
    for raw in tsv_path.read_text(encoding="utf-8", errors="replace").splitlines():
        parts = raw.split("\t")
        if len(parts) < 10:
            continue
        line, rank, total, ip, action, record_id, ok, request_id, error_code, error_message = parts[:10]
        operations.append(
            {
                "line": line,
                "rank": to_int(rank, 0),
                "total": to_int(total, 0),
                "ip": ip,
                "action": action,
                "record_id": record_id,
                "ok": str(ok).strip() in {"1", "true", "True"},
                "request_id": request_id,
                "error_code": error_code,
                "error_message": error_message,
            }
        )

success_count = sum(1 for op in operations if op.get("ok"))
failed_count = sum(1 for op in operations if not op.get("ok"))
ip_changed = selected_ips != previous_ips

summary = {
    "run_id": os.environ.get("SUM_RUN_ID", ""),
    "status": os.environ.get("SUM_STATUS", "failed"),
    "exit_code": exit_code,
    "started_at": os.environ.get("SUM_STARTED_AT", ""),
    "finished_at": os.environ.get("SUM_FINISHED_AT", ""),
    "duration_seconds": to_int(os.environ.get("SUM_DURATION_SECONDS", "0"), 0),
    "config": {
        "domain_name": os.environ.get("SUM_DOMAIN_NAME", ""),
        "rr": os.environ.get("SUM_RR", ""),
        "type": os.environ.get("SUM_TYPE", ""),
        "ttl": os.environ.get("SUM_TTL", ""),
        "line_list": line_list,
        "push_ip_count": push_ip_count,
    },
    "selected_ips": selected_ips,
    "previous_selected_ips": previous_ips,
    "previous_run_id": previous_run_id,
    "ip_changed": ip_changed,
    "ddns": {
        "operations": operations,
        "success_count": success_count,
        "failed_count": failed_count,
    },
    "error": {
        "message": os.environ.get("SUM_LAST_ERROR", ""),
        "code": os.environ.get("SUM_LAST_ERROR_CODE", ""),
        "detail": os.environ.get("SUM_LAST_ERROR_MESSAGE", ""),
    },
}

payload = json.dumps(summary, ensure_ascii=False, indent=2) + "\n"
run_file.parent.mkdir(parents=True, exist_ok=True)
latest_file.parent.mkdir(parents=True, exist_ok=True)
run_file.write_text(payload, encoding="utf-8")
latest_file.write_text(payload, encoding="utf-8")
PY

  log "Summary written: ${SUMMARY_RUN_FILE}"
}

on_exit() {
  local exit_code="$1"
  write_run_summary "${exit_code}" || true
  rm -f "${DDNS_SUMMARY_TSV}" 2>/dev/null || true
}

alidns_request() {
  local action="$1"
  shift
  python3 - "$ALI_ACCESS_KEY_ID" "$ALI_ACCESS_KEY_SECRET" "$action" "$@" <<'PY'
import base64
import datetime
import hashlib
import hmac
import json
import sys
import urllib.parse
import urllib.request
import uuid

akid = sys.argv[1]
aksec = sys.argv[2]
action = sys.argv[3]
pairs = sys.argv[4:]

params = {
    "Format": "JSON",
    "Version": "2015-01-09",
    "AccessKeyId": akid,
    "SignatureMethod": "HMAC-SHA1",
    "Timestamp": datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
    "SignatureVersion": "1.0",
    "SignatureNonce": str(uuid.uuid4()),
    "Action": action,
}

for p in pairs:
    if "=" not in p:
        continue
    k, v = p.split("=", 1)
    params[k] = v

def pct(s: str) -> str:
    return urllib.parse.quote(str(s), safe="~")

items = sorted(params.items(), key=lambda kv: kv[0])
canonical = "&".join(f"{pct(k)}={pct(v)}" for k, v in items)
string_to_sign = "GET&%2F&" + pct(canonical)
key = (aksec + "&").encode("utf-8")
signature = base64.b64encode(hmac.new(key, string_to_sign.encode("utf-8"), hashlib.sha1).digest()).decode("utf-8")
params["Signature"] = signature

query = urllib.parse.urlencode(params, quote_via=urllib.parse.quote, safe="~")
url = "https://alidns.aliyuncs.com/?" + query

try:
    with urllib.request.urlopen(url, timeout=25) as resp:
        body = resp.read().decode("utf-8", errors="replace")
except Exception as e:
    print(json.dumps({"ok": False, "error": str(e)}, ensure_ascii=False))
    sys.exit(2)

print(body)
PY
}

read_config() {
  if [[ ! -f "${CONFIG_FILE}" ]]; then
    LAST_ERROR="Config file not found: ${CONFIG_FILE}"
    err "Config file not found: ${CONFIG_FILE}"
    exit 1
  fi

  FOLDER="$(get_conf FOLDER)"
  ALI_ACCESS_KEY_ID="$(get_conf ALI_ACCESS_KEY_ID)"
  ALI_ACCESS_KEY_SECRET="$(get_conf ALI_ACCESS_KEY_SECRET)"
  DOMAIN_NAME="$(get_conf DOMAIN_NAME)"
  RR="$(get_conf RR)"
  TYPE="$(get_conf TYPE)"
  TTL="$(get_conf TTL)"
  LINE_RAW="$(get_conf LINE)"
  PUSH_IP_COUNT_RAW="$(get_conf PUSH_IP_COUNT)"
  ENABLE_SUMMARY_RAW="$(get_conf ENABLE_SUMMARY)"
  SUMMARY_DIR_RAW="$(get_conf SUMMARY_DIR)"
  CONFIG_RECORD_ID="$(get_conf RECORD_ID)"
  CFST_ARGS="$(get_conf CFST_ARGS)"
  RESULT_FILE_CONF="$(get_conf RESULT_FILE)"

  require_conf "FOLDER" "${FOLDER}"
  require_conf "ALI_ACCESS_KEY_ID" "${ALI_ACCESS_KEY_ID}"
  require_conf "ALI_ACCESS_KEY_SECRET" "${ALI_ACCESS_KEY_SECRET}"
  require_conf "DOMAIN_NAME" "${DOMAIN_NAME}"
  require_conf "RR" "${RR}"
  require_conf "TYPE" "${TYPE}"
  require_conf "TTL" "${TTL}"
  [[ -z "${LINE_RAW}" ]] && LINE_RAW="default"
  [[ -n "${RESULT_FILE_CONF}" ]] && RESULT_FILE="${RESULT_FILE_CONF}"
  parse_push_ip_count
  parse_summary_config

  parse_line_list
}

parse_push_ip_count() {
  local raw="${PUSH_IP_COUNT_RAW}"
  [[ -z "${raw}" ]] && raw="1"

  if [[ ! "${raw}" =~ ^[0-9]+$ ]]; then
    LAST_ERROR="PUSH_IP_COUNT must be a positive integer, got: ${raw}"
    err "PUSH_IP_COUNT must be a positive integer, got: ${raw}"
    exit 1
  fi

  PUSH_IP_COUNT="${raw}"
  if (( PUSH_IP_COUNT <= 0 )); then
    LAST_ERROR="PUSH_IP_COUNT must be >= 1, got: ${PUSH_IP_COUNT}"
    err "PUSH_IP_COUNT must be >= 1, got: ${PUSH_IP_COUNT}"
    exit 1
  fi
  if (( PUSH_IP_COUNT > 20 )); then
    LAST_ERROR="PUSH_IP_COUNT too large (${PUSH_IP_COUNT}), max supported is 20"
    err "PUSH_IP_COUNT too large (${PUSH_IP_COUNT}), max supported is 20"
    exit 1
  fi

  log "Resolved PUSH_IP_COUNT: ${PUSH_IP_COUNT}"
}

parse_line_list() {
  local -a raw_parts=()
  local item trimmed
  IFS=',' read -r -a raw_parts <<< "${LINE_RAW}"

  LINE_LIST=()
  for item in "${raw_parts[@]}"; do
    trimmed="$(trim_ws "${item}")"
    [[ -z "${trimmed}" ]] && continue
    if ! in_array "${trimmed}" "${LINE_LIST[@]}"; then
      LINE_LIST+=("${trimmed}")
    fi
  done

  if [[ ${#LINE_LIST[@]} -eq 0 ]]; then
    LINE_LIST=("default")
  fi

  log "Resolved LINE values: $(IFS=,; echo "${LINE_LIST[*]}")"
}

run_cfst_and_pick_ips() {
  cd "${FOLDER}"
  if [[ ! -x "./cfst" ]]; then
    LAST_ERROR="cfst binary not found or not executable: ${FOLDER}/cfst"
    err "cfst binary not found or not executable: ${FOLDER}/cfst"
    exit 1
  fi

  local -a args=()
  if [[ -n "${CFST_ARGS:-}" ]]; then
    # shellcheck disable=SC2206
    args=( ${CFST_ARGS} )
  fi

  log "Running CFST..."
  ./cfst "${args[@]}" -o "${RESULT_FILE}"

  if [[ ! -f "${RESULT_FILE}" ]]; then
    log "CFST output file not found (${RESULT_FILE}), skip DDNS update."
    exit 0
  fi

  CONTENT_LIST=()
  local ip
  while IFS= read -r ip; do
    ip="$(trim_ws "${ip}")"
    [[ -z "${ip}" ]] && continue
    if ! in_array "${ip}" "${CONTENT_LIST[@]}"; then
      CONTENT_LIST+=("${ip}")
    fi
    if (( ${#CONTENT_LIST[@]} >= PUSH_IP_COUNT )); then
      break
    fi
  done < <(awk -F',' 'NR>1 {print $1}' "${RESULT_FILE}")

  if (( ${#CONTENT_LIST[@]} == 0 )); then
    log "No valid IP from CFST result, skip DDNS update."
    exit 0
  fi

  log "Selected IP count: ${#CONTENT_LIST[@]}"
  log "Selected IP list: $(IFS=,; echo "${CONTENT_LIST[*]}")"
}

get_sub_domain() {
  local sub_domain
  if [[ "${RR}" == "@" ]]; then
    sub_domain="${DOMAIN_NAME}"
  else
    sub_domain="${RR}.${DOMAIN_NAME}"
  fi
  printf '%s' "${sub_domain}"
}

find_record_ids_for_line() {
  local line="$1"
  local sub_domain resp api_code api_msg
  CURRENT_RECORD_IDS=()

  sub_domain="$(get_sub_domain)"

  log "Querying AliDNS record id for ${sub_domain} (${TYPE}, Line=${line}) ..."
  resp="$(alidns_request "DescribeSubDomainRecords" "SubDomain=${sub_domain}" "Type=${TYPE}" "PageSize=200")"

  api_code="$(printf '%s' "${resp}" | python3 -c '
import json
import sys

try:
    d = json.load(sys.stdin)
except Exception:
    print("JSON_PARSE_ERROR")
    raise SystemExit(0)

print(d.get("Code", ""))
')"
  if [[ -n "${api_code}" ]]; then
    api_msg="$(printf '%s' "${resp}" | python3 -c '
import json
import sys

try:
    d = json.load(sys.stdin)
except Exception:
    print("unknown error")
    raise SystemExit(0)

print(d.get("Message", "unknown error"))
    ')"
    LAST_ERROR="AliDNS query failed: ${api_code} ${api_msg}"
    LAST_ERROR_CODE="${api_code}"
    LAST_ERROR_MESSAGE="${api_msg}"
    err "AliDNS query failed: ${api_code} ${api_msg}"
    err "Response: ${resp}"
    exit 1
  fi

  mapfile -t CURRENT_RECORD_IDS < <(printf '%s' "${resp}" | python3 -c '
import json
import sys

target = sys.argv[1].strip().lower()

try:
    d = json.load(sys.stdin)
except Exception:
    print("")
    raise SystemExit(0)

records = d.get("DomainRecords", {}).get("Record", [])
if isinstance(records, dict):
    records = [records]

seen = set()
for rec in records:
    rid = str(rec.get("RecordId", "")).strip()
    if not rid:
        continue
    candidates = [
        str(rec.get("Line", "")).strip().lower(),
        str(rec.get("LineCode", "")).strip().lower(),
        str(rec.get("LineKey", "")).strip().lower(),
        str(rec.get("LineName", "")).strip().lower(),
    ]
    if target in candidates and rid not in seen:
        print(rid)
        seen.add(rid)
' "${line}")

  log "Matched existing records for Line=${line}: ${#CURRENT_RECORD_IDS[@]}"
}

upsert_record_for_line_ip() {
  local line="$1"
  local ip="$2"
  local record_id="$3"
  local rank="$4"
  local total="$5"
  local resp
  local action
  action="add"

  if [[ -n "${record_id}" ]]; then
    action="update"
    log "Updating AliDNS record for Line=${line} Rank=${rank}/${total}: RecordId=${record_id} IP=${ip}"
    resp="$(alidns_request \
      "UpdateDomainRecord" \
      "RecordId=${record_id}" \
      "RR=${RR}" \
      "Type=${TYPE}" \
      "Value=${ip}" \
      "TTL=${TTL}" \
      "Line=${line}")"
  else
    log "No existing record for Line=${line} Rank=${rank}/${total}, creating new AliDNS record with IP=${ip}"
    resp="$(alidns_request \
      "AddDomainRecord" \
      "DomainName=${DOMAIN_NAME}" \
      "RR=${RR}" \
      "Type=${TYPE}" \
      "Value=${ip}" \
      "TTL=${TTL}" \
      "Line=${line}")"
  fi

  local code msg req rid
  code="$(printf '%s' "${resp}" | python3 -c '
import json
import sys

try:
    d = json.load(sys.stdin)
except Exception:
    print("JSON_PARSE_ERROR")
    raise SystemExit(0)

print(d.get("Code", ""))
')"

  if [[ -n "${code}" ]]; then
    msg="$(printf '%s' "${resp}" | python3 -c '
import json
import sys

try:
    d = json.load(sys.stdin)
except Exception:
    print("unknown error")
    raise SystemExit(0)

print(d.get("Message", "unknown error"))
    ')"
    append_ddns_summary_row "${line}" "${rank}" "${total}" "${ip}" "${action}" "${record_id}" "0" "" "${code}" "${msg}"
    LAST_ERROR="AliDNS API failed: ${code} ${msg}"
    LAST_ERROR_CODE="${code}"
    LAST_ERROR_MESSAGE="${msg}"
    err "AliDNS API failed: ${code} ${msg}"
    err "Response: ${resp}"
    exit 1
  fi

  req="$(printf '%s' "${resp}" | python3 -c '
import json
import sys

try:
    d = json.load(sys.stdin)
except Exception:
    print("")
    raise SystemExit(0)

print(d.get("RequestId", ""))
')"
  rid="$(printf '%s' "${resp}" | python3 -c '
import json
import sys

try:
    d = json.load(sys.stdin)
except Exception:
    print("")
    raise SystemExit(0)

print(d.get("RecordId", ""))
')"

  append_ddns_summary_row "${line}" "${rank}" "${total}" "${ip}" "${action}" "${rid}" "1" "${req}" "" ""
  log "AliDNS upsert success. Line=${line} Rank=${rank}/${total} IP=${ip} RequestId=${req} RecordId=${rid}"
}

process_lines() {
  local line record_id
  local index rank
  local single_line=0
  local total_ips="${#CONTENT_LIST[@]}"
  if [[ ${#LINE_LIST[@]} -eq 1 ]]; then
    single_line=1
  fi

  if [[ -n "${CONFIG_RECORD_ID}" && ( ${single_line} -eq 0 || ${PUSH_IP_COUNT} -ne 1 ) ]]; then
    log "RECORD_ID is only valid for single LINE with PUSH_IP_COUNT=1; ignore RECORD_ID and auto-match."
  fi

  for line in "${LINE_LIST[@]}"; do
    CURRENT_RECORD_IDS=()
    if [[ -n "${CONFIG_RECORD_ID}" && ${single_line} -eq 1 && ${PUSH_IP_COUNT} -eq 1 ]]; then
      CURRENT_RECORD_IDS=("${CONFIG_RECORD_ID}")
      log "Using RECORD_ID from config for Line=${line}: ${CONFIG_RECORD_ID}"
    else
      find_record_ids_for_line "${line}"
    fi

    if (( ${#CURRENT_RECORD_IDS[@]} > total_ips )); then
      log "Line=${line} has ${#CURRENT_RECORD_IDS[@]} existing records, only top ${total_ips} will be updated."
    fi

    for index in "${!CONTENT_LIST[@]}"; do
      record_id=""
      rank=$((index + 1))
      if (( index < ${#CURRENT_RECORD_IDS[@]} )); then
        record_id="${CURRENT_RECORD_IDS[$index]}"
      fi
      upsert_record_for_line_ip "${line}" "${CONTENT_LIST[$index]}" "${record_id}" "${rank}" "${total_ips}"
    done
  done
}

trap 'on_exit $?' EXIT

read_config
run_cfst_and_pick_ips
process_lines
