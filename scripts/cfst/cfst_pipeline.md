# cfst_pipeline.ps1 用法

这个脚本把流程拆成两步：

1. 调用 CFST 优选 IP  
2. 将优选出的第一名 IP 传给你的下一步命令

## 1) 只做优选，不执行下一步

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\cfst\cfst_pipeline.ps1 `
  -CfstPath .\cfst.exe `
  -ResultFile .\result_pipeline.csv `
  -CfstArgs '-n 200 -t 4 -dn 10 -url https://cf.xiu2.xyz/url -p 0 -o ".\result_pipeline.csv"'
```

## 2) 优选后执行下一步

在 `-NextCommand` 中使用 `{ip}` 占位符，脚本会自动替换为优选 IP。

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\cfst\cfst_pipeline.ps1 `
  -CfstPath .\cfst.exe `
  -ResultFile .\result_pipeline.csv `
  -CfstArgs '-n 200 -t 4 -dn 10 -p 0 -o ".\result_pipeline.csv"' `
  -NextCommand 'echo 最佳IP是 {ip}'
```

## 3) 常见下一步示例

- 更新 hosts：`-NextCommand 'powershell -File .\scripts\cfst\update_hosts.ps1 -BestIP {ip}'`
- 调用接口：`-NextCommand 'curl "https://example.com/update?ip={ip}"'`
- 写入本地配置：`-NextCommand 'powershell -Command "(Get-Content .\app.env) -replace ''BEST_IP=.*'',''BEST_IP={ip}'' | Set-Content .\app.env"'`
