# CFST_ARGS 填写说明（对应 cfst 二进制参数）

## 1. 参数来源与版本

- 参数来源：`cfst` 二进制自带帮助（`cfst -h`）
- 当前验证版本：`CloudflareSpeedTest v2.3.4`
- 用法关系：`cfst_ddns.conf` 里的 `CFST_ARGS` 会被直接拼接到 `./cfst` 命令中

示例：

```bash
CFST_ARGS=-n 100 -t 4 -dn 10 -dt 10 -cfcolo NRT
```

实际执行类似：

```bash
./cfst -n 100 -t 4 -dn 10 -dt 10 -cfcolo NRT -o result_ddns.txt
```

## 2. 全部可用选项（v2.3.4）

1. 基础测速
- `-n`：延迟测速线程数（默认 `200`，最大 `1000`）
- `-t`：单个 IP 延迟测速次数（默认 `4`）
- `-dn`：下载测速数量（默认 `10`）
- `-dt`：单个 IP 下载测速最大秒数（默认 `10`）
- `-tp`：测速端口（默认 `443`）
- `-url`：测速地址（用于 HTTPing/下载测速）

2. HTTPing/地区筛选
- `-httping`：启用 HTTPing 模式（默认是 TCPing）
- `-httping-code`：HTTPing 有效状态码（默认允许 `200/301/302`，此参数设置单个值）
- `-cfcolo`：按地区码过滤（英文逗号分隔，仅 HTTPing 模式有效）

3. 过滤条件
- `-tl`：平均延迟上限（ms，默认 `9999`）
- `-tll`：平均延迟下限（ms，默认 `0`）
- `-tlr`：丢包率上限（`0.00~1.00`，默认 `1.00`）
- `-sl`：下载速度下限（MB/s，默认 `0.00`）

4. 输入输出
- `-p`：显示结果数量（默认 `10`，`0` 为不显示）
- `-f`：IP 段文件路径（默认 `ip.txt`）
- `-ip`：直接指定 IP/IP 段（逗号分隔）
- `-o`：输出结果文件（`cfst_ddns.sh` 会自动追加自己的 `-o`）

5. 其他开关
- `-dd`：禁用下载测速（只按延迟排序）
- `-allip`：IPv4 段内每个 IP 都测速（默认是每个 /24 随机一个）
- `-debug`：调试日志
- `-v`：版本信息
- `-h`：帮助

## 3. 在配置文件里推荐怎么填

1. 常用稳定版（推荐起步）

```bash
CFST_ARGS=-n 100 -t 4 -dn 10 -dt 10
```

2. 日本节点优先（HTTPing + 地区码）

```bash
CFST_ARGS=-n 100 -t 4 -dn 10 -dt 10 -httping -cfcolo NRT,HND,KIX
```

3. 低丢包 + 限制延迟

```bash
CFST_ARGS=-n 100 -t 4 -dn 10 -dt 10 -tl 120 -tlr 0.2
```

4. 只看延迟，不跑下载

```bash
CFST_ARGS=-n 100 -t 4 -dd -tl 150
```

## 4. 填写注意事项

1. `CFST_ARGS` 只填参数本体，不要包含 `./cfst`。
2. `RESULT_FILE` 单独由配置项控制，脚本会自动加 `-o`，一般不需要在 `CFST_ARGS` 再写 `-o`。
3. 如果你要用 `-f` 指定文件，请保证该文件在运行目录可访问（当前目录通常是 `FOLDER`）。
4. 当前脚本按空格拆分 `CFST_ARGS`，不建议在参数值里放带空格路径。

## 5. 参考位置

- 原项目文档：`CloudflareSpeedTest/README.md`
- 二进制帮助：在测试机执行 `/root/coitd/scripts/cfst_ddns/cfst -h`
