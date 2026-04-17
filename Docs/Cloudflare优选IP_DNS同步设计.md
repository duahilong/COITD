# 方案A总览（基于阶段1+阶段2定稿）
## Cloudflare 优选 IP -> DDNS 自动同步 -> WebUI 统一控制

## 1. 方案定位

方案A的目标是构建一个可长期运行的闭环系统：
1. 持续优选 Cloudflare 可用 IP（采集层）。
2. 将优选结果幂等同步到 DDNS（同步层）。
3. 通过 WebUI 作为唯一控制入口（控制层）。

当前采用“两阶段实施”：
1. 阶段1：先做稳定采集与数据沉淀（不做 DNS 更新，不开放 Web 服务）。
2. 阶段2：接入 DDNS 同步和 WebUI 控制面（所有操作由 WebUI 发起）。

阶段交付边界：
1. 阶段1交付“可持续采集 + 可追溯数据”，不承担对外控制职责。
2. 阶段2交付“DNS 同步 + WebUI 控制面”，形成完整运维闭环。

---

## 2. 总体架构

```text
WebUI
   <-> Control API（唯一控制平面）
       |- Collector Runner（run_once -> CloudflareSpeedTest -> state/history）
       |- DDNS Runner（sync_ddns -> Cloudflare API -> dns_state/ddns_history）
       \- Scheduler / Config / Audit
```

架构原则：
1. 采集链路与同步链路解耦。
2. 控制面统一入口（WebUI/API），命令行仅作为后端实现细节。
3. 数据契约冻结，版本可演进。
4. API 命名空间统一为 `/api/v1/collector/*`、`/api/v1/ddns/*`、`/api/v1/system/*`。

---

## 3. 阶段实施范围

## 3.1 阶段1（已定稿）：优选采集基础层

阶段1只完成两件事：
1. 定时运行 CloudflareSpeedTest，获取优选 IP。
2. 持久化状态与历史（`state.json`、`history.jsonl`）。

阶段1关键约束：
1. Linux 环境运行。
2. 必须启用下载测速（禁止 `-dd`）。
3. 互斥执行（`flock`），防并发覆盖。
4. 文件原子写入。
5. 输出统一 JSON Envelope，错误码明确。
6. 参数约束与阶段1一致：`-cfcolo` 必须配合 `-httping`，`-url` 必须为稳定可访问下载地址。

阶段1不做：
1. Cloudflare DNS 更新。
2. 对外 Web 服务。
3. 分布式调度。

## 3.2 阶段2（已定稿）：DDNS + WebUI 控制层

阶段2核心目标：
1. 将 `state.json.bestIp` 自动同步到 Cloudflare DNS。
2. WebUI 成为唯一控制入口，覆盖采集、同步、调度、配置、日志、回滚。

阶段2关键约束：
1. 生产运维不依赖手工 CLI。
2. API 全量统一返回 Envelope。
3. DDNS 更新幂等（IP 不变则 NOOP）。
4. 429/5xx 重试与二次校验。
5. 全操作审计可追溯（user + traceId）。
6. 默认仅本机监听（`127.0.0.1`），远程访问必须经反向代理与认证。
7. IP 版本与记录类型必须一致：`IP_VERSION=4->A`，`IP_VERSION=6->AAAA`（或 `AUTO` 自动判型）。

---

## 4. 阶段间契约与依赖

阶段2依赖阶段1冻结契约，不可破坏：
1. 命令契约：`run-once/status/history/validate-config/self-check/version`
2. 输出契约：`ok/code/message/data/ts/traceId`
3. 状态契约：`schemaVersion/isRunning/lastRunAt/lastRunStatus/lastErrorCode/bestIp/latencyMs/speedMBps/testUrl/cfstArgsHash`
4. 错误语义契约：`LOCKED/CONFIG_INVALID/EXEC_TIMEOUT/RESULT_NOT_FOUND/RESULT_PARSE_ERROR/STATE_WRITE_ERROR/UNKNOWN_ERROR`
5. API 契约：Collector 路径统一为 `/api/v1/collector/*`（不使用短路径别名）。

兼容策略：
1. 仅允许新增字段，不允许删除/重命名冻结字段。
2. 非兼容变更必须升级 `schemaVersion` 并提供迁移说明。

---

## 5. 关键流程

## 5.1 采集流程（阶段1主流程）
1. 校验配置。
2. 抢锁。
3. 执行 CloudflareSpeedTest。
4. 解析首条有效候选。
5. 写 state/history。
6. 输出结果并释放锁。

## 5.2 DDNS 同步流程（阶段2新增）
1. 读取 `state.json.bestIp`。
2. 判定 IP 版本并确定 `A/AAAA`（或 `AUTO` 自动判型）。
3. 查询当前 DNS record。
4. 相同则 `DDNS_NOOP`。
5. 不同则更新并二次查询校验。
6. 写 `dns_state.json` 与 `ddns_history.jsonl`。

## 5.3 WebUI 控制流程（阶段2新增）
1. 用户在 WebUI 发起操作。
2. Control API 进行参数校验与权限校验。
3. 调度内部执行器（collector/ddns）。
4. 返回 Envelope 并写审计日志。
5. 禁止把“手工 CLI”作为常规生产入口。

---

## 6. 配置与运行模型

## 6.1 采集配置（阶段1）
核心字段：
1. `CFST_BIN`、`CFST_ARGS`、`RESULT_FILE`
2. `IP_VERSION`、`COLO_ALLOWLIST`
3. `RUN_TIMEOUT_SEC`、`HISTORY_MAX_LINES`

## 6.2 同步配置（阶段2）
核心字段：
1. `CF_API_BASE`
2. `CF_API_TOKEN_FILE`
3. `CF_ZONE_NAME`、`CF_RECORD_NAME`、`CF_RECORD_TYPE`
4. `CF_TTL`、`CF_PROXIED`
5. `CF_RECORD_TYPE` 推荐 `AUTO`，由后端按 `bestIp` 判型；固定 `A/AAAA` 时必须与 `IP_VERSION` 一致。

安全要求：
1. Token 独立文件存储（600 权限）。
2. 配置修改需校验后保存。
3. 日志不打印敏感信息。

## 6.3 调度模型
1. 阶段1可用 cron 或 systemd timer。
2. 阶段2由 Control API 内建调度器统一管理（WebUI 可启停与改周期）。
3. 进入阶段2后，采集/同步任务调度以 Control API 为唯一真源，避免双入口漂移。
4. 阶段切换必须执行“停旧调度 -> 联测 -> 启新调度”，未完成前不得开启自动任务。

---

## 7. SLO 与验收

## 7.1 阶段1 SLO（24h）
1. 采集成功率 >= 99%
2. 空结果率 <= 5%
3. 执行耗时 P95 <= 120s
4. `state.json` 全时段可解析

## 7.2 阶段2 SLO（24h）
1. DDNS 同步成功率 >= 99%
2. DDNS API 失败率 <= 1%
3. 同步耗时 P95 <= 10s
4. 稳定期 NOOP 占比 >= 80%

## 7.3 阶段2验收重点
1. WebUI 可一键采集、一键同步。
2. WebUI 可启停自动任务、修改周期。
3. 回滚可用且有审计记录。
4. 连续运行 72 小时稳定。

---

## 8. 风险与治理

1. DNS 抖动
- 治理：幂等更新、最小同步间隔、仅变更时更新。

2. Token 泄露
- 治理：文件隔离、最小权限、日志脱敏。

3. WebUI 误操作
- 治理：高风险操作二次确认、配置先校验后保存、审计日志。

4. 调度重叠
- 治理：任务独立锁、冲突返回 `LOCKED`。

---

## 9. 实施里程碑

1. M1（阶段1完成）
- 采集链路稳定、契约冻结、24h 通过。

2. M2（阶段2后端完成）
- DDNS 同步链路可用，API 完整，幂等/重试/校验通过。

3. M3（阶段2前端完成）
- WebUI 覆盖全部操作，调度可控，审计可查。

4. M4（阶段2验收通过）
- 72h 稳定性通过，文档与契约冻结。

---

## 10. 文档映射

本总览文档与阶段文档关系：
1. 阶段1实施细节：
   - `D:\Code-Project\cfnet\第一阶段_CloudflareSpeedTest任务与架构设计.md`
2. 阶段2实施细节：
   - `D:\Code-Project\cfnet\第二阶段_DDNS与WebUI开发设计.md`

本文件用于“方案总览、边界统一、里程碑管理”。
