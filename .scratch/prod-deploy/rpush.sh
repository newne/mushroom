#!/usr/bin/env bash
# 受控上传包装：用法  PROD_PW=<密码> bash rpush.sh <本地路径> <远端路径>
# 密码只从环境变量读，不写进任何文件。
set -uo pipefail
HOSTKEY="SHA256:rOwJ+JqF8k22QfutzcqyFDGbf2rfBtsqAsZWr7KhmT4"
exec "C:/Program Files/PuTTY/pscp.exe" -batch -hostkey "$HOSTKEY" \
  -pw "${PROD_PW:?请先设置 PROD_PW}" \
  "$1" "root@10.77.77.39:$2"
