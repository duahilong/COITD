set -euo pipefail
cd /opt/cfst-collector/bin
./CloudflareST -httping -cfcolo HKG,SJC,LAX -url https://cf.xiu2.xyz/url -dn 8 -dt 8 -tl 400 -sl 0 -o /tmp/cfst_test.csv
echo "=== result head ==="
head -n 5 /tmp/cfst_test.csv || true