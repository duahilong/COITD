# COITD

COITD 是一个围绕 [CloudflareSpeedTest](./CloudflareSpeedTest/) 的自动化工具集，目标是把测速、任务控制、网页操作、定时调度和 DDNS 更新串成一条可维护的 Linux 运维流程。

仓库当前建议以 `main` 作为统一主线分支；`CloudflareSpeedTest/` 保持上游只读，自定义逻辑全部放在外围脚本与文档中。

## 项目能力

- 用配置文件驱动 CFST，避免反复手写长命令
- 后台运行 CFST 任务，支持状态、停止、日志、历史查询
- 提供 CFST Web 控制台，支持网页启动任务和配置 cron
- 提供 DDNS 脚本，将测速结果推送到阿里云 DNS（AliDNS）
- 提供 DDNS 专用 Web 监控页，查看 timer/service、最新结果与历史执行记录
- `LINE` 支持多线路配置，`PUSH_IP_COUNT` 支持每条线路推送多个 IP

## 目录结构

- `CloudflareSpeedTest/`：上游子模块，只读
- `scripts/cfst/`：CFST 配置运行器、任务控制器、配置模板
- `scripts/webui/`：CFST Web 控制台
- `scripts/cfst_ddns/`：AliDNS DDNS 推送脚本与配置模板
- `scripts/cfst_ddns_web/`：DDNS 专用 Web 监控页
- `Docs/`：使用说明、模块文档、统一索引
- `doss/`：阶段总结、测试记录、部署实录归档

## 快速开始

### 1. 按配置运行 CFST

```bash
python3 scripts/cfst/cfst_config_runner.py -c scripts/cfst/cfst_config.full.json
```

### 2. 启动 CFST Web 控制台

```bash
python3 scripts/webui/cfst_web_console.py \
  --host 0.0.0.0 \
  --port 8088 \
  --state-dir /root/coitd/.cfst_jobs_web
```

### 3. 运行 DDNS 推送

```bash
cd scripts/cfst_ddns
bash ./cfst_ddns.sh
```

### 4. 启动 DDNS 专用 Web 页

```bash
python3 scripts/cfst_ddns_web/ddns_web_server.py \
  --host 0.0.0.0 \
  --port 8091 \
  --timer-name cfst-ddns.timer \
  --service-name cfst-ddns.service \
  --state-dir /root/coitd/scripts/cfst_ddns/state \
  --run-log-file /root/coitd/scripts/cfst_ddns/logs/cfst_ddns_run.log
```

## 文档入口

统一文档导航见 [Docs/README.md](./Docs/README.md)。

推荐阅读顺序：

1. [Docs/基础信息.md](./Docs/基础信息.md)
2. [Docs/功能实现总结.md](./Docs/功能实现总结.md)
3. [Docs/cfst_config_runner.md](./Docs/cfst_config_runner.md)
4. [Docs/cfst_job_controller.md](./Docs/cfst_job_controller.md)
5. [Docs/cfst_web_console.md](./Docs/cfst_web_console.md)
6. [scripts/cfst_ddns/README.md](./scripts/cfst_ddns/README.md)
7. [scripts/cfst_ddns_web/README.md](./scripts/cfst_ddns_web/README.md)
8. [doss/跨机器部署教程_2026-04-19.md](./doss/%E8%B7%A8%E6%9C%BA%E5%99%A8%E9%83%A8%E7%BD%B2%E6%95%99%E7%A8%8B_2026-04-19.md)

## 配置提示

- `scripts/cfst_ddns/cfst_ddns.conf` 是本地私密配置文件，不应提交到仓库
- 公开模板使用 `scripts/cfst_ddns/cfst_ddns.conf.example`
- `CloudflareSpeedTest/` 为上游目录，不在本仓库中直接修改
