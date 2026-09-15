#!/usr/bin/env bash
# 巡检台容器的端到端验证（在 WSL 里跑；不动真机、不动现场数据，预览那一段用假上游）。
#
# 验的是"上机时最容易坏、坏了又最难查"的五件事：
#   1. 前端容器出得来页面，并且把 /api 反代到了后端（单一 origin）；
#   2. 后端**能写**协调文件——data/trigger（触发请求）与 data/cmd（手动指令/会话/急停）。
#      这两处必须是 rw 挂载：整棵 data 只读时，页面看起来正常，一点"跑一轮"就 500；
#   3. 手动面在真容器里能走完"接管 → 提交 → 读回"，且急停能置位；
#   4. 页面目录里的开发文件（dev.py/README/nginx.conf）不会被当成静态资源发出去；
#   5. 实时预览这一层：ffmpeg 在镜像里、`preview` 角色起得来、console 的
#      /api/preview/status 在**没有上游**时也回 200（页面不能被它拖垮）。
#
# 前置：镜像已在本地（没推过 registry 也能验）
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
  docker rm -f "$SVC" "$WEB" cverify_preview >/dev/null 2>&1
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

echo "--- 4. 巡检执行方：用**入口脚本的真实调用形状**起一次预检 ---"
# 两个坑都在这一步：
#   1) 2026-09-15 上机时容器反复重启，日志只有一句 `unrecognized arguments: --room …`
#      ——`--help` 能过，但入口脚本把 m1 的参数**跟在后面**，argparse 的位置参数吃不下
#      那种"可选参数与位置参数交替"的形状；
#   2) 镜像是**一个入口多角色**（`patrol-serve` 是角色名，不是 PATH 里的可执行文件），
#      所以必须把角色当第一个参数传给入口脚本，不能用 `--entrypoint patrol-serve`。

# 4a. 指向一个不存在的厂商库：预检必须**明确失败**（退出 2）并指出方向。
#     本地没有厂商 .so，所以这条是"在哪都能跑"的确定性检查。
docker run --rm mushroom_patrol:dev patrol-serve --dry-run \
  --trigger-dir /tmp/t --cmd-dir /tmp/c \
  --room /app/configs/room.yaml --stations /app/configs/stations.yaml \
  --outbox /tmp/o.jsonl --lib /tmp/no-such-libFMC4030.so --log "" >/tmp/serve-bad.txt 2>&1
check "patrol-serve 预检：缺厂商库时退出 2" 2 "$?"
check_has "预检指出方向（GLIBCXX / 路径）" "厂商库加载失败" "$(cat /tmp/serve-bad.txt)"

# 4b. 有真厂商库时（VENDOR_LIB=/host/path/libFMC4030_2009_1.so）再验一次"能加载"。
#     这一步是最有价值的：上机时页面出现的 `GLIBCXX_3.4.32 not found` 就是它拦下的那类问题。
if [ -n "${VENDOR_LIB:-}" ] && [ -f "$VENDOR_LIB" ]; then
  docker run --rm -v "$(dirname "$VENDOR_LIB"):/opt/fmc-lib:ro" mushroom_patrol:dev \
    patrol-serve --dry-run --trigger-dir /tmp/t --cmd-dir /tmp/c \
    --room /app/configs/room.yaml --stations /app/configs/stations.yaml \
    --lib "/opt/fmc-lib/$(basename "$VENDOR_LIB")" --log "" >/tmp/serve-lib.txt 2>&1
  check "patrol-serve 预检：真厂商库可加载" 0 "$?"
  check_has "预检确认已加载" "已加载" "$(cat /tmp/serve-lib.txt)"
else
  echo "[skip] 真厂商库检查（未设置 VENDOR_LIB；上机时设成宿主上的 libFMC4030_2009_1.so 再跑）"
fi

echo "--- 5. 实时预览这一层（ADR-0017） ---"
# 5a. ffmpeg 必须在镜像里：转码放在容器做，宿主上没有 ffmpeg。
check_has "镜像里有 ffmpeg" "ffmpeg version" "$(docker run --rm mushroom_patrol:dev ffmpeg -version 2>&1 | head -1)"

# 5b. preview 角色的参数形状（与入口脚本一致：角色名当第一个参数）。
#     站位表**读不到**时必须明确说"拿不到 RTSP 地址"并退出 2，而不是空转等一个永远不来的画面。
#     ⚠️ 别用"站位表里没有 camera_ip"来触发这个分支：`Station.camera_ip` 有全场默认值
#     （`patrol.stations.CAMERA_IP`），所以那种表照样能拼出地址、照样一直跑（这是设计：
#     相机晚到一会儿不该让容器反复重启）。本文件第一版就是那么写的，于是把 docker run 挂住了。
docker run --rm -v "$CFG/configs:/app/configs:ro" mushroom_patrol:dev \
  preview --stations /app/configs/no-such-stations.yaml >/tmp/preview-nocam.txt 2>&1
check "preview：读不到站位表时退出 2（不空转）" 2 "$?"
check_has "preview：说清缺什么" "拿不到 RTSP 地址" "$(cat /tmp/preview-nocam.txt)"

# 5c. 有上游（这里用一个假的 RTSP 地址 + 一个不存在的 ffmpeg）时，HTTP 面仍然要起得来。
#     /healthz 会回 503（还没有帧）——**这正是页面要看到的"暂时没有画面"**，
#     而不是连不上；console 的 /api/preview/status 必须把它翻译成 available:false。
docker run -d --name cverify_preview --network "$NET" --network-alias mushroom_preview \
  mushroom_patrol:dev preview --rtsp "rtsp://127.0.0.1:1/none" \
  --ffmpeg /bin/false --stations /app/configs/stations.yaml >/dev/null
sleep 3
check "preview：/healthz 在没有帧时回 503" 503 \
  "$(docker run --rm --network "$NET" mushroom_patrol:dev \
     curl -s -o /dev/null -w '%{http_code}' -m 5 http://mushroom_preview:8003/healthz)"
check "preview：取不到画面时 /frame.jpg 回 503" 503 \
  "$(docker run --rm --network "$NET" mushroom_patrol:dev \
     curl -s -o /dev/null -w '%{http_code}' -m 5 http://mushroom_preview:8003/frame.jpg)"
check "console：有上游但没画面时 /api/preview/frame.jpg 回 503" 503 \
  "$(curl -s -o /dev/null -w '%{http_code}' -m 8 "$FE/api/preview/frame.jpg")"
docker rm -f cverify_preview >/dev/null 2>&1

# 5d. 上游不在时（现场最常见：预览容器没起），console 的状态口必须**仍然是 200**——
#     页面每秒读它，一个坏依赖不该让整页变错误，只说"暂时看不到画面"。
#     这里的 console 没有设 PATROL_PREVIEW，用的是默认的 http://mushroom_preview:8003，
#     而上面那个假上游刚被删掉：于是走的正是"连不上"这条真实路径。
check "console：上游不在时 /api/preview/status 仍回 200" 200 \
  "$(curl -s -o /dev/null -w '%{http_code}' -m 8 "$FE/api/preview/status")"
check_has "console：状态里 available=false（页面据此显示「看不到画面」）" '"available":false' \
  "$(curl -s -m 8 "$FE/api/preview/status")"
check "console：上游不在时 /api/preview 回 503（不是空流）" 503 \
  "$(curl -s -o /dev/null -w '%{http_code}' -m 8 "$FE/api/preview")"

echo
if [ "$fail" = 0 ]; then echo "=== 全部通过 ==="; else echo "=== 有失败项（上面标 FAIL 的）==="; fi
exit "$fail"
