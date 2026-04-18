# CFST 任务控制器（Web 适配层）

脚本：`scripts/cfst/cfst_job_controller.py`

这个控制器用于给后续 Web 页面提供稳定接口，支持：

- 后台启动任务
- 查询任务状态
- 停止任务
- 拉取日志尾部
- 查看历史任务
- 生成 crontab 模板

## 1) 启动任务（后台）

```bash
python3 scripts/cfst/cfst_job_controller.py start \
  -c scripts/cfst/cfst_config.jp.jsonc \
  --state-dir .cfst_jobs \
  --cwd . \
  --json
```

返回示例（JSON）：

```json
{
  "ok": true,
  "action": "start",
  "run_id": "20260418-193000-a1b2c3",
  "status": "running",
  "pid": 12345,
  "log_file": "/root/coitd/.cfst_jobs/runs/20260418-193000-a1b2c3/run.log",
  "summary_file": "/root/coitd/.cfst_jobs/runs/20260418-193000-a1b2c3/summary.json"
}
```

## 2) 查询状态

```bash
python3 scripts/cfst/cfst_job_controller.py status \
  --state-dir .cfst_jobs \
  --run-id 20260418-193000-a1b2c3 \
  --json
```

状态字段可能为：`starting` `running` `success` `failed` `stopping` `stopped`

## 3) 停止任务

```bash
python3 scripts/cfst/cfst_job_controller.py stop \
  --state-dir .cfst_jobs \
  --run-id 20260418-193000-a1b2c3 \
  --timeout-sec 8 \
  --json
```

## 4) 查看日志（尾部）

```bash
python3 scripts/cfst/cfst_job_controller.py logs \
  --state-dir .cfst_jobs \
  --run-id 20260418-193000-a1b2c3 \
  --lines 120
```

或 JSON：

```bash
python3 scripts/cfst/cfst_job_controller.py logs \
  --state-dir .cfst_jobs \
  --run-id 20260418-193000-a1b2c3 \
  --lines 120 \
  --json
```

## 5) 列出任务

```bash
python3 scripts/cfst/cfst_job_controller.py list \
  --state-dir .cfst_jobs \
  --limit 20 \
  --json
```

## 6) 生成定时任务模板（cron）

```bash
python3 scripts/cfst/cfst_job_controller.py cron-template \
  -c scripts/cfst/cfst_config.jp.jsonc \
  --state-dir .cfst_jobs \
  --cron-expr "*/30 * * * *" \
  --json
```

拿到模板后再写入 crontab（由运维层控制）。

## 目录结构

默认状态目录：`.cfst_jobs`

每个 run 会创建：

- `runs/<run_id>/meta.json`：任务元信息（状态、PID、时间戳）
- `runs/<run_id>/run.log`：完整运行日志
- `runs/<run_id>/summary.json`：结构化结果（来自 `cfst_config_runner.py --summary-json`）

## 给 Web 对接的建议

- 启动任务：调用 `start --json`，存储 `run_id`
- 刷新状态：轮询 `status --json`
- 实时日志：轮询 `logs --json --lines N`，前端按增量渲染
- 结果展示：任务结束后读取 `status.summary.best_ip` / `best_ip_list`
- 停止按钮：调用 `stop --json`
