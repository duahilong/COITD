# CFST 配置文件启动

## 为什么之前是那串命令

`./cfst -n 200 -t 4 -dn 10 -dt 10 -tl 200 -p 0 -o result.csv` 来自 `cfst -h` 参数体系：

- `-n 200`：并发线程（默认值）
- `-t 4`：单 IP 延迟测试次数（默认值）
- `-dn 10`：下载测速数量（默认值）
- `-dt 10`：单 IP 下载测速最长时间（默认值）
- `-tl 200`：延迟上限（自定义筛选条件）
- `-p 0`：不在终端打印结果
- `-o result.csv`：把结果写入文件，便于后续脚本处理

## 使用配置文件启动

1. 编辑配置文件：`scripts/cfst/cfst_config.full.json`（全量参数模板）
2. 执行：

```bash
python3 scripts/cfst/cfst_config_runner.py -c scripts/cfst/cfst_config.full.json
```

执行成功后会输出：

- `BEST_IP=...`（标准输出）
- `BEST_IP_LIST=ip1,ip2,...`（当 `best_ip_count > 1` 时）
- `best_ip.txt`（由配置项 `best_ip_file` 控制）

## 配置文件字段说明

- `cfst_path`：CFST 可执行文件路径（建议 Linux 下用 `./cfst`）
- `workdir`：执行目录
- `result_file`：CFST 结果文件路径（相对 `workdir`）
- `best_ip_file`：保存最佳 IP 的文件（可选）
- `best_ip_count`：输出并写入前 N 个优选 IP（默认 `1`）
- `options`：CFST 参数映射，键名不带 `-`，例如 `n`、`tl`、`url`
- `strict_known_options`：是否严格校验参数名（默认 `true`）

布尔开关（值为 `true` 时追加该开关参数）：

- `httping`
- `dd`
- `allip`
- `debug`
- `v`
- `h`

## 全量参数映射（对应 `cfst -h`）

`n` `t` `dn` `dt` `tp` `url` `httping` `httping-code` `cfcolo` `tl` `tll` `tlr` `sl` `p` `f` `ip` `o` `dd` `allip` `debug` `v` `h`

提示：

- `o` 设为 `null` 时，启动器会自动使用 `result_file`。
- 你也可以写 `httping_code`，启动器会自动转为 `httping-code`。
