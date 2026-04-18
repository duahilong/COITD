# CFST Web Console（简版）

## 启动

在项目根目录执行：

```bash
python3 scripts/webui/cfst_web_console.py --host 0.0.0.0 --port 8088 --state-dir /root/coitd/.cfst_jobs_web
```

## 访问

- 页面：`http://<服务器IP>:8088/`
- 健康检查：`GET /healthz`

## 页面能力

- 选择配置并启动任务
- 任务状态轮询（`running/success/failed/stopped`）
- 实时日志刷新
- 任务停止
- 历史任务列表
- 单任务模式（同一时刻只允许一个任务运行）
  - 手动启动：若已有运行任务，直接拒绝并返回当前 run_id
  - 定时触发：若已有运行任务，自动跳过本次触发
- 定时任务一键设置（直接写入 Linux `crontab`）
  - 每分钟运行一次
  - 每天运行一次
  - 每小时运行一次
  - 每半小时运行一次
  - 每 N 小时运行一次
- 定时任务日志观察
  - 设置日志（是否设置成功）
  - 执行日志（定时触发是否执行成功）
- 设置反馈更明确
  - 页面会显示“是否设置成功”
  - 页面会显示当前生效 `crontab` 规则

## 主要 API（网页内部已接入）

- `GET /api/configs`
- `POST /api/start`
- `GET /api/status?run_id=...`
- `GET /api/logs?run_id=...&lines=...`
- `POST /api/stop`
- `GET /api/list?limit=...`
- `GET /api/cron-template?...`
- `GET /api/schedule/status`
- `POST /api/schedule/setup`
- `POST /api/schedule/clear`
- `GET /api/schedule/logs?lines=...`
