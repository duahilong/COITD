set -euo pipefail
chmod +x /opt/cfst-collector/scripts/run_once.sh /opt/cfst-collector/bin/CloudflareST
cd /opt/cfst-collector
./scripts/run_once.sh validate-config --json
./scripts/run_once.sh run-once --json
./scripts/run_once.sh status --json
./scripts/run_once.sh history --limit 5 --json