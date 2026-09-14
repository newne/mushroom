#!/usr/bin/env bash
# 受控远程执行包装：用法  PROD_PW=<密码> bash rexec.sh "<远程命令>"
# 密码只从环境变量读，不写进任何文件。
set -uo pipefail
HOSTKEY="SHA256:rOwJ+JqF8k22QfutzcqyFDGbf2rfBtsqAsZWr7KhmT4"
exec "C:/Program Files/PuTTY/plink.exe" -ssh -batch \
  -hostkey "$HOSTKEY" -pw "${PROD_PW:?请先设置 PROD_PW}" \
  root@10.77.77.39 "$1"
