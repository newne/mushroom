#!/usr/bin/env bash
# 切换前预检：备份 compose、语法检查、合并结果核对、独立 display 上验证引导与 fail-closed
# 全程**不动生产容器**（预检用 throwaway 容器，display 用 :95，不碰 :99 / 7003 / 相机）
set -uo pipefail
D=/home/sysadmin/algorithm/mushroom_docker/xcloudsdk_py_offline_20260120_175307
IMG=xcloudsdk-py:0.1.0
cd "$D"

echo "===== 0. 现场信息 ====="
echo "  当前时间: $(date '+%F %T %Z')   （老系统每小时 :01:30 调用，避开）"
echo "  compose 文件:"
ls -la docker-compose*.yml 2>&1 | sed 's/^/    /'
echo

echo "===== 1. 备份 docker-compose.yml ====="
BAK="docker-compose.yml.bak.$(date +%Y%m%d%H%M%S)"
if cp -a docker-compose.yml "$BAK"; then echo "  ✅ 已备份 -> $BAK"; else echo "  ❌ 备份失败，停止"; exit 1; fi
echo

echo "===== 2. 语法检查 ====="
sh -n capture-entrypoint.sh && echo "  ✅ capture-entrypoint.sh (sh -n) 通过" || echo "  ❌ sh -n 失败"
python3 -m py_compile capture-healthcheck.py && echo "  ✅ capture-healthcheck.py 编译通过" || echo "  ❌ py_compile 失败"
sh -n capture-status.sh && echo "  ✅ capture-status.sh (sh -n) 通过" || echo "  ❌ sh -n 失败"
chmod 644 capture-entrypoint.sh capture-healthcheck.py docker-compose.override.yml capture-status.sh 2>/dev/null
chmod 755 capture-status.sh 2>/dev/null
echo

echo "===== 3. docker compose config（合并结果核对）====="
if docker compose config >/tmp/_cfg.yml 2>/tmp/_cfgerr; then
  echo "  ✅ 合并解析成功"
  echo "  --- 关键字段 ---"
  grep -nE 'entrypoint|init:|TZ:|XVFB_DISPLAY|healthcheck|test:|interval:|retries:|start_period:|stop_grace_period|image:|ports:|restart:' /tmp/_cfg.yml | sed 's/^/    /'
  echo "  --- 挂载 ---"
  sed -n '/volumes:/,/^[a-z]/p' /tmp/_cfg.yml | grep -E 'source|target|read_only' | sed 's/^/    /'
else
  echo "  ❌ 合并解析失败："; sed 's/^/    /' /tmp/_cfgerr
fi
rm -f /tmp/_cfg.yml /tmp/_cfgerr
echo

echo "===== 4. 预检 A：Xvfb 引导（独立 display :95，不启服务）====="
docker run --rm \
  -e XVFB_DISPLAY=:95 -e CAPTURE_ENTRYPOINT_DRYRUN=1 \
  -v "$D/capture-entrypoint.sh:/app/capture-entrypoint.sh:ro" \
  -v "$D/lib/x86_64/Release:/app/lib/x86_64/Release:ro" \
  -v "$D/Device:/app/Device:ro" \
  --entrypoint /bin/sh "$IMG" /app/capture-entrypoint.sh 2>&1 | sed 's/^/  /'
echo "  退出码=$?（期望 0）"
echo

echo "===== 5. 预检 B：fail-closed（故意给坏的 Xvfb 参数，必须非 0）====="
docker run --rm \
  -e XVFB_DISPLAY=:95 -e XVFB_SERVER_ARGS="-this-flag-does-not-exist" \
  -v "$D/capture-entrypoint.sh:/app/capture-entrypoint.sh:ro" \
  -v "$D/lib/x86_64/Release:/app/lib/x86_64/Release:ro" \
  -v "$D/Device:/app/Device:ro" \
  --entrypoint /bin/sh "$IMG" /app/capture-entrypoint.sh 2>&1 | sed 's/^/  /'
echo "  退出码=$?（期望非 0）"
echo

echo "===== 6. 预检 C：健康检查脚本在当前容器内跑（应当 healthy）====="
C=xcloudsdk_py_offline_20260120_175307-xcloud-capture-1
docker exec "$C" sh -lc 'command -v Xvfb >/dev/null && echo "  Xvfb 在位"' 2>&1
docker cp capture-healthcheck.py "$C:/tmp/_hc.py" 2>&1 >/dev/null && \
  docker exec "$C" python3 /tmp/_hc.py 2>&1 | sed 's/^/  /'
echo "  退出码=$?（期望 0，说明脚本对当前活着的服务判定为健康）"
docker exec "$C" rm -f /tmp/_hc.py 2>/dev/null
echo

echo "===== 7. 预检 D：覆盖层不会被 -f 误跳过（提示）====="
echo "  生产启动请用：cd $D && docker compose up -d      ← 不要带 -f"
