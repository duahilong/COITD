set -euo pipefail
cd /opt/cfst-collector/bin
api="https://api.github.com/repos/XIU2/CloudflareSpeedTest/releases/latest"
url=$(curl -fsSL "$api" | jq -r '.assets[] | select(.name | test("linux_amd64.*\\.tar\\.gz$")) | .browser_download_url' | head -n1)
if [ -z "$url" ]; then
  echo "No linux_amd64 asset found" >&2
  exit 1
fi
echo "DOWNLOAD_URL=$url"
curl -fL "$url" -o cfst.tar.gz
tar -xzf cfst.tar.gz
rm -f cfst.tar.gz
chmod +x CloudflareST
./CloudflareST -v || true
ls -l /opt/cfst-collector/bin