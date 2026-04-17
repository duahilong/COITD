set -euo pipefail
chmod +x /opt/cfst-collector/scripts/run_once.sh
cd /opt/cfst-collector
./scripts/run_once.sh run-once --json