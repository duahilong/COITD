# COITD

COITD 是一个围绕 CloudflareSpeedTest 的自动化工具集，目标是把测速、任务控制、网页操作和 DDNS 更新串成一条可维护的流程。

## 这个项目做什么

- 用配置文件方式运行 CFST（不再手写长命令）
- 支持后台任务控制（启动、停止、状态、日志、历史）
- 提供简易 Web 控制台（运行任务、看日志、配置定时）
- 将测速结果自动推送到阿里云 DNS（AliDNS）
- `LINE` 参数支持多线路配置（如 `telecom,unicom,mobile,edu`）

## 目录说明

- `CloudflareSpeedTest/`：上游目录（只读，保持原始内容）
- `scripts/cfst/`：CFST 配置运行器、任务控制器等核心脚本
- `scripts/webui/`：Web 控制台（后端 + 页面）
- `scripts/cfst_ddns/`：DDNS 推送脚本（AliDNS）
- `Docs/`：基础说明与模块文档
- `doss/`：阶段总结与测试报告

## 快速开始（Linux）

1. 按配置运行 CFST

```bash
python3 scripts/cfst/cfst_config_runner.py -c scripts/cfst/cfst_config.full.json
```

2. 启动 Web 控制台

```bash
python3 scripts/webui/cfst_web_console.py --host 0.0.0.0 --port 8088 --state-dir /root/coitd/.cfst_jobs_web
```

3. 运行 DDNS 推送（AliDNS）

```bash
cd scripts/cfst_ddns
bash ./cfst_ddns.sh
```

## 配置提示

- `scripts/cfst_ddns/cfst_ddns.conf` 是本地私密配置文件，不应提交到仓库
- 公开示例配置使用 `scripts/cfst_ddns/cfst_ddns.conf.example`
- 详细参数与流程说明请看 `Docs/` 与 `doss/` 中对应文档
