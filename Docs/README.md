# COITD 文档索引

本文档用于把当前仍在维护的说明文件整理成一个统一入口，方便后续维护时不再来回找资料。

## 建议阅读顺序

1. [基础信息.md](./基础信息.md)
2. [功能实现总结.md](./功能实现总结.md)
3. [cfst_config_runner.md](./cfst_config_runner.md)
4. [cfst_job_controller.md](./cfst_job_controller.md)
5. [cfst_web_console.md](./cfst_web_console.md)
6. [CFST_ARGS填写说明.md](./CFST_ARGS填写说明.md)
7. [scripts/cfst_ddns/README.md](../scripts/cfst_ddns/README.md)
8. [scripts/cfst_ddns_web/README.md](../scripts/cfst_ddns_web/README.md)
9. [doss/跨机器部署教程_2026-04-19.md](../doss/%E8%B7%A8%E6%9C%BA%E5%99%A8%E9%83%A8%E7%BD%B2%E6%95%99%E7%A8%8B_2026-04-19.md)

## 按主题查阅

### 项目概览

- [基础信息.md](./基础信息.md)：环境约束、测试机、只读目录边界
- [功能实现总结.md](./功能实现总结.md)：当前实现范围与关键能力汇总

### CFST 配置与任务控制

- [cfst_config_runner.md](./cfst_config_runner.md)：如何通过 JSON/JSONC 配置启动 CFST
- [cfst_job_controller.md](./cfst_job_controller.md)：后台任务控制、状态目录、Web 对接方式
- [cfst_web_console.md](./cfst_web_console.md)：CFST Web 控制台能力与 API

### DDNS 相关

- [CFST_ARGS填写说明.md](./CFST_ARGS填写说明.md)：`CFST_ARGS` 常见写法和注意点
- [scripts/cfst_ddns/README.md](../scripts/cfst_ddns/README.md)：DDNS 脚本目录、配置模板与摘要文件说明
- [scripts/cfst_ddns_web/README.md](../scripts/cfst_ddns_web/README.md)：DDNS 专用 Web 页启动方式与页面能力

### 部署文档

- [doss/跨机器部署教程_2026-04-19.md](../doss/%E8%B7%A8%E6%9C%BA%E5%99%A8%E9%83%A8%E7%BD%B2%E6%95%99%E7%A8%8B_2026-04-19.md)：新机器部署全流程

## 维护建议

- 面向长期使用的说明文档，优先放在 `Docs/`
- 面向直接部署的操作文档，可保留在 `doss/`
- 新增模块时，优先补对应目录 README，并在本索引追加入口
