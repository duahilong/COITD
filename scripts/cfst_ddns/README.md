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
- Script runs CFST, picks the first IP in `RESULT_FILE`, and updates AliDNS.
- If `RECORD_ID` is empty, script auto queries record id by `RR + DOMAIN_NAME + TYPE`.
- If record does not exist, script calls `AddDomainRecord`.
