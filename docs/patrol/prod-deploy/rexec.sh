#!/usr/bin/env bash
# 受控远程执行包装：用法  bash rexec.sh "<远程命令>"
#
# 口令来源（按优先级）：
#   1) 环境变量 PROD_PW
#   2) docker/.env 里的 PROD_PW=...（那个文件**未被 git 跟踪**，根 .gitignore 里有 `.env`）
# 现场主机的 root 口令只用于 plink/pscp，不参与任何 compose 变量。
set -uo pipefail
HOSTKEY="SHA256:rOwJ+JqF8k22QfutzcqyFDGbf2rfBtsqAsZWr7KhmT4"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENVFILE="$HERE/../../../docker/.env"

if [ -z "${PROD_PW:-}" ] && [ -f "$ENVFILE" ]; then
  PROD_PW="$(grep -E '^[[:space:]]*PROD_PW[[:space:]]*=' "$ENVFILE" | tail -1 | cut -d= -f2- | tr -d '\r' | sed -e 's/^["'\'']//' -e 's/["'\'']$//')"
fi
if [ -z "${PROD_PW:-}" ]; then
  echo "没有口令：请在环境变量 PROD_PW 或 $ENVFILE 里设置（后者未被 git 跟踪）" >&2
  exit 2
fi

exec "C:/Program Files/PuTTY/plink.exe" -ssh -batch \
  -hostkey "$HOSTKEY" -pw "$PROD_PW" \
  root@10.77.77.39 "$1"
