#!/usr/bin/env bash
set -euo pipefail

PATH=/bin:/sbin:/usr/bin:/usr/sbin:/usr/local/bin:/usr/local/sbin:~/bin
export PATH

# --------------------------------------------------------------
# Project: CloudflareSpeedTest -> Aliyun DDNS updater
# Version: 2.0.0
# Desc   : Run CFST, pick the first IP, update AliDNS record.
# --------------------------------------------------------------

CONFIG_FILE="cfst_ddns.conf"
RESULT_FILE="result_ddns.txt"
LINE_RAW=""
CONFIG_RECORD_ID=""
CURRENT_RECORD_ID=""
declare -a LINE_LIST=()

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
    err "Missing config key: ${key}"
    exit 1
  fi
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
  [[ ! -f "${CONFIG_FILE}" ]] && err "Config file not found: ${CONFIG_FILE}" && exit 1

  FOLDER="$(get_conf FOLDER)"
  ALI_ACCESS_KEY_ID="$(get_conf ALI_ACCESS_KEY_ID)"
  ALI_ACCESS_KEY_SECRET="$(get_conf ALI_ACCESS_KEY_SECRET)"
  DOMAIN_NAME="$(get_conf DOMAIN_NAME)"
  RR="$(get_conf RR)"
  TYPE="$(get_conf TYPE)"
  TTL="$(get_conf TTL)"
  LINE_RAW="$(get_conf LINE)"
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

  parse_line_list
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

run_cfst_and_pick_ip() {
  cd "${FOLDER}"
  [[ ! -x "./cfst" ]] && err "cfst binary not found or not executable: ${FOLDER}/cfst" && exit 1

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

  CONTENT="$(awk -F',' 'NR==2 {print $1; exit}' "${RESULT_FILE}")"
  if [[ -z "${CONTENT}" ]]; then
    log "No valid IP from CFST result, skip DDNS update."
    exit 0
  fi

  log "Selected IP: ${CONTENT}"
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

find_record_id_for_line() {
  local line="$1"
  local sub_domain resp api_code api_msg
  CURRENT_RECORD_ID=""

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
    err "AliDNS query failed: ${api_code} ${api_msg}"
    err "Response: ${resp}"
    exit 1
  fi

  CURRENT_RECORD_ID="$(printf '%s' "${resp}" | python3 -c '
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

for rec in records:
    rid = str(rec.get("RecordId", "")).strip()
    candidates = [
        str(rec.get("Line", "")).strip().lower(),
        str(rec.get("LineCode", "")).strip().lower(),
        str(rec.get("LineKey", "")).strip().lower(),
        str(rec.get("LineName", "")).strip().lower(),
    ]
    if target in candidates:
        print(rid)
        raise SystemExit(0)

print("")
' "${line}")"
}

upsert_record_for_line() {
  local line="$1"
  local record_id="$2"
  local resp

  if [[ -n "${record_id}" ]]; then
    log "Updating AliDNS record for Line=${line}: RecordId=${record_id}"
    resp="$(alidns_request \
      "UpdateDomainRecord" \
      "RecordId=${record_id}" \
      "RR=${RR}" \
      "Type=${TYPE}" \
      "Value=${CONTENT}" \
      "TTL=${TTL}" \
      "Line=${line}")"
  else
    log "No existing record for Line=${line}, creating new AliDNS record."
    resp="$(alidns_request \
      "AddDomainRecord" \
      "DomainName=${DOMAIN_NAME}" \
      "RR=${RR}" \
      "Type=${TYPE}" \
      "Value=${CONTENT}" \
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

  log "AliDNS upsert success. Line=${line} RequestId=${req} RecordId=${rid}"
}

process_lines() {
  local line record_id
  local single_line=0
  if [[ ${#LINE_LIST[@]} -eq 1 ]]; then
    single_line=1
  fi

  if [[ -n "${CONFIG_RECORD_ID}" && ${single_line} -eq 0 ]]; then
    log "RECORD_ID is set but LINE has multiple values; ignore RECORD_ID and auto-match each line."
  fi

  for line in "${LINE_LIST[@]}"; do
    record_id=""
    if [[ -n "${CONFIG_RECORD_ID}" && ${single_line} -eq 1 ]]; then
      record_id="${CONFIG_RECORD_ID}"
      log "Using RECORD_ID from config for Line=${line}: ${record_id}"
    else
      find_record_id_for_line "${line}"
      record_id="${CURRENT_RECORD_ID}"
    fi
    upsert_record_for_line "${line}" "${record_id}"
  done
}

read_config
run_cfst_and_pick_ip
process_lines
