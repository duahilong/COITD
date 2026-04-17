set -euo pipefail
chmod +x /opt/cfst-collector/scripts/run_once.sh
/opt/cfst-collector/scripts/run_once.sh version --json
/opt/cfst-collector/scripts/run_once.sh validate-config --json
/opt/cfst-collector/scripts/run_once.sh self-check --json