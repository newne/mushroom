#!/usr/bin/env bash
# 收尾：一次性验证脚本归档到子目录；删除本次验证产生的具名测试图
set -uo pipefail
D=/home/sysadmin/algorithm/mushroom_docker/xcloudsdk_py_offline_20260120_175307
cd "$D"

echo "===== 1. 归档一次性验证脚本 ====="
mkdir -p capture-fix
for f in preflight.sh switch.sh verify.sh regress.sh; do
  [ -f "$f" ] && { mv -f "$f" capture-fix/; echo "  归档 $f"; }
done
echo

echo "===== 2. 删除本次验证产生的测试图（仅这两个具名文件）====="
for f in mushfix_238.jpg mushfix_regress.jpg; do
  p="saved_datas/picture/$f"
  if [ -f "$p" ]; then ls -la "$p" | sed 's/^/  待删: /'; rm -f "$p" && echo "  已删 $p"; else echo "  未找到 $p"; fi
done
echo "  剩余 mushfix* 文件：$(ls saved_datas/picture/mushfix* 2>/dev/null | wc -l) 个（期望 0）"
echo

echo "===== 3. 目录现状 ====="
echo "--- compose 目录（生产运行所需）---"
ls -la "$D" | grep -vE '^total' | awk '{print "  "$NF"  ("$5" B)"}'
echo "--- capture-fix/（验证脚本留档）---"
ls -la "$D/capture-fix" | grep -vE '^total' | awk '{print "  "$NF}'
echo

echo "===== 4. 服务终态 ====="
docker compose ps 2>&1 | sed 's/^/  /'
CID="$(docker compose ps -q)"
docker inspect "$CID" --format '  健康={{.State.Health.Status}}  RestartCount={{.RestartCount}}
  入口={{json .Config.Entrypoint}}  init={{.HostConfig.Init}}' 2>&1
curl -s -m 3 -w '  /healthz http=%{http_code}\n' http://127.0.0.1:7003/healthz 2>&1
