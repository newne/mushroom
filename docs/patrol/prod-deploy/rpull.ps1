# 远端下载（pscp）包装：pwsh -File docs\patrol\prod-deploy\rpull.ps1 <远端路径> <本地路径>
# 与 rpush.ps1 对称；口令来源相同（环境变量 PROD_PW → docker/.env），不打印。
[CmdletBinding()]
param(
  [Parameter(Mandatory = $true, Position = 0)][string]$Remote,
  [Parameter(Mandatory = $true, Position = 1)][string]$Local,
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

$pscp = 'C:\Program Files\PuTTY\pscp.exe'
if (-not (Test-Path $pscp)) { throw "找不到 pscp：$pscp" }

& $pscp -batch -hostkey $HostKey -pw (Get-ProdPw) "${User}@${HostName}:$Remote" $Local
exit $LASTEXITCODE
