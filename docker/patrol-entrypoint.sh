#!/usr/bin/env bash
# 巡检镜像的入口：一个镜像多个角色（ADR-0012 §4）。默认 `patrol`。
#
#   patrol       常驻/单轮巡检（deploy.m1）
#   patrol-serve 触发请求的执行方（持有控制器的那一个进程）
#   console      巡检台页面 + 只读接口 + 实时接管
#   preview      相机实时预览（RTSP → MJPEG，ADR-0017）
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
  patrol-serve)
    # 触发请求的执行方：**持有控制器的那一个进程**（单实例）。
    # 巡检不是自己排时刻表，而是由算法侧每 3 小时投一次请求驱动。
    exec python3 -m deploy.patrol_serve \
      --trigger-dir "${PATROL_TRIGGER_DIR:-/app/data/trigger}" \
      --room "${PATROL_ROOM:-/app/configs/room.yaml}" \
      --stations "${PATROL_STATIONS:-/app/configs/stations.yaml}" \
      --outbox "${PATROL_OUTBOX:-/app/data/outbox.jsonl}" \
      --log "${PATROL_LOG:-/app/Logs/m1.log}" \
      --capture-host "${PATROL_CAPTURE_HOST:-172.17.0.1:7003}" \
      --ingest "${PATROL_INGEST:-http://172.17.0.1:8000/ingest}" \
      "$@"
    ;;
  console)
    # 巡检台页面 + 只读接口。单 worker：状态是进程内事件缓冲，多 worker 会各说各话。
    exec uvicorn deploy.console:create_app --factory \
      --host 0.0.0.0 --port "${CONSOLE_PORT:-8001}"
    ;;
  preview)
    # 实时预览：**一个 ffmpeg 只服务所有观看者**（广播），不是每个浏览器拉一路 RTSP。
    # 与巡检/抓拍互不冲突：相机实测支持 3 路并发 RTSP（见 ADR-0017 §实测）。
    exec python3 -m deploy.preview \
      --stations "${PATROL_STATIONS:-/app/configs/stations.yaml}" \
      --port "${PREVIEW_PORT:-8003}" \
      --scale "${PREVIEW_SCALE:-640}" \
      --fps "${PREVIEW_FPS:-5}" \
      "$@"
    ;;
  shell)
    exec bash
    ;;
  *)
    # 允许临时用别的命令进容器（例如 python3 -c ...）
    exec "${role}" "$@"
    ;;
esac
