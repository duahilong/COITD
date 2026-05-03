# COITD 文档索引

本文档用于把 `Docs/`、`scripts/` 内说明文件以及 `doss/` 中的阶段归档整理成一个统一入口，方便后续维护时不再来回找资料。

## 建议阅读顺序

1. [基础信息.md](./基础信息.md)
2. [功能实现总结.md](./功能实现总结.md)
3. [cfst_config_runner.md](./cfst_config_runner.md)
4. [cfst_job_controller.md](./cfst_job_controller.md)
5. [cfst_web_console.md](./cfst_web_console.md)
6. [DDNS迁移阶段总结_2026-04-19.md](./DDNS迁移阶段总结_2026-04-19.md)
7. [CFST_ARGS填写说明.md](./CFST_ARGS填写说明.md)
8. [scripts/cfst_ddns/README.md](../scripts/cfst_ddns/README.md)
9. [scripts/cfst_ddns_web/README.md](../scripts/cfst_ddns_web/README.md)
10. [doss/跨机器部署教程_2026-04-19.md](../doss/%E8%B7%A8%E6%9C%BA%E5%99%A8%E9%83%A8%E7%BD%B2%E6%95%99%E7%A8%8B_2026-04-19.md)

## 按主题查阅

### 项目概览

- [基础信息.md](./基础信息.md)：环境约束、测试机、只读目录边界
- [功能实现总结.md](./功能实现总结.md)：当前实现范围与关键能力汇总

### CFST 配置与任务控制

- [cfst_config_runner.md](./cfst_config_runner.md)：如何通过 JSON/JSONC 配置启动 CFST
- [cfst_job_controller.md](./cfst_job_controller.md)：后台任务控制、状态目录、Web 对接方式
- [cfst_pipeline.md](./cfst_pipeline.md)：Windows 下的两阶段流水线脚本说明
- [cfst_web_console.md](./cfst_web_console.md)：CFST Web 控制台能力与 API

### DDNS 相关

- [DDNS迁移阶段总结_2026-04-19.md](./DDNS迁移阶段总结_2026-04-19.md)：Cloudflare 到 AliDNS 的迁移说明
- [CFST_ARGS填写说明.md](./CFST_ARGS填写说明.md)：`CFST_ARGS` 常见写法和注意点
- [scripts/cfst_ddns/README.md](../scripts/cfst_ddns/README.md)：DDNS 脚本目录、配置模板与摘要文件说明
- [scripts/cfst_ddns_web/README.md](../scripts/cfst_ddns_web/README.md)：DDNS 专用 Web 页启动方式与页面能力

### 部署与运维归档

- [doss/跨机器部署教程_2026-04-19.md](../doss/%E8%B7%A8%E6%9C%BA%E5%99%A8%E9%83%A8%E7%BD%B2%E6%95%99%E7%A8%8B_2026-04-19.md)：新机器部署全流程
- [doss/跨机器部署教程实机验证记录_2026-04-19.md](../doss/%E8%B7%A8%E6%9C%BA%E5%99%A8%E9%83%A8%E7%BD%B2%E6%95%99%E7%A8%8B%E5%AE%9E%E6%9C%BA%E9%AA%8C%E8%AF%81%E8%AE%B0%E5%BD%95_2026-04-19.md)：部署教程的实机验证过程
- [doss/COITD项目阶段全流程总结_2026-04-19.md](../doss/COITD%E9%A1%B9%E7%9B%AE%E9%98%B6%E6%AE%B5%E5%85%A8%E6%B5%81%E7%A8%8B%E6%80%BB%E7%BB%93_2026-04-19.md)：阶段性全流程总结

### 测试与实验记录

- [doss/DDNS脚本检查与10轮测试报告_2026-04-19.md](../doss/DDNS%E8%84%9A%E6%9C%AC%E6%A3%80%E6%9F%A5%E4%B8%8E10%E8%BD%AE%E6%B5%8B%E8%AF%95%E6%8A%A5%E5%91%8A_2026-04-19.md)
- [doss/DDNS多IP推送能力测试记录_2026-04-19.md](../doss/DDNS%E5%A4%9AIP%E6%8E%A8%E9%80%81%E8%83%BD%E5%8A%9B%E6%B5%8B%E8%AF%95%E8%AE%B0%E5%BD%95_2026-04-19.md)
- [doss/DDNS脚本阶段操作总结_2026-04-19.md](../doss/DDNS%E8%84%9A%E6%9C%AC%E9%98%B6%E6%AE%B5%E6%93%8D%E4%BD%9C%E6%80%BB%E7%BB%93_2026-04-19.md)
- [doss/LINE参数多配置支持说明_2026-04-19.md](../doss/LINE%E5%8F%82%E6%95%B0%E5%A4%9A%E9%85%8D%E7%BD%AE%E6%94%AF%E6%8C%81%E8%AF%B4%E6%98%8E_2026-04-19.md)
- [doss/DDNS_10轮测试原始汇总_2026-04-19.tsv](../doss/DDNS_10%E8%BD%AE%E6%B5%8B%E8%AF%95%E5%8E%9F%E5%A7%8B%E6%B1%87%E6%80%BB_2026-04-19.tsv)：原始测试数据

## 维护建议

- 面向长期使用的说明文档，优先放在 `Docs/`
- 带时间戳的阶段总结、测试记录、部署实录，继续放在 `doss/`
- 新增模块时，优先补对应目录 README，并在本索引追加入口
