#!/usr/bin/env bash
# prod 第三轮：FMC4030 压缩包内容、既有采集服务接口、配置与凭据落点。
set -u
hr() { printf '\n===== %s =====\n' "$1"; }
mask() { sed -E 's/((secret|Secret|SECRET|password|Password|PASSWORD|access_key|AccessKey|accessKey|secret_key|SecretKey|private|PRIVATE)[A-Za-z_]*"?[[:space:]]*[:=][[:space:]]*")([^"]{0,4})[^"]*(")/\1\3****\4/g'; }

hr "1. fmc4030 目录"
ls -la /home/sysadmin/algorithm/fmc4030/ 2>&1
echo "--- 解压工具 ---"
for c in unrar rar 7z 7za p7zip bsdtar unar; do command -v $c >/dev/null 2>&1 && echo "  有: $c"; done
echo "--- 尝试列出 rar 内容（可能失败）---"
(7z l /home/sysadmin/algorithm/fmc4030/FMC4030.rar 2>/dev/null || \
 unrar l /home/sysadmin/algorithm/fmc4030/FMC4030.rar 2>/dev/null || \
 bsdtar -tf /home/sysadmin/algorithm/fmc4030/FMC4030.rar 2>/dev/null || echo "  (无可用解压工具)") | head -40

hr "2. 采集服务配置 XCloudSDKTest_config.ini"
cat /home/sysadmin/algorithm/mushroom_docker/xcloudsdk_py_offline_20260120_175307/XCloudSDKTest_config.ini 2>&1

hr "3. minio_config.json（敏感值已打码）"
cat /home/sysadmin/algorithm/mushroom_docker/xcloudsdk_py_offline_20260120_175307/minio_config.json 2>/dev/null | mask

hr "4. 谁引用了控制器 239 / 相机 238（分开统计）"
echo "--- 提到 192.168.1.239 的文件 ---"
grep -rIl '192\.168\.1\.239' /home /opt /srv /etc 2>/dev/null | head -10
echo "--- 提到 192.168.1.238 的文件 ---"
grep -rIl '192\.168\.1\.238' /home /opt /srv /etc 2>/dev/null | head -10

hr "5. 采集服务 HTTP 接口（本机 7003）"
for p in / /docs /openapi.json /status /api/status /health /capture; do
  code=$(timeout 4 curl -s -o /tmp/_r -w '%{http_code}' "http://127.0.0.1:7003$p" 2>/dev/null)
  echo "  GET $p -> $code  $(head -c 160 /tmp/_r 2>/dev/null | tr -d '\n\r' | tr -s ' ')"
done
rm -f /tmp/_r

hr "6. 采集容器最近日志"
docker logs --tail 25 xcloudsdk_py_offline_20260120_175307-xcloud-capture-1 2>&1 | tail -25

hr "7. 相机凭证文件（只看结构与大小，不打印内容）"
for f in /home/sysadmin/algorithm/image_capture/KEY_VALUES_6.txt \
         /home/sysadmin/algorithm/image_capture/Device/local_eketkey.txt \
         /home/sysadmin/algorithm/image_capture/device_config.ini; do
  [ -e "$f" ] && printf '  %s  %s bytes  %s\n' "$(stat -c%s "$f")" "$(stat -c%y "$f" | cut -d. -f1)" "$f"
done
echo "--- device_config.ini 内容 ---"
cat /home/sysadmin/algorithm/image_capture/device_config.ini 2>&1

hr "DONE-3"
