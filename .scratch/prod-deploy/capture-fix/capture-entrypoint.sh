#!/bin/sh
# =============================================================================
# 加固版采图服务入口（覆盖镜像内 /entrypoint.sh，见 docker-compose.override.yml）
#
# 【为什么需要它】
# 镜像自带的入口已经会启动 Xvfb，但在 Xvfb **起不来**时只打一行警告就继续启动
# python。结果是：进程活着、/healthz 返回 200、而每一次采图都 500。docker 的
# restart: unless-stopped 只关心主进程是否存活，因此不会重启它——故障可以一直
# 躺着，直到有人手工重启容器。运行期 Xvfb 崩溃是同样的静默降级。
#
# 【本入口堵死的三条路】
#   1) 就绪判定 = 「真的能连上 X11 unix socket」，而不是 [ -S socket ]。
#      残留的 socket 文件会让 [ -S ] 误判为就绪（Xvfb 已死、文件还在）。
#   2) Xvfb 起不来 → exit 1（fail-closed）。交给 restart 策略反复重试，把
#      "静默降级成 headless" 变成"可见的 crash loop"。
#   3) 运行期互监管：任一进程（Xvfb / python）退出就带走整个容器。
#
# 【环境变量】与镜像保持一致，可直接替换：
#   PORT HOST LIB_PATH SDK_CONFIG_PATH MINIO_CONFIG_PATH PICTURE_DIR
#   USE_XVFB XVFB_DISPLAY XVFB_SERVER_ARGS DISABLE_POOL
# =============================================================================
set -eu

PORT="${PORT:-7003}"
HOST="${HOST:-0.0.0.0}"
LIB_PATH="${LIB_PATH:-/app/lib/x86_64/Release/libXCloudSDK.so}"
SDK_CONFIG_PATH="${SDK_CONFIG_PATH:-/app/XCloudSDKTest_config.ini}"
MINIO_CONFIG_PATH="${MINIO_CONFIG_PATH:-/app/minio_config.json}"
PICTURE_DIR="${PICTURE_DIR:-/app/saved_datas/picture}"
USE_XVFB="${USE_XVFB:-1}"
XVFB_DISPLAY="${XVFB_DISPLAY:-:99}"
XVFB_SERVER_ARGS="${XVFB_SERVER_ARGS:--screen 0 1920x1080x24 -ac}"

mkdir -p "${PICTURE_DIR}" /app/log /app/saved_datas /app/lib/x86_64/Release /app/Device

if [ ! -f "${LIB_PATH}" ]; then
  echo "❌ libXCloudSDK.so not found: ${LIB_PATH}" >&2
  exit 2
fi
[ -f "${SDK_CONFIG_PATH}" ]   || echo "⚠️  SDK config not found: ${SDK_CONFIG_PATH}" >&2
[ -f "${MINIO_CONFIG_PATH}" ] || echo "⚠️  MinIO config not found: ${MINIO_CONFIG_PATH} (storage=cloud will fail)" >&2

lib_dir="$(dirname "${LIB_PATH}")"
if [ -n "${LD_LIBRARY_PATH:-}" ]; then
  export LD_LIBRARY_PATH="${lib_dir}:${LD_LIBRARY_PATH}"
else
  export LD_LIBRARY_PATH="${lib_dir}"
fi

if command -v ldd >/dev/null 2>&1; then
  missing="$(ldd "${LIB_PATH}" 2>/dev/null | awk '/not found/ {print}')"
  if [ -z "${missing}" ]; then
    echo "✅ lib deps ok"
  else
    echo "❌ Missing shared libraries for ${LIB_PATH}:" >&2
    echo "${missing}" >&2
    exit 3
  fi
fi

disable_arg=""
case "${DISABLE_POOL:-}" in
  1|true|yes|TRUE|YES) disable_arg="--disable-pool" ;;
esac

# 真连接探测：能连上才算 X server 就绪。残留 socket 会拒绝连接 → 返回非 0。
x_alive() {
  python3 - "$1" <<'PY' 2>/dev/null
import socket, sys
try:
    s = socket.socket(socket.AF_UNIX)
    s.settimeout(0.5)
    s.connect(sys.argv[1])
    s.close()
except Exception:
    sys.exit(1)
PY
}

xvfb_pid=""
case "${USE_XVFB}" in
  0|false|no|FALSE|NO)
    echo "ℹ️  USE_XVFB=${USE_XVFB} —— 按配置不启动虚拟显示"
    ;;
  *)
    if ! command -v Xvfb >/dev/null 2>&1; then
      # 明确失败：没有虚拟显示就直接 exit，不要下去变成"每张图都 500"
      echo "❌ USE_XVFB=1 但容器内找不到 Xvfb —— 拒绝以降级模式启动" >&2
      exit 1
    fi

    export DISPLAY="${XVFB_DISPLAY}"
    disp_num="${DISPLAY#:}"
    sock="/tmp/.X11-unix/X${disp_num}"
    lock="/tmp/.X${disp_num}-lock"

    if x_alive "${sock}"; then
      # 同一容器内重复执行入口（罕见）时不要起第二个 X server 抢同一个 display
      echo "ℹ️  DISPLAY=${DISPLAY} 已有存活的 X server，沿用之"
    else
      # 清掉上次运行的残留（docker restart 不重置容器 /tmp）。
      # 只在确认没有活着的 X server 时才清，避免误删正在服务的 socket。
      rm -f "${lock}" "${sock}" 2>/dev/null || true

      echo "🖥  Starting Xvfb on DISPLAY=${DISPLAY} (log=/app/log/xvfb.log) ..."
      Xvfb "${DISPLAY}" ${XVFB_SERVER_ARGS} >/app/log/xvfb.log 2>&1 &
      xvfb_pid="$!"

      i=0
      ready=0
      while [ "${i}" -lt 100 ]; do          # 最多 10 s
        if x_alive "${sock}"; then ready=1; break; fi
        kill -0 "${xvfb_pid}" 2>/dev/null || break   # Xvfb 已退出，不必再等
        i=$((i + 1))
        sleep 0.1
      done

      if [ "${ready}" != "1" ]; then
        echo "❌ Xvfb 未能在 10s 内就绪（DISPLAY=${DISPLAY}）" >&2
        echo "   拒绝以降级模式启动：headless 下每次采图都会 500 且不会被自动重启。" >&2
        echo "   容器即将退出，交由 restart 策略重试。xvfb.log 尾部：" >&2
        tail -5 /app/log/xvfb.log >&2 2>/dev/null || true
        kill "${xvfb_pid}" >/dev/null 2>&1 || true
        exit 1
      fi
      echo "✅ Xvfb 就绪：DISPLAY=${DISPLAY} pid=${xvfb_pid}"
    fi
    ;;
esac

# ---------------------------------------------------------------------------
# 自检开关：只验证「Xvfb 能否引导」，不启动服务、不碰相机。
#   引导成功 → exit 0；引导失败 → 走上面的 exit 1（fail-closed 行为也可被验证）
#   docker run --rm -e XVFB_DISPLAY=:95 -e CAPTURE_ENTRYPOINT_DRYRUN=1 \
#     -v ./capture-entrypoint.sh:/app/capture-entrypoint.sh:ro \
#     --entrypoint /bin/sh xcloudsdk-py:0.1.0 /app/capture-entrypoint.sh
# ---------------------------------------------------------------------------
if [ "${CAPTURE_ENTRYPOINT_DRYRUN:-0}" = "1" ]; then
  echo "ℹ️  自检模式（CAPTURE_ENTRYPOINT_DRYRUN=1）：Xvfb 引导成功，不启动采图服务"
  [ -n "${xvfb_pid}" ] && kill "${xvfb_pid}" 2>/dev/null || true
  exit 0
fi

# ---------------------------------------------------------------------------
# 启动服务并监管：display 不可用 → 结束整个容器 → restart 策略重建
#
# 判据刻意选「display 还能不能连」，而不是「Xvfb 进程还在不在」：
#   进程活着 ≠ 显示器可用（进程可能已经卡死），而且这与就绪判定用的是同一把尺子。
#   （注：实测 dash 会回收后台子进程，`kill -0` 对已死子进程返回非 0，
#     所以"僵尸不可辨"在这套镜像里不成立——换判据是为了覆盖面，不是因为那个。）
#
# 结构上「后台 watchdog + 前台 wait」：前台用 wait 阻塞并回收主服务，
# watchdog 只负责守条件。POSIX sh 没有 `wait -n`，这是不会自旋空转的写法。
# ---------------------------------------------------------------------------
trap 'echo "收到终止信号，正在停止…"; \
      [ -n "${app_pid:-}" ] && kill "${app_pid}" 2>/dev/null || true; \
      [ -n "${watchdog_pid:-}" ] && kill "${watchdog_pid}" 2>/dev/null || true; \
      [ -n "${xvfb_pid}" ] && kill "${xvfb_pid}" 2>/dev/null || true; \
      exit 0' TERM INT

python3 -m xcloudsdk_py \
  --host "${HOST}" \
  --http "${PORT}" \
  --lib "${LIB_PATH}" \
  --sdk-config "${SDK_CONFIG_PATH}" \
  --minio-config "${MINIO_CONFIG_PATH}" \
  --picture-dir "${PICTURE_DIR}" \
  ${disable_arg} &
app_pid="$!"

watchdog_pid=""
if [ -n "${xvfb_pid}" ]; then
  (
    fails=0
    while :; do
      sleep "${WATCH_INTERVAL_S:-5}"
      if x_alive "${sock}"; then
        fails=0
      else
        fails=$((fails + 1))
        echo "⚠️  display ${DISPLAY} 连续 ${fails} 次不可连"
        if [ "${fails}" -ge 2 ]; then
          echo "❌ Xvfb 已不可用（display ${DISPLAY}）—— 结束采图服务，容器将重启" >&2
          tail -5 /app/log/xvfb.log >&2 2>/dev/null || true
          kill "${app_pid}" 2>/dev/null || true
          exit 1
        fi
      fi
    done
  ) &
  watchdog_pid="$!"
fi

wait "${app_pid}"
rc="$?"
[ -n "${watchdog_pid}" ] && kill "${watchdog_pid}" 2>/dev/null || true
echo "ℹ️  采图服务退出 rc=${rc}"
exit "${rc}"
