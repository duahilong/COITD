set -e
cd /root/coitd

echo "[S1] ???? run1"
r1=$(curl -s -X POST http://127.0.0.1:8088/api/start -H 'Content-Type: application/json' -d '{"config":"scripts/cfst/cfst_config.full.json","label":"manual-1"}')
echo "$r1"
run1=$(echo "$r1" | python3 -c "import json,sys; print(json.load(sys.stdin).get('run_id',''))")
echo "RUN1=$run1"

echo "[S2] ???????? run2??????"
code=$(curl -s -o /tmp/start2.json -w '%{http_code}' -X POST http://127.0.0.1:8088/api/start -H 'Content-Type: application/json' -d '{"config":"scripts/cfst/cfst_config.full.json","label":"manual-2"}')
echo "HTTP=$code"
cat /tmp/start2.json
echo

echo "[S3] ??? skip ?????????"
skip=$(python3 /root/coitd/scripts/cfst/cfst_job_controller.py start -c /root/coitd/scripts/cfst/cfst_config.full.json --runner /root/coitd/scripts/cfst/cfst_config_runner.py --cwd /root/coitd --state-dir /root/coitd/.cfst_jobs_web --if-busy skip --json)
echo "$skip"

echo "[S4] list ??????? running"
list1=$(curl -s 'http://127.0.0.1:8088/api/list?limit=5')
echo "$list1"

echo "[S5] ?? run1?????"
curl -s -X POST http://127.0.0.1:8088/api/stop -H 'Content-Type: application/json' -d "{\"run_id\":\"$run1\",\"timeout_sec\":10}"
echo

echo "[S6] ??????"
curl -s -X POST http://127.0.0.1:8088/api/schedule/clear -H 'Content-Type: application/json' -d '{}'
echo