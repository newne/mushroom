#!/usr/bin/env bash
# 巡检镜像的入口：一个镜像多个角色（ADR-0012 §4）。默认 `patrol`。
#
#   patrol       常驻/单轮巡检（deploy.m1）
#   fetch-room   从生产库读入库日期写 room.yaml
#   flush-outbox 补传累积的 outbox
#   api          prod 接收 API（analysis.api，单 worker 硬约束）
#   shell        进容器排障
set -euo pipefail

role="${1:-patrol}"
if [ $# -gt 0 ]; then shift; fi

case "${role}" in
  patrol)
    # 默认参数从环境变量取，现场改 compose 的 environment 即可，不必改命令
    exec python3 -m deploy.m1 \
      --room "${PATROL_ROOM:-/app/configs/room.yaml}" \
      --stations "${PATROL_STATIONS:-/app/configs/stations.yaml}" \
      --outbox "${PATROL_OUTBOX:-/app/data/outbox.jsonl}" \
      --log "${PATROL_LOG:-/app/Logs/m1.log}" \
      --capture-host "${PATROL_CAPTURE_HOST:-172.17.0.1:7003}" \
      --ingest "${PATROL_INGEST:-http://172.17.0.1:8000/ingest}" \
      "$@"
    ;;
  fetch-room)
    exec python3 -m deploy.fetch_room --out "${PATROL_ROOM:-/app/configs/room.yaml}" "$@"
    ;;
  flush-outbox)
    exec python3 -m deploy.flush_outbox \
      --ingest "${PATROL_INGEST:-http://172.17.0.1:8000/ingest}" "$@"
    ;;
  api)
    exec uvicorn analysis.api:create_app --factory \
      --host 0.0.0.0 --port "${ANALYSIS_PORT:-8000}"
    ;;
  console)
    # 巡检台页面 + 只读接口。单 worker：状态是进程内事件缓冲，多 worker 会各说各话。
    exec uvicorn deploy.console:create_app --factory \
      --host 0.0.0.0 --port "${CONSOLE_PORT:-8001}"
    ;;
  shell)
    exec bash
    ;;
  *)
    # 允许临时用别的命令进容器（例如 python3 -c ...）
    exec "${role}" "$@"
    ;;
esac
