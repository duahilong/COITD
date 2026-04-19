# DDNS Web (Dedicated)

This folder contains a dedicated web page for `scripts/cfst_ddns/cfst_ddns.sh`.

## Start

```bash
python3 scripts/cfst_ddns_web/ddns_web_server.py \
  --host 0.0.0.0 \
  --port 8091 \
  --timer-name cfst-ddns.timer \
  --service-name cfst-ddns.service \
  --state-dir /root/coitd/scripts/cfst_ddns/state \
  --run-log-file /root/coitd/scripts/cfst_ddns/logs/cfst_ddns_run.log
```

## Features

- Show timer/service status and next run time
- Show latest summary (`state/latest.json`) and IP change diff
- Show latest DDNS operation table
- Show run history from `state/history/*.json`
- Show run log tail
- Support manual trigger via `systemctl start cfst-ddns.service`

