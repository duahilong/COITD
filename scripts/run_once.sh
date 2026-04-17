#!/usr/bin/env bash
set -u
set -o pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
CONFIG_FILE="${CONFIG_FILE:-${ROOT_DIR}/config/config.env}"

EXIT_OK=0
EXIT_LOCKED=10
EXIT_CONFIG_INVALID=11
EXIT_EXEC_TIMEOUT=12
EXIT_RESULT_NOT_FOUND=13
EXIT_RESULT_PARSE_ERROR=14
EXIT_STATE_WRITE_ERROR=15
EXIT_UNKNOWN_ERROR=16

OUTPUT_MODE="json"
SUBCOMMAND=""
HISTORY_LIMIT=20
CLI_ERROR=""
TRACE_ID=""
LOCK_ACQUIRED=0

SCRIPT_VERSION="1.0.0"
SCHEMA_VERSION="1.0"
CFST_BIN=""
CFST_WORKDIR=""
RESULT_FILE=""
IP_VERSION=""
PRIMARY_TEST_URL=""
CFST_ARGS=""
LOCK_FILE=""
LOCK_META_FILE=""
STATE_FILE=""
HISTORY_FILE=""
LOG_FILE=""
RUN_TIMEOUT_SEC=900
HISTORY_MAX_LINES=100000
LOG_ROTATE_SIZE_MB=100
SCHEDULE_INTERVAL_MIN=20

declare -a CFST_ARGV=()
declare -a VALIDATION_ERRORS=()

BEST_IP=""
BEST_COLO=""
BEST_LATENCY_MS="0"
BEST_SPEED_MBPS="0"
BEST_TEST_URL=""
BEST_RAW_LINE=""

iso_now() { date +"%Y-%m-%dT%H:%M:%S%z" | sed -E 's/([+-][0-9]{2})([0-9]{2})$/\1:\2/'; }
epoch_ms() { date +%s%3N 2>/dev/null || printf '%s000' "$(date +%s)"; }

trace_id() {
  local r
  r="$(LC_ALL=C tr -dc 'a-z0-9' </dev/urandom 2>/dev/null | head -c 4 || true)"
  [[ -z "${r}" ]] && r="$(printf '%04x' "$((RANDOM % 65536))")"
  printf 'cfst-%s-%s' "$(date +%Y%m%d%H%M%S)" "${r}"
}

jesc() {
  local s="${1:-}"
  s="${s//\\/\\\\}"; s="${s//\"/\\\"}"; s="${s//$'\n'/\\n}"; s="${s//$'\r'/\\r}"; s="${s//$'\t'/\\t}"
  printf '%s' "${s}"
}

emit() {
  local ok="$1" code="$2" msg="$3" data="${4:-{}}" rc="$5" ts
  ts="$(iso_now)"
  if [[ "${OUTPUT_MODE}" == "plain" ]]; then
    printf 'ok=%s code=%s message=%s ts=%s traceId=%s\n' "${ok}" "${code}" "${msg}" "${ts}" "${TRACE_ID}"
    if command -v jq >/dev/null 2>&1; then printf '%s\n' "${data}" | jq .; else printf '%s\n' "${data}"; fi
  else
    if command -v jq >/dev/null 2>&1; then
      jq -cn --argjson ok "${ok}" --arg code "${code}" --arg message "${msg}" --argjson data "${data}" --arg ts "${ts}" --arg traceId "${TRACE_ID}" \
        '{ok:$ok,code:$code,message:$message,data:$data,ts:$ts,traceId:$traceId}'
    else
      printf '{"ok":%s,"code":"%s","message":"%s","data":%s,"ts":"%s","traceId":"%s"}\n' \
        "${ok}" "$(jesc "${code}")" "$(jesc "${msg}")" "${data}" "$(jesc "${ts}")" "$(jesc "${TRACE_ID}")"
    fi
  fi
  return "${rc}"
}

trim() { local v="${1:-}"; v="${v#"${v%%[![:space:]]*}"}"; v="${v%"${v##*[![:space:]]}"}"; printf '%s' "${v}"; }
add_verr() { VALIDATION_ERRORS+=("$1"); }

verr_json() {
  [[ ${#VALIDATION_ERRORS[@]} -eq 0 ]] && { printf '[]'; return 0; }
  if command -v jq >/dev/null 2>&1; then
    printf '%s\n' "${VALIDATION_ERRORS[@]}" | jq -Rsc 'split("\n") | map(select(length>0))'
    return 0
  fi
  local first=1 item
  printf '['
  for item in "${VALIDATION_ERRORS[@]}"; do
    (( first == 0 )) && printf ','
    first=0
    printf '"%s"' "$(jesc "${item}")"
  done
  printf ']'
}

parse_cfst_argv() { CFST_ARGV=(); [[ -n "${CFST_ARGS}" ]] && read -r -a CFST_ARGV <<< "${CFST_ARGS}"; }

has_opt() {
  local f="$1" t
  for t in "${CFST_ARGV[@]}"; do [[ "${t}" == "${f}" || "${t}" == "${f}="* ]] && return 0; done
  return 1
}

opt_val() {
  local f="$1" i=0 t
  while (( i < ${#CFST_ARGV[@]} )); do
    t="${CFST_ARGV[i]}"
    [[ "${t}" == "${f}" ]] && { printf '%s' "${CFST_ARGV[i+1]:-}"; return 0; }
    [[ "${t}" == "${f}="* ]] && { printf '%s' "${t#${f}=}"; return 0; }
    i=$((i+1))
  done
  printf ''
}

load_config() {
  VALIDATION_ERRORS=()
  [[ -f "${CONFIG_FILE}" ]] || { add_verr "config file not found: ${CONFIG_FILE}"; return 1; }
  # shellcheck source=/dev/null
  . "${CONFIG_FILE}" || { add_verr "failed to load config: ${CONFIG_FILE}"; return 1; }

  : "${CFST_BIN:=/opt/cfst-collector/bin/CloudflareST}"
  : "${CFST_WORKDIR:=/opt/cfst-collector/bin}"
  : "${RESULT_FILE:=/opt/cfst-collector/data/result.csv}"
  : "${IP_VERSION:=4}"
  : "${PRIMARY_TEST_URL:=}"
  : "${CFST_ARGS:=}"
  : "${LOCK_FILE:=/opt/cfst-collector/data/run.lock}"
  : "${LOCK_META_FILE:=/opt/cfst-collector/data/run.lock.meta.json}"
  : "${STATE_FILE:=/opt/cfst-collector/data/state.json}"
  : "${HISTORY_FILE:=/opt/cfst-collector/data/history.jsonl}"
  : "${LOG_FILE:=/opt/cfst-collector/logs/app.log}"
  : "${SCRIPT_VERSION:=1.0.0}"
  : "${SCHEMA_VERSION:=1.0}"
  : "${RUN_TIMEOUT_SEC:=900}"
  : "${HISTORY_MAX_LINES:=100000}"
  : "${LOG_ROTATE_SIZE_MB:=100}"
  : "${SCHEDULE_INTERVAL_MIN:=20}"
  parse_cfst_argv
  return 0
}

ensure_parent_writable() {
  local p d; p="$1"; d="$(dirname "${p}")"
  if [[ -d "${d}" ]]; then [[ -w "${d}" ]] || add_verr "directory not writable: ${d}"; return 0; fi
  mkdir -p "${d}" 2>/dev/null || { add_verr "directory cannot be created: ${d}"; return 0; }
  [[ -w "${d}" ]] || add_verr "directory not writable: ${d}"
}

validate_config() {
  VALIDATION_ERRORS=()
  local c; for c in flock timeout awk sed grep jq; do command -v "${c}" >/dev/null 2>&1 || add_verr "required command missing: ${c}"; done
  [[ -x "${CFST_BIN}" ]] || add_verr "CFST_BIN is not executable: ${CFST_BIN}"
  [[ -d "${CFST_WORKDIR}" ]] || add_verr "CFST_WORKDIR not found: ${CFST_WORKDIR}"
  [[ -w "${CFST_WORKDIR}" ]] || add_verr "CFST_WORKDIR not writable: ${CFST_WORKDIR}"

  ensure_parent_writable "${RESULT_FILE}"; ensure_parent_writable "${LOCK_FILE}"; ensure_parent_writable "${LOCK_META_FILE}"
  ensure_parent_writable "${STATE_FILE}"; ensure_parent_writable "${HISTORY_FILE}"; ensure_parent_writable "${LOG_FILE}"

  [[ "${RUN_TIMEOUT_SEC}" =~ ^[0-9]+$ ]] && (( RUN_TIMEOUT_SEC >= 60 && RUN_TIMEOUT_SEC <= 3600 )) || add_verr "RUN_TIMEOUT_SEC must be integer in [60,3600]"
  [[ "${IP_VERSION}" == "4" || "${IP_VERSION}" == "6" ]] || add_verr "IP_VERSION must be 4 or 6"
  [[ "${SCHEDULE_INTERVAL_MIN}" =~ ^[0-9]+$ ]] && (( SCHEDULE_INTERVAL_MIN >= 10 )) || add_verr "SCHEDULE_INTERVAL_MIN must be >= 10"
  [[ "${HISTORY_MAX_LINES}" =~ ^[0-9]+$ ]] && (( HISTORY_MAX_LINES >= 1 )) || add_verr "HISTORY_MAX_LINES must be >= 1"
  [[ "${LOG_ROTATE_SIZE_MB}" =~ ^[0-9]+$ ]] && (( LOG_ROTATE_SIZE_MB >= 1 )) || add_verr "LOG_ROTATE_SIZE_MB must be >= 1"

  [[ ${#CFST_ARGV[@]} -gt 0 ]] || add_verr "CFST_ARGS cannot be empty"
  has_opt "-httping" || add_verr "CFST_ARGS must contain -httping"
  has_opt "-dd" && add_verr "CFST_ARGS must not contain -dd in phase 1"
  has_opt "-cfcolo" && ! has_opt "-httping" && add_verr "-cfcolo requires -httping"
  local o u; o="$(opt_val "-o")"; u="$(opt_val "-url")"
  [[ -n "${o}" ]] || add_verr "CFST_ARGS must contain -o <RESULT_FILE>"
  [[ "${o}" == "${RESULT_FILE}" ]] || add_verr "CFST_ARGS -o must equal RESULT_FILE"
  [[ -n "${u}" ]] || add_verr "CFST_ARGS must contain -url"
  [[ "${u}" =~ ^https?://[^[:space:]]+$ ]] || add_verr "CFST_ARGS -url must be valid http/https URL"

  [[ ${#VALIDATION_ERRORS[@]} -eq 0 ]]
}

sha256_of() {
  local in="${1:-}"
  if command -v sha256sum >/dev/null 2>&1; then printf 'sha256:%s' "$(printf '%s' "${in}" | sha256sum | awk '{print $1}')"; return 0; fi
  if command -v shasum >/dev/null 2>&1; then printf 'sha256:%s' "$(printf '%s' "${in}" | shasum -a 256 | awk '{print $1}')"; return 0; fi
  printf 'sha256:unknown'
}

atomic_write() { local p="$1" c="$2" t="${p}.tmp.$$"; printf '%s\n' "${c}" > "${t}" && mv -f "${t}" "${p}" || { rm -f "${t}" 2>/dev/null || true; return 1; }; }

log_event() {
  [[ -n "${LOG_FILE}" ]] || return 0
  mkdir -p "$(dirname "${LOG_FILE}")" 2>/dev/null || true
  if [[ -f "${LOG_FILE}" && "${LOG_ROTATE_SIZE_MB}" =~ ^[0-9]+$ ]]; then
    local b m; b="$(wc -c < "${LOG_FILE}" | tr -d '[:space:]')"; m=$((LOG_ROTATE_SIZE_MB * 1024 * 1024))
    [[ "${b}" =~ ^[0-9]+$ ]] && (( b >= m )) && mv -f "${LOG_FILE}" "${LOG_FILE}.1" 2>/dev/null || true
  fi
  jq -cn --arg ts "$(iso_now)" --arg level "${1}" --arg message "${2}" --arg traceId "${TRACE_ID}" --argjson context "${3:-{}}" \
    '{ts:$ts,level:$level,message:$message,traceId:$traceId,context:$context}' >> "${LOG_FILE}" 2>/dev/null || true
}

release_lock() {
  if (( LOCK_ACQUIRED == 1 )); then
    rm -f "${LOCK_META_FILE}" 2>/dev/null || true
    flock -u 200 2>/dev/null || true
    LOCK_ACQUIRED=0
  fi
}

acquire_lock() {
  mkdir -p "$(dirname "${LOCK_FILE}")" 2>/dev/null || true
  exec 200>"${LOCK_FILE}" || return 2
  flock -n 200 || return 1
  LOCK_ACQUIRED=1
  local m; m="$(jq -cn --argjson pid "$$" --arg startAt "$(iso_now)" --arg traceId "${TRACE_ID}" --arg command "run-once" \
    '{pid:$pid,startAt:$startAt,traceId:$traceId,command:$command}')"
  atomic_write "${LOCK_META_FILE}" "${m}" || { release_lock; return 2; }
  return 0
}

ipv4_ok() { [[ "${1}" =~ ^((25[0-5]|2[0-4][0-9]|1[0-9]{2}|[1-9]?[0-9])\.){3}(25[0-5]|2[0-4][0-9]|1[0-9]{2}|[1-9]?[0-9])$ ]]; }
ipv6_ok() { [[ "${1}" =~ ^[0-9a-fA-F:]+$ ]]; }

speed_to_mbps() {
  local s="$1"
  [[ -z "${s}" ]] && { printf '0'; return 0; }
  [[ "${s}" =~ ^([0-9]+([.][0-9]+)?)\s*(MB/s|KB/s|GB/s)$ ]] || return 1
  awk -v v="${BASH_REMATCH[1]}" -v u="${BASH_REMATCH[3]}" 'BEGIN{if(u=="KB/s")printf "%.6f",v/1024;else if(u=="MB/s")printf "%.6f",v;else printf "%.6f",v*1024}'
}

extract_best() {
  [[ -f "${RESULT_FILE}" ]] || return "${EXIT_RESULT_NOT_FOUND}"
  local line c1 c2 c3 c4 c5 c6 rest ip lat sp cc
  line="$(sed -n '2p' "${RESULT_FILE}" | tr -d '\r')"
  [[ -n "$(trim "${line}")" ]] || return "${EXIT_RESULT_NOT_FOUND}"
  IFS=',' read -r c1 c2 c3 c4 c5 c6 rest <<< "${line}"
  ip="$(trim "${c1}")"; lat="$(trim "${c3}")"; sp="$(trim "${c6}")"

  if [[ "${IP_VERSION}" == "4" ]]; then ipv4_ok "${ip}" || return "${EXIT_RESULT_PARSE_ERROR}"; else ipv6_ok "${ip}" || return "${EXIT_RESULT_PARSE_ERROR}"; fi
  [[ "${lat}" =~ ^([0-9]+([.][0-9]+)?)\s*ms$ ]] || return "${EXIT_RESULT_PARSE_ERROR}"
  BEST_LATENCY_MS="${BASH_REMATCH[1]}"
  BEST_SPEED_MBPS="$(speed_to_mbps "${sp}")" || return "${EXIT_RESULT_PARSE_ERROR}"

  cc="$(trim "${c4}")"; if [[ ! "${cc}" =~ ^[A-Za-z]{3}$ ]]; then cc="$(trim "${c5}")"; fi
  [[ "${cc}" =~ ^[A-Za-z]{3}$ ]] && BEST_COLO="${cc^^}" || BEST_COLO=""
  BEST_IP="${ip}"
  BEST_RAW_LINE="${line}"
  BEST_TEST_URL="$(opt_val "-url")"; [[ -z "${BEST_TEST_URL}" ]] && BEST_TEST_URL="${PRIMARY_TEST_URL}"
  return 0
}

trim_history() {
  [[ -f "${HISTORY_FILE}" ]] || return 0
  local n t; n="$(wc -l < "${HISTORY_FILE}" | tr -d '[:space:]')"
  [[ "${n}" =~ ^[0-9]+$ ]] || return 1
  (( n <= HISTORY_MAX_LINES )) && return 0
  t="${HISTORY_FILE}.tmp.$$"
  tail -n "${HISTORY_MAX_LINES}" "${HISTORY_FILE}" > "${t}" && mv -f "${t}" "${HISTORY_FILE}" || { rm -f "${t}" 2>/dev/null || true; return 1; }
  log_event "INFO" "history trimmed" "$(jq -cn --argjson history_trimmed true --argjson before_lines "${n}" --argjson after_lines "${HISTORY_MAX_LINES}" \
    '{history_trimmed:$history_trimmed,before_lines:$before_lines,after_lines:$after_lines}')"
}

run_once_cmd() {
  local started ended dur lock_rc=0 cfst_rc=0 parse_rc=0 run_at st hs data
  started="$(epoch_ms)"
  load_config || { local e; e="$(verr_json)"; emit false "CONFIG_INVALID" "config load failed" "$(jq -cn --arg configFile "${CONFIG_FILE}" --argjson errors "${e}" '{configFile:$configFile,errors:$errors}')" "${EXIT_CONFIG_INVALID}"; return $?; }
  validate_config || { local e; e="$(verr_json)"; emit false "CONFIG_INVALID" "config validation failed" "$(jq -cn --arg configFile "${CONFIG_FILE}" --argjson errors "${e}" '{configFile:$configFile,errors:$errors}')" "${EXIT_CONFIG_INVALID}"; return $?; }

  acquire_lock || lock_rc=$?
  (( lock_rc == 1 )) && { emit false "LOCKED" "another run is in progress" '{}' "${EXIT_LOCKED}"; return $?; }
  (( lock_rc != 0 )) && { emit false "STATE_WRITE_ERROR" "failed to prepare lock metadata" '{}' "${EXIT_STATE_WRITE_ERROR}"; return $?; }

  log_event "INFO" "run-once started" "{}"
  rm -f "${RESULT_FILE}" 2>/dev/null || true
  parse_cfst_argv
  ( cd "${CFST_WORKDIR}" && timeout "${RUN_TIMEOUT_SEC}" "${CFST_BIN}" "${CFST_ARGV[@]}" ) || cfst_rc=$?
  (( cfst_rc == 124 || cfst_rc == 137 )) && { log_event "ERROR" "CloudflareST execution timeout" "{}"; emit false "EXEC_TIMEOUT" "CloudflareST execution timeout" '{}' "${EXIT_EXEC_TIMEOUT}"; return $?; }
  (( cfst_rc != 0 )) && log_event "WARN" "CloudflareST exited non-zero, parse result anyway" "$(jq -cn --argjson cfstExitCode "${cfst_rc}" '{cfstExitCode:$cfstExitCode}')"

  extract_best || parse_rc=$?
  (( parse_rc == EXIT_RESULT_NOT_FOUND )) && { emit false "RESULT_NOT_FOUND" "result file not found or empty" '{}' "${EXIT_RESULT_NOT_FOUND}"; return $?; }
  (( parse_rc == EXIT_RESULT_PARSE_ERROR )) && { emit false "RESULT_PARSE_ERROR" "result parsing failed" '{}' "${EXIT_RESULT_PARSE_ERROR}"; return $?; }
  (( parse_rc != 0 )) && { emit false "UNKNOWN_ERROR" "unknown parse error" '{}' "${EXIT_UNKNOWN_ERROR}"; return $?; }

  run_at="$(iso_now)"
  st="$(jq -cn --arg schemaVersion "${SCHEMA_VERSION}" --arg lastRunAt "${run_at}" --arg bestIp "${BEST_IP}" --arg colo "${BEST_COLO}" \
    --arg latencyMs "${BEST_LATENCY_MS}" --arg speedMBps "${BEST_SPEED_MBPS}" --arg testUrl "${BEST_TEST_URL}" --arg cfstArgsHash "$(sha256_of "${CFST_ARGS}")" \
    --arg rawLine "${BEST_RAW_LINE}" \
    '{schemaVersion:$schemaVersion,isRunning:false,lastRunAt:$lastRunAt,lastRunStatus:"success",lastErrorCode:"",bestIp:$bestIp,colo:$colo,latencyMs:($latencyMs|tonumber),speedMBps:($speedMBps|tonumber),testUrl:$testUrl,cfstArgsHash:$cfstArgsHash,rawLine:$rawLine,source:"CloudflareSpeedTest"}')"
  atomic_write "${STATE_FILE}" "${st}" || { emit false "STATE_WRITE_ERROR" "failed to write state file" '{}' "${EXIT_STATE_WRITE_ERROR}"; return $?; }

  hs="$(jq -cn --arg runAt "${run_at}" --arg traceId "${TRACE_ID}" --arg bestIp "${BEST_IP}" --arg colo "${BEST_COLO}" --arg latencyMs "${BEST_LATENCY_MS}" \
    --arg speedMBps "${BEST_SPEED_MBPS}" --arg testUrl "${BEST_TEST_URL}" --arg rawLine "${BEST_RAW_LINE}" \
    '{runAt:$runAt,traceId:$traceId,bestIp:$bestIp,colo:$colo,latencyMs:($latencyMs|tonumber),speedMBps:($speedMBps|tonumber),testUrl:$testUrl,rawLine:$rawLine}')"
  printf '%s\n' "${hs}" >> "${HISTORY_FILE}" || { emit false "STATE_WRITE_ERROR" "failed to append history file" '{}' "${EXIT_STATE_WRITE_ERROR}"; return $?; }
  trim_history || { emit false "STATE_WRITE_ERROR" "failed to trim history file" '{}' "${EXIT_STATE_WRITE_ERROR}"; return $?; }

  ended="$(epoch_ms)"; dur=$((ended - started))
  data="$(jq -cn --arg bestIp "${BEST_IP}" --arg colo "${BEST_COLO}" --arg latencyMs "${BEST_LATENCY_MS}" --arg speedMBps "${BEST_SPEED_MBPS}" \
    --arg testUrl "${BEST_TEST_URL}" --arg resultFile "${RESULT_FILE}" --arg stateFile "${STATE_FILE}" --arg historyFile "${HISTORY_FILE}" \
    --argjson durationMs "${dur}" --argjson cfstExitCode "${cfst_rc}" \
    '{bestIp:$bestIp,colo:$colo,latencyMs:($latencyMs|tonumber),speedMBps:($speedMBps|tonumber),testUrl:$testUrl,resultFile:$resultFile,stateFile:$stateFile,historyFile:$historyFile,durationMs:$durationMs,cfstExitCode:$cfstExitCode}')"
  log_event "INFO" "run-once completed" "${data}"
  emit true "OK" "success" "${data}" "${EXIT_OK}"
}

status_cmd() {
  local st lk pid="" run=false data
  load_config || { local e; e="$(verr_json)"; emit false "CONFIG_INVALID" "config load failed" "$(jq -cn --arg configFile "${CONFIG_FILE}" --argjson errors "${e}" '{configFile:$configFile,errors:$errors}')" "${EXIT_CONFIG_INVALID}"; return $?; }
  if [[ -f "${STATE_FILE}" ]]; then st="$(jq -c . "${STATE_FILE}" 2>/dev/null)" || { emit false "STATE_WRITE_ERROR" "state file is not valid JSON" "$(jq -cn --arg stateFile "${STATE_FILE}" '{stateFile:$stateFile}')" "${EXIT_STATE_WRITE_ERROR}"; return $?; }
  else st="$(jq -cn --arg schemaVersion "${SCHEMA_VERSION}" '{schemaVersion:$schemaVersion,isRunning:false,lastRunAt:"",lastRunStatus:"",lastErrorCode:"",bestIp:"",colo:"",latencyMs:0,speedMBps:0,testUrl:"",cfstArgsHash:"",rawLine:"",source:"CloudflareSpeedTest"}')"; fi
  lk='null'
  if [[ -f "${LOCK_META_FILE}" ]]; then
    lk="$(jq -c . "${LOCK_META_FILE}" 2>/dev/null || printf 'null')"
    pid="$(jq -r '.pid // empty' "${LOCK_META_FILE}" 2>/dev/null || true)"
    [[ -n "${pid}" ]] && kill -0 "${pid}" 2>/dev/null && run=true
  fi
  data="$(jq -cn --argjson isRunning "${run}" --argjson state "${st}" --argjson lockMeta "${lk}" --arg stateFile "${STATE_FILE}" --arg historyFile "${HISTORY_FILE}" \
    --arg lockFile "${LOCK_FILE}" --arg lockMetaFile "${LOCK_META_FILE}" \
    '{isRunning:$isRunning,state:$state,lockMeta:$lockMeta,files:{stateFile:$stateFile,historyFile:$historyFile,lockFile:$lockFile,lockMetaFile:$lockMetaFile}}')"
  emit true "OK" "success" "${data}" "${EXIT_OK}"
}

history_cmd() {
  local items cnt data
  load_config || { local e; e="$(verr_json)"; emit false "CONFIG_INVALID" "config load failed" "$(jq -cn --arg configFile "${CONFIG_FILE}" --argjson errors "${e}" '{configFile:$configFile,errors:$errors}')" "${EXIT_CONFIG_INVALID}"; return $?; }
  [[ -f "${HISTORY_FILE}" ]] && items="$(tail -n "${HISTORY_LIMIT}" "${HISTORY_FILE}" 2>/dev/null | jq -Rsc 'split("\n") | map(select(length>0) | fromjson?) | map(select(. != null))')" || items='[]'
  cnt="$(jq -r 'length' <<< "${items}")"
  data="$(jq -cn --argjson limit "${HISTORY_LIMIT}" --argjson count "${cnt}" --argjson items "${items}" '{limit:$limit,count:$count,items:$items}')"
  emit true "OK" "success" "${data}" "${EXIT_OK}"
}

validate_cmd() {
  load_config || { local e; e="$(verr_json)"; emit false "CONFIG_INVALID" "config load failed" "$(jq -cn --arg configFile "${CONFIG_FILE}" --argjson errors "${e}" '{configFile:$configFile,errors:$errors}')" "${EXIT_CONFIG_INVALID}"; return $?; }
  validate_config || { local e; e="$(verr_json)"; emit false "CONFIG_INVALID" "config validation failed" "$(jq -cn --arg configFile "${CONFIG_FILE}" --argjson errors "${e}" '{configFile:$configFile,errors:$errors}')" "${EXIT_CONFIG_INVALID}"; return $?; }
  emit true "OK" "success" "$(jq -cn --arg configFile "${CONFIG_FILE}" --arg cfstBin "${CFST_BIN}" --arg resultFile "${RESULT_FILE}" --arg ipVersion "${IP_VERSION}" \
    '{configFile:$configFile,cfstBin:$cfstBin,resultFile:$resultFile,ipVersion:$ipVersion}')" "${EXIT_OK}"
}

self_check_cmd() {
  local deps='[]' dep_ok=true overall_ok=true cfg_ok=true dep e data d
  for dep in bash flock timeout awk sed grep jq; do
    if command -v "${dep}" >/dev/null 2>&1; then deps="$(jq -cn --argjson a "${deps}" --arg name "${dep}" '$a + [{"name":$name,"ok":true}]')"
    else deps="$(jq -cn --argjson a "${deps}" --arg name "${dep}" '$a + [{"name":$name,"ok":false}]')"; dep_ok=false; overall_ok=false; fi
  done
  load_config || { cfg_ok=false; overall_ok=false; e="$(verr_json)"; }
  if [[ "${cfg_ok}" == "true" ]]; then validate_config || { cfg_ok=false; overall_ok=false; e="$(verr_json)"; }; fi
  [[ -z "${e:-}" ]] && e='[]'
  d=''; [[ "${cfg_ok}" == "true" ]] && d="${STATE_FILE}" || d=""
  data="$(jq -cn --argjson dependencies "${deps}" --argjson dependenciesValid "${dep_ok}" --argjson configValid "${cfg_ok}" --argjson configErrors "${e}" \
    --arg configFile "${CONFIG_FILE}" --arg stateFile "${d}" '{dependencies:$dependencies,dependenciesValid:$dependenciesValid,configValid:$configValid,configErrors:$configErrors,files:{configFile:$configFile,stateFile:$stateFile}}')"
  [[ "${overall_ok}" == "true" && "${cfg_ok}" == "true" ]] && emit true "OK" "success" "${data}" "${EXIT_OK}" || emit false "CONFIG_INVALID" "self-check failed" "${data}" "${EXIT_CONFIG_INVALID}"
}

version_cmd() {
  [[ -f "${CONFIG_FILE}" ]] && load_config >/dev/null 2>&1 || true
  emit true "OK" "success" "$(jq -cn --arg scriptVersion "${SCRIPT_VERSION}" --arg schemaVersion "${SCHEMA_VERSION}" --arg configFile "${CONFIG_FILE}" \
    '{scriptVersion:$scriptVersion,schemaVersion:$schemaVersion,configFile:$configFile}')" "${EXIT_OK}"
}

parse_mode() { case "${1}" in --json) OUTPUT_MODE="json";; --plain) OUTPUT_MODE="plain";; *) return 1;; esac; }

parse_args() {
  [[ $# -ge 1 ]] || { CLI_ERROR="missing subcommand"; return 1; }
  SUBCOMMAND="$1"; shift
  case "${SUBCOMMAND}" in run-once|status|validate-config|self-check|version)
    while [[ $# -gt 0 ]]; do parse_mode "$1" || { CLI_ERROR="unknown argument: $1"; return 1; }; shift; done
    ;;
  history)
    local seen=false
    while [[ $# -gt 0 ]]; do
      case "$1" in
        --limit) shift; [[ $# -gt 0 ]] || { CLI_ERROR="missing value for --limit"; return 1; }; HISTORY_LIMIT="$1"; seen=true ;;
        --json|--plain) parse_mode "$1" || true ;;
        *) CLI_ERROR="unknown argument: $1"; return 1 ;;
      esac
      shift
    done
    [[ "${seen}" == "true" ]] || { CLI_ERROR="history requires --limit <1-1000>"; return 1; }
    [[ "${HISTORY_LIMIT}" =~ ^[0-9]+$ ]] && (( HISTORY_LIMIT >= 1 && HISTORY_LIMIT <= 1000 )) || { CLI_ERROR="--limit must be integer in [1,1000]"; return 1; }
    ;;
  *) CLI_ERROR="unknown subcommand: ${SUBCOMMAND}"; return 1 ;;
  esac
}

usage_json() {
  jq -cn --arg usage "run_once.sh run-once [--json|--plain]\nrun_once.sh status [--json|--plain]\nrun_once.sh history --limit <1-1000> [--json|--plain]\nrun_once.sh validate-config [--json|--plain]\nrun_once.sh self-check [--json|--plain]\nrun_once.sh version [--json|--plain]" '{usage:$usage}'
}

main() {
  TRACE_ID="$(trace_id)"
  trap release_lock EXIT
  parse_args "$@" || { emit false "CONFIG_INVALID" "invalid command arguments" "$(jq -cn --arg error "${CLI_ERROR}" --argjson u "$(usage_json)" '{error:$error,usage:$u.usage}')" "${EXIT_CONFIG_INVALID}"; return $?; }
  case "${SUBCOMMAND}" in
    run-once) run_once_cmd ;;
    status) status_cmd ;;
    history) history_cmd ;;
    validate-config) validate_cmd ;;
    self-check) self_check_cmd ;;
    version) version_cmd ;;
    *) emit false "UNKNOWN_ERROR" "unknown error" '{}' "${EXIT_UNKNOWN_ERROR}" ;;
  esac
}

main "$@"
