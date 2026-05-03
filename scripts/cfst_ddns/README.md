# CFST DDNS Workspace

This folder is an isolated workspace copy of `cfst_ddns` so future changes do not touch `CloudflareSpeedTest`.

## Files
- `cfst_ddns.sh`: Linux shell script (migrated to Aliyun DDNS API).
- `cfst_ddns.bat`: Windows batch version (copied from upstream, still Cloudflare style).
- `cfst_ddns.conf`: editable Aliyun config template.
- `cfst_ddns.conf.example`: Aliyun config example.
- `README.upstream.md`: upstream script README copy.

## Quick Start (Linux)
1. Edit `cfst_ddns.conf` and fill all required fields.
2. Ensure `FOLDER` points to a directory containing executable `cfst`.
3. Run: `bash ./cfst_ddns.sh`

## Notes
- Script runs CFST, selects top N IPs in `RESULT_FILE`, and upserts AliDNS records.
- Configure `PUSH_IP_COUNT` in `cfst_ddns.conf` to control N (default `1`).
- Script writes structured summary JSON after each run (default `state/latest.json` and `state/history/<run_id>.json`).
- Configure `ENABLE_SUMMARY` and `SUMMARY_DIR` in `cfst_ddns.conf` to control summary behavior.
- If `RECORD_ID` is empty, script auto queries record id by `RR + DOMAIN_NAME + TYPE`.
- If record does not exist, script calls `AddDomainRecord`.
