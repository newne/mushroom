# 远程执行/上传的 Windows 侧包装（plink / pscp 自带口令参数，从 .env 读，不写进命令行历史）。
#
#   pwsh -File docs\patrol\prod-deploy\rexec.ps1 "systemctl status patrol-m1 --no-pager"
#   pwsh -File docs\patrol\prod-deploy\rpush.ps1 .\web\console\index.html /home/.../index.html
#
# 口令来源（按优先级）：环境变量 PROD_PW → docker/.env 里的 PROD_PW=...
# 与 rexec.sh / rpush.sh 同一套约定（bash 那两个给 WSL 用，这两个给 Windows 用）。
[CmdletBinding()]
param(
  [Parameter(Mandatory = $true, Position = 0)][string]$Command,
  [string]$HostName = '10.77.77.39',
  [string]$User = 'root'
)

$ErrorActionPreference = 'Stop'
$HostKey = 'SHA256:rOwJ+JqF8k22QfutzcqyFDGbf2rfBtsqAsZWr7KhmT4'

function Get-ProdPw {
  if ($env:PROD_PW) { return $env:PROD_PW }
  $envFile = Join-Path $PSScriptRoot '..\..\..\docker\.env'
  if (Test-Path $envFile) {
    $line = (Get-Content $envFile | Where-Object { $_ -match '^\s*PROD_PW\s*=' } | Select-Object -Last 1)
    if ($line) { return ($line -replace '^\s*PROD_PW\s*=\s*', '').Trim().Trim('"').Trim("'") }
  }
  throw "没有口令：请设置环境变量 PROD_PW，或在 docker/.env 里写 PROD_PW=<口令>（该文件未被 git 跟踪）"
}

$plink = 'C:\Program Files\PuTTY\plink.exe'
if (-not (Test-Path $plink)) { throw "找不到 plink：$plink" }

& $plink -ssh -batch -hostkey $HostKey -pw (Get-ProdPw) "$User@$HostName" $Command
exit $LASTEXITCODE
