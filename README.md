# COITD - 第一阶段实现

本仓库已按第一阶段设计落地基础采集器，实现：

- `CloudflareSpeedTest` 单次运行与结果解析
- `state.json` 原子写入
- `history.jsonl` 追加与裁剪
- `flock` 互斥与 `run.lock.meta.json`
- 统一 JSON Envelope 输出与错误码映射

## 目录

```text
bin/
config/
  config.env
scripts/
  run_once.sh
data/
logs/
Docs/
```

## 快速开始（Linux）

1. 安装依赖：`bash flock timeout awk sed grep jq`
2. 将 CloudflareSpeedTest 二进制放到：`bin/CloudflareST`
3. 赋权：`chmod +x bin/CloudflareST scripts/run_once.sh`
4. 校验配置：
   `scripts/run_once.sh validate-config --json`
5. 手动运行一次：
   `scripts/run_once.sh run-once --json`

## 命令

- `run_once.sh run-once [--json|--plain]`
- `run_once.sh status [--json|--plain]`
- `run_once.sh history --limit <1-1000> [--json|--plain]`
- `run_once.sh validate-config [--json|--plain]`
- `run_once.sh self-check [--json|--plain]`
- `run_once.sh version [--json|--plain]`

默认输出为 `--json`。
