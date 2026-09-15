#!/usr/bin/env bash
# 受控上传包装：用法  bash rpush.sh <本地路径> <远端路径>
#
# 口令来源（按优先级）：环境变量 PROD_PW → docker/.env 里的 PROD_PW=...
# （见 rexec.sh 的说明；那个文件未被 git 跟踪）
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

exec "C:/Program Files/PuTTY/pscp.exe" -batch -hostkey "$HOSTKEY" \
  -pw "$PROD_PW" \
  "$1" "root@10.77.77.39:$2"
