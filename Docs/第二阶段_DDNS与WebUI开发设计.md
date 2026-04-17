# 第二阶段开发设计（Linux）
## WebUI 唯一控制入口：优选 IP 推送 DDNS + 可视化控制

## 1. 目标与强约束

## 1.1 第二阶段目标
1. 将第一阶段产出的优选 IP 自动同步到 DDNS（Cloudflare DNS）。
2. 所有操作通过 WebUI 发起和管理。
3. 提供完整的状态可视化、审计与回滚能力。

## 1.2 强约束（本阶段硬要求）
1. 生产环境中，不允许依赖手工 CLI 作为常规运维入口。
2. 采集、同步、调度、配置、回滚、日志查看均需可由 WebUI 完成。
3. 脚本命令仍保留，但仅作为后端内部执行层，不作为用户主入口。

## 1.3 非目标
1. 不做多租户权限模型（先单管理员账号）。
2. 不做跨节点分布式调度。
3. 不做复杂 DNS 流量策略编排（只做单记录同步与手动回滚）。

---

## 2. 总体架构（WebUI First）

```text
Browser(WebUI)
   <-> Control API Service (唯一控制平面)
           |- Job Scheduler (内建调度器)
           |- Collector Runner (内部调用 run_once)
           |- DDNS Runner (内部调用 sync_ddns)
           |- Config Manager
           |- Audit Logger
           |- State Store (state/dns_state/history)
           \- Cloudflare API Client
```

设计原则：
1. WebUI/HTTP API 是唯一控制入口。
2. 后端统一调度与执行，避免 cron+手工命令双入口漂移。
3. 全部动作产生审计日志，可追溯到用户与 traceId。

---

## 3. 操作闭环（全部由 WebUI 触发）

## 3.1 采集操作
1. 手动“立即采集”。
2. 启用/暂停自动采集任务。
3. 修改采集参数并生效（含 `-cfcolo/-url/-dn/-dt/-sl/-tl`）。
4. 查看采集状态与历史。

## 3.2 DDNS 操作
1. 手动“立即同步 DDNS”。
2. 启用/暂停自动同步任务。
3. 查看 DNS 当前记录与目标优选 IP 差异。
4. 手动回滚到指定历史 IP。

## 3.3 配置与系统操作
1. 在线校验配置。
2. 保存配置（带版本号）。
3. 查看日志、下载日志。
4. 健康检查与系统状态查看。

---

## 4. DDNS 同步链路设计

## 4.1 同步主流程
1. 读取 `state.json.bestIp`。
2. 判定 `bestIp` 的 IP 版本（IPv4/IPv6），并确定目标记录类型（`A/AAAA`）。
3. 查询 `dns_state.json.currentDnsIp`。
4. 若一致：返回 `DDNS_NOOP`。
5. 若不一致：
   - 查询 zone/record
   - PATCH 更新 record content
   - 二次查询校验
6. 成功后写 `dns_state.json` 与 `ddns_history.jsonl`。

## 4.2 幂等与一致性
1. 同步操作必须幂等（重复执行不产生副作用）。
2. 更新失败不覆盖上次成功状态。
3. 所有写操作原子化（tmp->mv）。

## 4.3 重试策略
1. 仅对 429/5xx 重试。
2. 指数退避：2s -> 4s -> 8s。
3. 最大重试 3 次。

## 4.4 回滚策略（第二阶段）
1. 提供 WebUI 手动回滚按钮。
2. 回滚本质是一次指定 IP 的 DDNS 更新。
3. 自动回滚留到第三阶段。

## 4.5 IP 版本与记录类型约束（硬规则）
1. 若 `collector.env` 的 `IP_VERSION=4`，则 DDNS 记录类型必须为 `A`。
2. 若 `collector.env` 的 `IP_VERSION=6`，则 DDNS 记录类型必须为 `AAAA`。
3. `CF_RECORD_TYPE=AUTO` 时，后端按 `bestIp` 实际类型自动选择 `A/AAAA`。
4. 若手工固定类型与 `bestIp` 不一致，返回 `CONFIG_INVALID`，并拒绝写入 DNS。

---

## 5. 控制面 API 设计

监听建议：`127.0.0.1:18080`（默认仅本机）

## 5.1 Collector API
1. `GET /api/v1/collector/status`
2. `POST /api/v1/collector/run`
3. `GET /api/v1/collector/history?limit=50`
4. `POST /api/v1/collector/schedule/enable`
5. `POST /api/v1/collector/schedule/pause`
6. `POST /api/v1/collector/config/validate`
7. `POST /api/v1/collector/config/save`

## 5.2 DDNS API
1. `GET /api/v1/ddns/status`
2. `POST /api/v1/ddns/sync`
3. `GET /api/v1/ddns/history?limit=50`
4. `POST /api/v1/ddns/rollback`
5. `POST /api/v1/ddns/schedule/enable`
6. `POST /api/v1/ddns/schedule/pause`
7. `POST /api/v1/ddns/config/validate`
8. `POST /api/v1/ddns/config/save`

## 5.3 System API
1. `GET /api/v1/system/healthz`
2. `GET /api/v1/system/metrics`
3. `GET /api/v1/system/logs?name=app&tail=200`
4. `GET /api/v1/system/audit?limit=100`

## 5.4 返回协议
统一使用第一阶段 Envelope：
`ok/code/message/data/ts/traceId`

## 5.5 DDNS 错误码契约（WebUI 展示与重试依据）
| code | 含义 | 建议 HTTP 状态 | 是否可重试 |
|---|---|---|---|
| `OK` | 同步成功 | 200 | 否 |
| `DDNS_NOOP` | IP 未变化，无需更新 | 200 | 否 |
| `LOCKED` | 任务正在执行 | 409 | 是（短间隔） |
| `CONFIG_INVALID` | 配置非法或 IP/记录类型冲突 | 400 | 否 |
| `RESULT_NOT_FOUND` | 无可用优选 IP（上游采集缺失） | 424 | 是（等待下次采集） |
| `CF_API_429` | Cloudflare 限流 | 429 | 是（指数退避） |
| `CF_API_5XX` | Cloudflare 服务端错误 | 502 | 是（指数退避） |
| `CF_API_4XX` | Cloudflare 客户端错误（鉴权/参数） | 502 | 否 |
| `DNS_RECORD_NOT_FOUND` | 目标 DNS 记录不存在 | 404 | 否 |
| `DNS_VERIFY_FAILED` | 更新后校验不一致 | 502 | 是（有限重试） |
| `STATE_WRITE_ERROR` | 状态落盘失败 | 500 | 否 |
| `UNKNOWN_ERROR` | 未归类异常 | 500 | 否 |

---

## 6. WebUI 页面设计

## 6.1 Dashboard
1. 当前优选 IP。
2. 当前 DNS 记录 IP。
3. 最后采集/同步时间。
4. 当前任务运行状态（Running/Idle/Failed）。

## 6.2 Collector 页面
1. 参数编辑与校验。
2. 立即采集。
3. 自动采集开关与周期设置。
4. 采集历史图表（延迟/速度/地区）。

## 6.3 DDNS 页面
1. DDNS 配置（Zone/Record/Type/TTL/Proxied）。
2. 立即同步按钮。
3. 自动同步开关与周期设置。
4. 同步历史列表与失败详情。
5. 回滚入口。

## 6.4 Logs & Audit 页面
1. 应用日志浏览与下载。
2. 审计日志（谁在何时做了什么操作）。

## 6.5 System 页面
1. 服务健康状态。
2. 调度器状态。
3. 最近错误摘要。

---

## 7. 配置模型（第二阶段）

建议拆分为两个配置文件：
1. `collector.env`（采集相关）
2. `ddns.env`（DNS 相关）

示例（ddns.env）：
```bash
CF_API_BASE="https://api.cloudflare.com/client/v4"
CF_API_TOKEN_FILE="/opt/cfst-collector/config/cf_token"
CF_ZONE_NAME="example.com"
CF_RECORD_NAME="edge.example.com"
CF_RECORD_TYPE="AUTO"   # AUTO | A | AAAA
CF_TTL="120"
CF_PROXIED="false"
DDNS_MAX_RETRIES=3
DDNS_RETRY_BASE_SEC=2
```

一致性要求（配置）：
1. `ddns.env` 读取 `collector.env` 的 `IP_VERSION` 作为校验输入。
2. 当 `CF_RECORD_TYPE=A|AAAA` 时，必须与 `IP_VERSION` 匹配。
3. 当 `CF_RECORD_TYPE=AUTO` 时，后端在每次同步前按 `bestIp` 自动判型。

安全要求：
1. token 单独文件存储（`600` 权限）。
2. WebUI 不回显 token 明文。
3. 日志不打印敏感字段。

---

## 8. 第二阶段数据契约

## 8.1 dns_state.json

```json
{
  "schemaVersion": "2.0",
  "zoneName": "example.com",
  "recordName": "edge.example.com",
  "recordType": "A",
  "zoneId": "xxxx",
  "recordId": "yyyy",
  "currentDnsIp": "1.2.3.4",
  "lastSyncAt": "2026-04-18T10:00:00+08:00",
  "lastSyncStatus": "success",
  "lastErrorCode": "",
  "lastGoodIp": "1.2.3.4"
}
```

## 8.2 ddns_history.jsonl

```json
{"ts":"2026-04-18T10:00:00+08:00","traceId":"ddns-...","action":"UPDATE","fromIp":"1.2.3.3","toIp":"1.2.3.4","result":"success","httpStatus":200}
```

## 8.3 audit_log.jsonl

```json
{"ts":"2026-04-18T10:05:00+08:00","user":"admin","action":"collector.run","result":"success","traceId":"api-..."}
```

一致性要求：
1. `dns_state.json` 原子写入。
2. `ddns_history.jsonl` 与 `audit_log.jsonl` 单行追加。
3. 失败不覆盖成功状态。

---

## 9. 调度设计（由 WebUI 控制）

## 9.1 调度原则
1. 调度器内置于 `control-api` 服务。
2. 调度策略配置存储在本地配置文件（或 sqlite）。
3. WebUI 修改调度后即时生效。

## 9.2 调度项
1. `collector_job`：默认每 20 分钟。
2. `ddns_sync_job`：默认每 20 分钟，建议错峰 1 分钟。

## 9.3 并发控制
1. `collector_job` 和 `ddns_sync_job` 各自独立锁。
2. 手动触发与定时触发共享同一锁。
3. 锁冲突返回 `LOCKED`，WebUI显示“任务正在运行”。

## 9.4 从阶段1到阶段2的调度切换（强制流程）
1. 上线 `control-api` 与 WebUI，但先不启用内建自动任务。
2. 停用阶段1外部调度（cron 或 systemd timer），确保只保留手动触发能力。
3. 通过 WebUI 连续执行“立即采集 -> 立即同步”至少 3 次，验证无并发冲突。
4. 验证通过后再启用 `collector_job` 与 `ddns_sync_job` 自动调度。
5. 切换完成后，禁止恢复阶段1 cron/timer，调度真源固定为 `control-api`。

---

## 10. 安全与访问控制

## 10.1 访问面
1. 默认仅监听 `127.0.0.1`。
2. 需要远程访问时，必须通过反向代理并启用认证。

## 10.2 身份认证（第二阶段最小版）
1. 单管理员账号密码登录。
2. 会话过期机制（例如 12 小时）。
3. CSRF 防护（对写接口）。

## 10.3 操作授权
1. 只允许已登录用户执行写操作。
2. 高风险操作（二次确认）：
   - 保存 DDNS 配置
   - 执行回滚
   - 暂停自动任务

---

## 11. 可观测与 SLO（第二阶段）

新增指标：
1. `ddns_sync_total`
2. `ddns_sync_success_total`
3. `ddns_sync_failed_total`
4. `ddns_noop_total`
5. `ddns_update_latency_ms`
6. `audit_event_total`

SLO：
| 指标 | 目标值 |
|---|---|
| DDNS 同步成功率 | >= 99% |
| DDNS API 失败率 | <= 1% |
| DDNS 同步耗时 P95 | <= 10 秒 |
| NOOP 占比（稳定期） | >= 80% |

---

## 12. 第二阶段任务清单

| 任务ID | 任务 | 产出 | 前置依赖 |
|---|---|---|---|
| S2-T1 | 设计 DDNS 配置与状态文件 | `ddns.env` + `dns_state.json` | 第一阶段完成 |
| S2-T2 | 实现 DDNS 同步模块 | `sync_ddns` 可运行 | S2-T1 |
| S2-T3 | 实现 DDNS 重试与校验 | 429/5xx 可恢复 | S2-T2 |
| S2-T4 | 实现 DDNS 历史与审计 | `ddns_history` + `audit_log` | S2-T2 |
| S2-T5 | 实现 control-api | API 全量可调用 | S2-T2,S2-T4 |
| S2-T6 | 实现 WebUI 页面 | Dashboard/Collector/DDNS/Logs | S2-T5 |
| S2-T7 | 实现配置读写与校验 | 配置可视化管理 | S2-T5 |
| S2-T8 | 实现 WebUI 调度控制 | 启停与周期可控 | S2-T5 |
| S2-T9 | 72h 联调验证 | 稳定性报告 | S2-T6,S2-T8 |
| S2-T10 | 契约冻结与定稿 | schema 2.x 文档 | S2-T9 |

---

## 13. 验收标准

1. WebUI 可一键触发采集与 DDNS 同步。
2. WebUI 可启停自动任务并调整执行周期。
3. IP 未变化时同步返回 `DDNS_NOOP`，不执行更新。
4. 同步失败时 WebUI 显示明确错误码与原因。
5. WebUI 可查看并下载历史与日志。
6. WebUI 可执行手动回滚并校验成功。
7. IPv4/IPv6 场景下记录类型匹配正确（`4->A`，`6->AAAA`）。
8. 连续运行 72 小时稳定。

---

## 14. 风险与缓解

1. 风险：WebUI 误操作导致频繁更新
- 缓解：写操作确认 + 最小同步间隔 + 幂等判断

2. 风险：Token 泄露
- 缓解：token 文件隔离 + 权限 600 + 日志脱敏

3. 风险：控制服务不可用
- 缓解：systemd 托管 + 健康检查 + 本地重启策略

4. 风险：采集与同步耦合故障
- 缓解：任务解耦 + 独立锁 + 失败隔离

---

## 15. 兼容策略

1. 第一阶段冻结命令与字段保持兼容。
2. 第二阶段新增字段只追加不替换。
3. 非兼容改动必须升级 `schemaVersion` 并提供迁移说明。

---

## 16. 实施顺序建议

1. 先完成 DDNS 同步模块（不做页面）。
2. 再完成 control-api（全量 API）。
3. 最后完成 WebUI 页面。
4. 按 9.4 执行阶段切换，完成“停旧调度 -> 启新调度”。
5. 72 小时稳定后再冻结第二阶段契约。

---

## 17. 文档结论

第二阶段按“WebUI 唯一入口”实施后：
1. 所有操作可视化可控。
2. 优选 IP 到 DDNS 的同步链路闭环可追溯。
3. 采集核心与控制面分层清晰，便于后续扩展。
