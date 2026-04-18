param(
    [string]$CfstPath = ".\cfst.exe",
    [string]$ResultFile = ".\result_pipeline.csv",
    [string]$CfstArgs = "-p 0 -o `"$ResultFile`"",
    [int]$TopN = 1,
    [string]$NextCommand = ""
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Get-BestIPs {
    param(
        [string]$CsvPath,
        [int]$Take
    )

    if (-not (Test-Path -LiteralPath $CsvPath)) {
        throw "测速结果文件不存在: $CsvPath"
    }

    $rows = Import-Csv -LiteralPath $CsvPath
    if (-not $rows -or $rows.Count -eq 0) {
        throw "测速结果为空: $CsvPath"
    }

    $firstHeader = $rows[0].PSObject.Properties[0].Name
    $ips = @()
    foreach ($row in $rows) {
        $ip = [string]$row.$firstHeader
        if (-not [string]::IsNullOrWhiteSpace($ip)) {
            $ips += $ip.Trim()
        }
    }

    if ($ips.Count -eq 0) {
        throw "结果文件中未解析到 IP: $CsvPath"
    }

    if ($Take -le 0) {
        $Take = 1
    }
    return $ips | Select-Object -First $Take
}

Write-Host "[1/3] 开始优选 IP ..."

# 清理旧结果，避免误读历史文件
if (Test-Path -LiteralPath $ResultFile) {
    Remove-Item -LiteralPath $ResultFile -Force
}

Invoke-Expression "& `"$CfstPath`" $CfstArgs"

Write-Host "[2/3] 解析测速结果 ..."
$bestIPs = Get-BestIPs -CsvPath $ResultFile -Take $TopN
$bestIP = $bestIPs[0]
Write-Host ("优选 IP: {0}" -f $bestIP)

if ([string]::IsNullOrWhiteSpace($NextCommand)) {
    Write-Host "[3/3] 未设置下一步命令，流程结束。"
    Write-Host "提示：用 -NextCommand 传入命令，并在命令中使用 {ip} 占位符。"
    exit 0
}

Write-Host "[3/3] 执行下一步动作 ..."
$commandToRun = $NextCommand.Replace("{ip}", $bestIP)
Write-Host ("执行命令: {0}" -f $commandToRun)
Invoke-Expression $commandToRun

