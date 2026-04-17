# 第一阶段开发设计（Linux）
## CloudflareSpeedTest 优选采集与网页预留（精简重排版）

## 1. 目标与边界

## 1.1 第一阶段目标（只做这两件事）
1. 在 Linux 上周期运行 CloudflareSpeedTest，获取当前优选 IP。
2. 将结果稳定持久化到本地状态与历史数据，供后续网页与 DNS 同步阶段复用。

## 1.2 明确不做
1. 不做 Cloudflare DNS 自动更新。
2. 不做对外开放的 Web 服务。
3. 不做复杂分布式编排或多节点调度。

## 1.3 实现基线
第一阶段以 XIU2 官方脚本体系为主线：
1. 先运行 CloudflareSpeedTest，再读取结果文件。
2. 取第一条有效候选作为当前优选。
3. 空结果则失败退出并保留旧状态。

参考：
- https://github.com/XIU2/CloudflareSpeedTest
- https://github.com/XIU2/CloudflareSpeedTest/blob/master/script/cfst_ddns.sh
- https://github.com/XIU2/CloudflareSpeedTest/blob/master/script/cfst_hosts.sh
- https://raw.githubusercontent.com/XIU2/CloudflareSpeedTest/master/script/README.md

---

## 2. 运行环境与部署结构

## 2.1 运行环境
1. OS：Ubuntu 22.04+/Debian 12+/CentOS Stream 9。
2. 依赖：`bash`、`flock`、`timeout`、`awk`、`sed`、`grep`、`jq`。
3. 二进制：CloudflareSpeedTest（示例 `CloudflareST`）。

## 2.2 目录结构（最终版）

```text
/opt/cfst-collector/
  bin/
    CloudflareST
  scripts/
    run_once.sh
  config/
    config.env
  data/
    result.csv
    state.json
    history.jsonl
    run.lock
    run.lock.meta.json
  logs/
    app.log
    cron.log
```

---

## 3. 配置模型（config.env）

## 3.1 推荐配置示例（可直接落地）

```bash
# CloudflareSpeedTest 基础
CFST_BIN="/opt/cfst-collector/bin/CloudflareST"
CFST_WORKDIR="/opt/cfst-collector/bin"
RESULT_FILE="/opt/cfst-collector/data/result.csv"
IP_VERSION="4"  # 4 或 6

# 输入治理
PRIMARY_TEST_URL="https://speed.example.com/cfst-20m.bin"
FALLBACK_TEST_URLS="https://speed-bak1.example.com/cfst-20m.bin,https://speed-bak2.example.com/cfst-20m.bin"
COLO_ALLOWLIST="HKG,SJC,LAX"

# CFST 参数基线（第一阶段：地区 + 下载测速）
CFST_ARGS="-httping -cfcolo HKG,SJC,LAX -url https://speed.example.com/cfst-20m.bin -dn 20 -dt 10 -tl 250 -sl 1 -o /opt/cfst-collector/data/result.csv"

# 运行控制
LOCK_FILE="/opt/cfst-collector/data/run.lock"
LOCK_META_FILE="/opt/cfst-collector/data/run.lock.meta.json"
STATE_FILE="/opt/cfst-collector/data/state.json"
HISTORY_FILE="/opt/cfst-collector/data/history.jsonl"
LOG_FILE="/opt/cfst-collector/logs/app.log"
RUN_TIMEOUT_SEC=900
HISTORY_MAX_LINES=100000
LOG_ROTATE_SIZE_MB=100

# 版本与契约
SCRIPT_VERSION="1.0.0"
SCHEMA_VERSION="1.0"
```

## 3.2 配置强约束（validate-config 必须覆盖）
1. `CFST_BIN` 存在且可执行。
2. `CFST_WORKDIR` 与各输出文件父目录可写。
3. `RUN_TIMEOUT_SEC` 在 `60~3600`。
4. `CFST_ARGS` 必须包含 `-o <RESULT_FILE>`。
5. `CFST_ARGS` 必须包含 `-httping`。
6. 若配置了 `-cfcolo`，必须同时有 `-httping`。
7. `CFST_ARGS` 禁止出现 `-dd`（第一阶段必须做下载测速）。
8. `-url` 必须为合法 `http/https` 且非空。
9. 仅允许单一 IP 类型，`IP_VERSION=4|6` 必填。
10. 调度建议间隔不低于 10 分钟。

---

## 4. 命令契约（为网页接入冻结）

## 4.1 子命令
1. `run_once.sh run-once [--json|--plain]`
2. `run_once.sh status [--json|--plain]`
3. `run_once.sh history --limit <N> [--json|--plain]`（`N: 1~1000`）
4. `run_once.sh validate-config [--json|--plain]`
5. `run_once.sh self-check [--json|--plain]`
6. `run_once.sh version [--json|--plain]`


## 4.1.1 参数语法与非法参数处理
1. 未知子命令或未知参数，统一返回：
   - `ok=false`
   - `code=CONFIG_INVALID`
   - 退出码 `11`
2. `history --limit` 仅接受整数，范围 `1~1000`，越界返回 `CONFIG_INVALID`。
3. 若未指定 `--json|--plain`，默认按 `--json` 输出。
4. `--json` 模式下即使失败也必须输出合法 JSON Envelope。

usage 示例：
```bash
run_once.sh run-once [--json|--plain]
run_once.sh status [--json|--plain]
run_once.sh history --limit <1-1000> [--json|--plain]
run_once.sh validate-config [--json|--plain]
run_once.sh self-check [--json|--plain]
run_once.sh version [--json|--plain]
```

## 4.2 统一输出协议（JSON Envelope）

```json
{
  "ok": true,
  "code": "OK",
  "message": "success",
  "data": {},
  "ts": "2026-04-17T13:00:00+08:00",
  "traceId": "cfst-20260417130000-xxxx"
}
```

## 4.3 退出码与错误码映射（最终版）
| Exit Code | code | 含义 |
|---|---|---|
| 0 | OK | 成功 |
| 10 | LOCKED | 抢锁失败 |
| 11 | CONFIG_INVALID | 配置非法 |
| 12 | EXEC_TIMEOUT | 执行超时 |
| 13 | RESULT_NOT_FOUND | 无结果文件或无有效行 |
| 14 | RESULT_PARSE_ERROR | 解析失败/IP非法 |
| 15 | STATE_WRITE_ERROR | 状态写入失败 |
| 16 | UNKNOWN_ERROR | 未归类异常 |

## 4.4 Web API 预映射（第二阶段直接复用）
1. `POST /api/v1/collector/run` -> `run-once --json`
2. `GET /api/v1/collector/status` -> `status --json`
3. `GET /api/v1/collector/history?limit=20` -> `history --limit 20 --json`
4. `POST /api/v1/collector/config/validate` -> `validate-config --json`
5. 第一阶段不再定义短路径别名（如 `/api/v1/run`），避免阶段2对接歧义。

---

## 5. 数据契约（单一真源）

## 5.1 state.json（冻结字段）

```json
{
  "schemaVersion": "1.0",
  "isRunning": false,
  "lastRunAt": "2026-04-17T12:00:00+08:00",
  "lastRunStatus": "success",
  "lastErrorCode": "",
  "bestIp": "1.2.3.4",
  "colo": "HKG",
  "latencyMs": 132,
  "speedMBps": 12.5,
  "testUrl": "https://speed.example.com/cfst-20m.bin",
  "cfstArgsHash": "sha256:xxxx",
  "rawLine": "1.2.3.4,132 ms,...",
  "source": "CloudflareSpeedTest"
}
```

## 5.2 history.jsonl（每行一条）

```json
{"runAt":"2026-04-17T12:00:00+08:00","traceId":"cfst-...","bestIp":"1.2.3.4","colo":"HKG","latencyMs":132,"speedMBps":12.5,"testUrl":"https://speed.example.com/cfst-20m.bin","rawLine":"1.2.3.4,132 ms,..."}
```

## 5.3 run.lock.meta.json（可观测锁）

```json
{
  "pid": 12345,
  "startAt": "2026-04-17T13:05:00+08:00",
  "traceId": "cfst-20260417130500-abcd",
  "command": "run-once"
}
```

## 5.4 写入一致性约束
1. `state.json` 必须原子写入：`state.json.tmp -> mv`。
2. `history.jsonl` 必须单行原子追加。
3. 失败时不得覆盖旧 `state.json`。


## 5.4.1 history.jsonl 裁剪策略（HISTORY_MAX_LINES）
1. 触发时机：每次成功追加一条历史后立即检查并裁剪。
2. 保留规则：仅保留“最新 N 行”，N=`HISTORY_MAX_LINES`。
3. 裁剪方式：写临时文件后原子替换，避免中途中断导致文件损坏。
4. 裁剪失败处理：
   - 当前次执行返回 `STATE_WRITE_ERROR`
   - 保留原文件，不进行破坏性覆盖。
5. 裁剪记录：`app.log` 写入 `history_trimmed=true`、`before_lines`、`after_lines`。
---

## 6. 核心执行流程（run-once）

## 6.1 标准流程
1. 生成 `traceId`，加载配置。
2. 执行 `validate-config`。
3. 使用 `flock` 抢占互斥锁，写 `run.lock.meta.json`。
4. 使用 `timeout` 执行 CloudflareSpeedTest。
5. 读取并解析 `result.csv`。
6. 写 `state.json`，追加 `history.jsonl`，记录 `app.log`。
7. 释放锁并返回 JSON 结果。

## 6.2 CSV 解析规范（列位优先 + 格式校验）
1. 第 1 列：IP（必需）
2. 第 2 列：端口（可选）
3. 第 3 列：延迟文本（必需，如 `132 ms`）
4. 第 6 列：下载速度文本（可选，如 `12.34 MB/s`）
5. 第二行不存在：`RESULT_NOT_FOUND`

正则：
1. IPv4：`^((25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)\.){3}(25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)$`
2. IPv6（简化）：`^[0-9a-fA-F:]+$`
3. 延迟：`^([0-9]+(\.[0-9]+)?)\s*ms$`
4. 速度：`^([0-9]+(\.[0-9]+)?)\s*(MB/s|KB/s|GB/s)$`

## 6.3 失败处理
1. 超时：`EXEC_TIMEOUT`
2. 无结果文件/无有效行：`RESULT_NOT_FOUND`
3. 解析失败/IP 类型不匹配：`RESULT_PARSE_ERROR`
4. 抢锁失败：`LOCKED`

---

## 7. CloudflareSpeedTest 参数策略（地区、速度、下载文件）

## 7.1 地区优选
1. 使用 `-cfcolo`（IATA 码，如 `HKG,SJC,LAX`）。
2. `-cfcolo` 必须配合 `-httping`。
3. 建议先小范围白名单，避免扫描面过大。

参考起步：
1. 移动/广电：`HKG`
2. 电信/联通：`SJC,LAX`

## 7.2 下载测速
1. 第一阶段必须启用下载测速，禁止 `-dd`。
2. `-dn` 建议 `10~30`。
3. `-dt` 建议 `8~15` 秒。
4. `-sl` 初期建议保守（如 `1 MB/s`）。
5. 可配合 `-tl 250` 控制延迟上限。

## 7.3 下载文件 URL
1. 使用长期可访问的静态文件地址。
2. 建议文件大小 `20MB~100MB`。
3. URL 避免鉴权、短时签名、频繁重定向。
4. URL 应通过 Cloudflare 提供访问。

## 7.4 Linux 参数模板

```bash
CloudflareST \
  -httping \
  -cfcolo HKG,SJC,LAX \
  -url https://speed.example.com/cfst-20m.bin \
  -dn 20 \
  -dt 10 \
  -tl 250 \
  -sl 1 \
  -o /opt/cfst-collector/data/result.csv
```

---

## 8. 调度与部署（Linux）

## 8.1 cron（快速上线）

```cron
*/20 * * * * /usr/bin/bash /opt/cfst-collector/scripts/run_once.sh run-once --json >> /opt/cfst-collector/logs/cron.log 2>&1
```

## 8.2 systemd timer（推荐生产）

### `/etc/systemd/system/cfst-collector.service`

```ini
[Unit]
Description=CFST Collector One-shot Runner
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
User=cfst
Group=cfst
WorkingDirectory=/opt/cfst-collector
EnvironmentFile=/opt/cfst-collector/config/config.env
ExecStart=/usr/bin/bash /opt/cfst-collector/scripts/run_once.sh run-once --json
Nice=10
IOSchedulingClass=best-effort
IOSchedulingPriority=7
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=full
ProtectHome=true
ReadWritePaths=/opt/cfst-collector/data /opt/cfst-collector/logs

[Install]
WantedBy=multi-user.target
```

### `/etc/systemd/system/cfst-collector.timer`

```ini
[Unit]
Description=Run CFST Collector every 20 minutes

[Timer]
OnBootSec=2min
OnUnitActiveSec=20min
AccuracySec=30s
RandomizedDelaySec=20s
Persistent=true
Unit=cfst-collector.service

[Install]
WantedBy=timers.target
```

启用：

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now cfst-collector.timer
```

验证：

```bash
systemctl list-timers | grep cfst-collector
journalctl -u cfst-collector.service -n 200 --no-pager
```


### systemd 与 flock 竞争关系说明
1. `timer` 只负责“触发”，不保证实例互斥；最终互斥由脚本内 `flock` 保证。
2. 即使出现重叠触发，后触发实例应快速返回 `LOCKED`，不会并发写状态。
3. `cfst-collector.service` 不设置 `Restart=`，避免与 timer 周期触发产生叠加重试。
4. 失败恢复由下一次 timer 触发承担，不在 service 层做自动重启。
## 8.3 互斥约束
同一主机 `cron` 与 `systemd timer` 二选一，禁止同时启用。

## 8.4 阶段2调度切换硬步骤（必须执行）
1. 切换到阶段2前，先停用阶段1外部调度：
   - 若使用 cron：删除对应 crontab 项。
   - 若使用 systemd timer：`systemctl disable --now cfst-collector.timer`。
2. 确认阶段1外部调度已停：
   - `systemctl is-enabled cfst-collector.timer` 应为 `disabled`（如使用过 timer）。
   - `crontab -l` 不应再包含 `run_once.sh run-once`。
3. 仅在上述检查通过后，启用 `control-api` 内建调度器。
4. 切换窗口内进行一次手动“采集 -> 同步”联测，确认不存在并发双触发。

---

## 9. 任务实施清单（唯一版本）

| 任务ID | 任务 | 产出 | 前置依赖 |
|---|---|---|---|
| T1 | 初始化目录与权限 | 部署结构 | 无 |
| T2 | 部署 CloudflareST 并验证 | 可执行二进制 | T1 |
| T3 | 编写 config.env | 配置基线 | T1 |
| T4 | 实现 validate-config/self-check | 配置与环境校验 | T3 |
| T5 | 实现 run-once 主流程 | 单次闭环 | T2,T3,T4 |
| T6 | 实现锁与锁元数据 | run.lock/meta | T5 |
| T7 | 实现解析与IP校验 | 结构化解析结果 | T5 |
| T8 | 实现 state/history 持久化 | state.json/history.jsonl | T7 |
| T9 | 实现统一 JSON 输出 | Envelope 输出 | T5 |
| T10 | 实现 status/history/version | 查询子命令 | T8,T9 |
| T11 | 配置周期调度 | cron 或 timer | T10 |
| T12 | 24h 稳定性验证 | 验证报告 | T11 |
| T13 | 文档与契约冻结 | schemaVersion/变更记录 | T12 |

依赖链：
`T1 -> T2 -> T3 -> T4 -> T5 -> T6/T7 -> T8 -> T9 -> T10 -> T11 -> T12 -> T13`

---

## 10. 可观测、SLO 与验收

## 10.1 指标清单
1. `run_total`
2. `run_success_total`
3. `run_failed_total`
4. `run_timeout_total`
5. `result_empty_total`
6. `result_parse_error_total`
7. `best_ip_change_total`
8. `run_duration_ms`
9. `best_latency_ms`
10. `best_speed_mbps`

## 10.2 SLO（24h 滚动）
| 指标 | 目标值 |
|---|---|
| 执行成功率 | >= 99% |
| 空结果率 | <= 5% |
| 执行耗时 P95 | <= 120 秒 |
| 超时率 | <= 1% |
| state.json 有效率 | 100% |

## 10.3 第一阶段验收标准
1. 手工执行一次，`state.json` 正确生成。
2. `history.jsonl` 每次运行新增合法 JSON。
3. 并发触发时仅一个实例执行。
4. 连续运行 24 小时无中断、无状态污染。
5. 命令输出 JSON 与退出码映射一致。

---

## 11. 运维、故障与变更

## 11.1 常用排查命令
1. `tail -n 200 /opt/cfst-collector/logs/app.log`
2. `cat /opt/cfst-collector/data/state.json`
3. `tail -n 20 /opt/cfst-collector/data/history.jsonl`
4. `run_once.sh self-check --json`
5. `run_once.sh validate-config --json`

## 11.2 故障分级
1. P1：`CONFIG_INVALID`、连续超时、状态不可写。
2. P2：连续空结果、地区白名单导致明显降级。
3. P3：单次解析失败、单次锁冲突。

## 11.3 快速处置
1. `CONFIG_INVALID`：回滚配置并重新校验。
2. `EXEC_TIMEOUT`：先降 `-dn/-dt`，再检查 URL 与网络。
3. `RESULT_NOT_FOUND`：检查 URL 可达性并放宽 `-cfcolo/-sl`。

## 11.4 变更与回退
1. 配置变更先 `validate-config`，观察 24h。
2. 不达标即回退到上一版本 `config.env`。
3. 记录 `changeId/changedBy/before/after/impact/rollback`。

---

## 12. 资源、安全与兼容边界

## 12.1 资源约束
1. 建议 `nice/ionice` 运行。
2. 周期建议 20~30 分钟，不建议低于 10 分钟。
3. 带宽紧张时优先降低 `-dn`、`-dt`。

## 12.2 安全约束
1. 禁止以 root 常态运行，使用低权限用户（如 `cfst`）。
2. 命令参数做白名单过滤，禁止任意 shell 拼接。
3. 路径参数必须引用与转义。
4. 不记录敏感信息到日志。

## 12.3 时间与一致性
1. 全部时间戳使用 ISO8601 且带时区。
2. 服务器必须启用 NTP。

## 12.4 第二阶段冻结契约（不可破坏）
冻结命令：`run-once/status/history/validate-config/self-check/version`

冻结 JSON Envelope 字段：`ok/code/message/data/ts/traceId`

冻结 `state.json` 核心字段：
`schemaVersion/isRunning/lastRunAt/lastRunStatus/lastErrorCode/bestIp/latencyMs/speedMBps/testUrl/cfstArgsHash`

扩展规则：
1. 仅允许新增字段。
2. 非兼容变更必须升级 `schemaVersion`。

---

## 13. 文档结论
当前文档已满足第一阶段“可直接实施”要求：
1. 目标边界明确。
2. 输入、输出、命令、状态、错误码全部契约化。
3. 调度、运维、故障、回退、SLO 完整闭环。
4. 第二阶段网页接入契约已冻结。






