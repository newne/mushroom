#!/usr/bin/env bash
# =============================================================================
# 采图服务体检（在 prod 宿主上执行）
#
#   ./capture-status.sh          只读体检，打印判断与建议
#   ./capture-status.sh --fix    体检后执行 docker compose up -d --force-recreate
#
# 存在的意义：这套服务的故障特征曾经是「进程活着、/healthz 绿、每次采图 500」，
# 因此不能只看进程/端口，必须查 X server 的可连接性。
# =============================================================================
set -uo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
SERVICE="xcloud-capture"
BASE_URL="http://127.0.0.1:7003"

C_CID="$(docker ps -q -f "label=com.docker.compose.service=${SERVICE}" | head -1)"
if [ -z "${C_CID}" ]; then
  C_CID="$(docker ps -aq -f "label=com.docker.compose.service=${SERVICE}" | head -1)"
fi

say() { printf '%s\n' "$*"; }
hdr() { printf '\n=== %s ===\n' "$*"; }

hdr "1. 容器状态"
if [ -z "${C_CID}" ]; then
  say "  未找到 ${SERVICE} 容器（label 查找失败）"
  docker ps -a --format '  {{.Names}}  {{.Status}}' | grep -i xcloud || true
else
  say "  CID=${C_CID:0:12}"
  docker inspect "${C_CID}" --format '  名称={{.Name}}
  状态={{.State.Status}} 退出码={{.State.ExitCode}} 重启次数={{.RestartCount}}
  启动于={{.State.StartedAt}}
  Entrypoint={{json .Config.Entrypoint}}
  健康={{if .State.Health}}{{.State.Health.Status}}（连续失败 {{.State.Health.FailingStreak}}）{{else}}<未定义 healthcheck>{{end}}' 2>&1
  say "  最近健康检查输出："
  docker inspect "${C_CID}" --format '{{if .State.Health}}{{range .State.Health.Log}}    [{{.ExitCode}}] {{.Output}}{{end}}{{else}}    (无){{end}}' 2>&1 | tail -4
fi

hdr "2. HTTP /healthz（注意：它不看显示器，绿也可能是坏的）"
if command -v curl >/dev/null 2>&1; then
  curl -s -m 3 -w '  http=%{http_code}\n' "${BASE_URL}/healthz" 2>&1
else
  say "  (宿主无 curl)"
fi

hdr "3. 容器内 X server 是否真能连（决定性判据）"
if [ -n "${C_CID}" ] && [ "$(docker inspect "${C_CID}" --format '{{.State.Status}}')" = "running" ]; then
  docker exec "${C_CID}" python3 - <<'PY' 2>&1 | sed 's/^/  /'
import os, socket
disp = os.environ.get("XVFB_DISPLAY") or ":99"
path = f"/tmp/.X11-unix/X{disp.lstrip(':')}"
exists = os.path.exists(path)
try:
    s = socket.socket(socket.AF_UNIX); s.settimeout(1); s.connect(path); s.close()
    print(f"  ✅ {path} 存在且可连接 —— X server 正常")
except Exception as exc:
    print(f"  ❌ {path} exists={exists} 不可连接: {type(exc).__name__}: {exc}")
    print("     → 采图必然 500。这就是历史故障的特征。")
PY
else
  say "  容器未运行，跳过"
fi

hdr "4. 最近的采图结果（500 与 200 计数）"
if [ -n "${C_CID}" ]; then
  logs="$(docker logs --tail 800 "${C_CID}" 2>&1)"
  n200=$(printf '%s' "${logs}" | grep -c 'capture?ip=.*" 200' || true)
  n500=$(printf '%s' "${logs}" | grep -c 'capture?ip=.*" 500' || true)
  say "  近期 GET *_capture： 200 → ${n200} 条，500 → ${n500} 条"
  say "  最近 3 条 500（若有）："
  printf '%s' "${logs}" | grep 'capture?ip=.*" 500' | tail -3 | sed 's/^/    /' || true
fi

hdr "5. 最近一次成功落盘的图片"
PIC_DIR="$(docker inspect "${C_CID}" --format '{{range .Mounts}}{{if eq .Destination "/app/saved_datas"}}{{.Source}}{{end}}{{end}}' 2>/dev/null)"
if [ -n "${PIC_DIR}" ] && [ -d "${PIC_DIR}/picture" ]; then
  find "${PIC_DIR}/picture" -maxdepth 2 -name '*.jpg' -printf '  %TY-%Tm-%Td %TH:%TM  %s B  %p\n' 2>/dev/null | sort -r | head -4
else
  say "  未能定位图片目录"
fi

hdr "6. 老系统调用窗口提醒"
say "  旧静态系统每小时 :01:30 左右调用 6 台相机（238/235/236/233/231/232）。"
say "  当前时间：$(date '+%H:%M:%S')  —— 若接近 :01:30，重建容器会与它撞车。"

hdr "结论"
if [ -n "${C_CID}" ] && [ "$(docker inspect "${C_CID}" --format '{{.State.Health.Status}}' 2>/dev/null)" = "healthy" ]; then
  say "  ✅ 容器健康，且 healthcheck 已覆盖 X server。"
else
  say "  ⚠️ 容器不健康或未定义 healthcheck。先看第 1、3 节输出。"
  say "     若第 3 节报不可连接："
  say "       - 若是刚部署覆盖层之前的老容器，执行：$0 --fix"
  say "       - 若重建后仍不可连接，看 /app/log/xvfb.log（挂载在 compose 目录 log/ 下）"
  say "       - display 被别的进程占用时，可在 override 里改 XVFB_DISPLAY"
fi

if [ "${1:-}" = "--fix" ]; then
  hdr "执行加固重建"
  say "  cd ${HERE} && docker compose up -d --force-recreate"
  ( cd "${HERE}" && docker compose up -d --force-recreate ) 2>&1 | sed 's/^/  /'
  say "  完成。用 $0 复查。"
fi
