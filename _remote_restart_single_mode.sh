cd /root/coitd
if [ -f /root/coitd/webui.pid ]; then
  oldpid=$(cat /root/coitd/webui.pid)
  if [ -n "$oldpid" ]; then
    kill "$oldpid" >/dev/null 2>&1 || true
  fi
fi
nohup python3 /root/coitd/scripts/webui/cfst_web_console.py --host 0.0.0.0 --port 8088 --state-dir /root/coitd/.cfst_jobs_web >/root/coitd/webui.log 2>&1 &
echo $! >/root/coitd/webui.pid
sleep 1
curl -s http://127.0.0.1:8088/healthz