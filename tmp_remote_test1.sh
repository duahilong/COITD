set -euo pipefail
/opt/cfst-collector/scripts/run_once.sh version --plain || true
/opt/cfst-collector/scripts/run_once.sh validate-config --plain || true