#!/usr/bin/env bash
# 受控下载包装（rpush.sh 的反向）：用法  bash rpull.sh <远端路径> <本地路径>
#
# 口令来源与 rpush.sh 一致：环境变量 PROD_PW → docker/.env 里的 PROD_PW=...
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
  "root@10.77.77.39:$1" "$2"
