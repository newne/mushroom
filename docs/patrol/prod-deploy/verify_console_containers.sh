#!/usr/bin/env bash
# 巡检台两个容器的端到端验证（在 WSL 里跑；不动真机、不动现场数据）。
#
# 验的是"上机时最容易坏、坏了又最难查"的四件事：
#   1. 前端容器出得来页面，并且把 /api 反代到了后端（单一 origin）；
#   2. 后端**能写**协调文件——data/trigger（触发请求）与 data/cmd（手动指令/会话/急停）。
#      这两处必须是 rw 挂载：整棵 data 只读时，页面看起来正常，一点"跑一轮"就 500；
#   3. 手动面在真容器里能走完"接管 → 提交 → 读回"，且急停能置位；
#   4. 页面目录里的开发文件（dev.py/README/nginx.conf）不会被当成静态资源发出去。
#
# 前置：两个镜像已在本地（没推过 registry 也能验）
#   docker build -f docker/Dockerfile.patrol       -t mushroom_patrol:dev       .
#   docker build -f docker/Dockerfile.console-web  -t mushroom_console_web:dev  .
set -uo pipefail

REPO=/mnt/d/code/mushroom
CFG=$(mktemp -d /tmp/console-verify-XXXX)
NET=cverify_net
SVC=mushroom_console
WEB=cverify_web
PORT_BE=18061
PORT_FE=18062

cleanup() {
  docker rm -f "$SVC" "$WEB" >/dev/null 2>&1
  docker network rm "$NET" >/dev/null 2>&1
  rm -rf "$CFG"
}
trap cleanup EXIT
cleanup

fail=0
check() {  # check <说明> <期望> <实际>
  if [ "$2" = "$3" ]; then echo "[ok]   $1 -> $3"; else echo "[FAIL] $1 -> 期望 $2，实际 $3"; fail=1; fi
}
check_has() {  # check_has <说明> <子串> <文本>
  case "$3" in *"$2"*) echo "[ok]   $1";; *) echo "[FAIL] $1 -> 未见「$2」：${3:0:160}"; fail=1;; esac
}

# ---------- 一次性配置（照抄上机的目录约定） ----------
mkdir -p "$CFG/configs" "$CFG/data/trigger" "$CFG/data/cmd" "$CFG/Logs"
printf 'room_id: "611"\nentry_date: "2026-09-04"\nbatch_no: "mogu-100"\n' > "$CFG/configs/room.yaml"
printf 'stations:\n  - {id: S101, box_id: B101, y: 187.1, z: -21.2, layer: 1, col: 1}\n' \
  > "$CFG/configs/stations.yaml"
echo "配置目录：$CFG"

docker network create "$NET" >/dev/null

echo "=== 起后端 console（挂载方式与 compose 一致）==="
docker run -d --name "$SVC" --network "$NET" --network-alias "$SVC" -p "$PORT_BE:8001" \
  -v "$CFG/configs:/app/configs:ro" -v "$CFG/data:/app/data:ro" -v "$CFG/Logs:/app/Logs:ro" \
  -v "$CFG/data/trigger:/app/data/trigger:rw" -v "$CFG/data/cmd:/app/data/cmd:rw" \
  mushroom_patrol:dev console >/dev/null

echo "=== 起前端（页面由宿主挂入，配置在镜像里）==="
docker run -d --name "$WEB" --network "$NET" -p "$PORT_FE:80" \
  -v "$REPO/web/console:/usr/share/nginx/html:ro" \
  mushroom_console_web:dev >/dev/null

for _ in $(seq 1 15); do
  sleep 2
  curl -sf -m 3 "http://127.0.0.1:$PORT_FE/healthz" >/dev/null 2>&1 && break
done

FE="http://127.0.0.1:$PORT_FE"
BE="http://127.0.0.1:$PORT_BE"

echo "--- 1. 页面与反代 ---"
check "页面 HTTP" 200 "$(curl -s -o /dev/null -w '%{http_code}' -m 5 "$FE/")"
check_has "页面是巡检台" "蘑菇房巡检台" "$(curl -s -m 5 "$FE/")"
check_has "后端存活（经前端 /healthz）" '"ok"' "$(curl -s -m 5 "$FE/healthz")"
check_has "状态接口（页面每秒读的那个）" '"patrol"' "$(curl -s -m 5 "$FE/api/status")"
check_has "站位表" '"stations"' "$(curl -s -m 5 "$FE/api/stations")"
for p in /dev.py /README.md /nginx.conf; do
  check "开发文件不透传 $p" 404 "$(curl -s -o /dev/null -w '%{http_code}' -m 5 "$FE$p")"
done

echo "--- 2. 协调文件必须写得进去（整棵 data 只读是最容易踩的坑）---"
check "触发请求 HTTP" 202 "$(curl -s -o /dev/null -w '%{http_code}' -m 5 -X POST "$BE/api/patrol/run?reason=verify")"
check "触发请求落盘" yes "$([ -s "$CFG/data/trigger/run.json" ] && echo yes || echo no)"
check "接管 HTTP" 201 "$(curl -s -o /dev/null -w '%{http_code}' -m 5 -X POST "$FE/api/session")"
check "会话落盘" yes "$([ -s "$CFG/data/cmd/session.json" ] && echo yes || echo no)"

echo "--- 3. 手动面：提交 → 读回 → 急停 ---"
check "提交补光灯 HTTP" 202 "$(curl -s -o /dev/null -w '%{http_code}' -m 5 -X POST \
  -H 'Content-Type: application/json' -d '{"kind":"lamp","args":{"on":false}}' "$FE/api/cmd")"
check "指令落盘" yes "$([ -s "$CFG/data/cmd/cmd.json" ] && echo yes || echo no)"
check_has "读回在飞的那条" '"inflight"' "$(curl -s -m 5 "$FE/api/cmd")"
check "急停 HTTP" 200 "$(curl -s -o /dev/null -w '%{http_code}' -m 5 -X POST "$BE/api/stop?reason=verify")"
check "急停标志落盘" yes "$([ -f "$CFG/data/cmd/ESTOP" ] && echo yes || echo no)"
check "复位急停 HTTP" 200 "$(curl -s -o /dev/null -w '%{http_code}' -m 5 -X DELETE "$BE/api/stop")"
check "急停标志已清" no "$([ -f "$CFG/data/cmd/ESTOP" ] && echo yes || echo no)"

echo "--- 4. 巡检进程的容器也在（patrol-serve 只认 --help，不连控制器）---"
docker run --rm mushroom_patrol:dev patrol-serve --help >/dev/null 2>&1
check "patrol-serve 可执行" 0 "$?"

echo
if [ "$fail" = 0 ]; then echo "=== 全部通过 ==="; else echo "=== 有失败项（上面标 FAIL 的）==="; fi
exit "$fail"
