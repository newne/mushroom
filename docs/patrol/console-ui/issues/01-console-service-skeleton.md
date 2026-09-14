# 01 · console 服务骨架：静态页 + 状态轮询口

Status: ready-for-agent

## 背景

今天没有任何可给前端调用的运动接口（`Fmc4030` 仅在 `patrol` 进程内经 ctypes 调用，
`patrol.links` 禁止库内发起网络请求）。前端要落地，必须先有一个部署侧服务
（ADR-0003 的 console 单元）。

## 目标

新增 `patrol` 部署侧的 console 服务：托管静态页面，并暴露 `GET /api/status`。

- 单端口、单 origin；静态文件从包内 `static/` 读（无 node 构建链）。
- `GET /api/status` 返回 `spec.md` §5.1 的形状：`machine`（`state/connected/real_pos/
  real_speed/run_mode/inputs/outputs`）、`session`、`job`、`patrol`、`envelope`、`events`。
- `machine.real_pos` 来自 `Fmc4030.current_xy()`；`state` 由本服务维护（见 02）。
- `events` 为最近 50 条环形缓冲，同时驱动页面底栏日志。
- 控制器连接走 30s 心跳（`Get_Machine_Status`），断链后 `connected=false`。

## 验收

- [ ] 浏览器打开 console 地址即可见页面，无 CORS、无第二个 origin。
- [ ] `GET /api/status` 1s 轮询下 `real_pos` 与实际导轨一致（与 `patrol-debug status` 对照）。
- [ ] 控制器拔网线后 `connected=false`，页面状态条变红，且不影响静态页加载。
- [ ] 服务重启不改变 `FMC4030` 的既有连接语义（60s 空闲断链、≤30s 心跳）。

## 依赖

- ADR-0003（单一 origin）、ADR-0006（REST + 轮询）
- 设计：`../spec.md` §2、§5.1

## 备注

> **2026-09-14 修订（ADR-0015）**：本票的"托管静态页面"由前端容器承担，**不经过 Python**。
> 后端只有 `/api/*` 与 `/healthz`；页面在 `web/console/`（nginx 发静态页 + 同源反代 `/api/`）。
> 验收第 1 条相应改为"打开 `http://<服务器IP>:8002/` 可见页面、无 CORS、无第二个 origin"。

建议把 `patrol-debug` 的指令映射（`patrol/debug.py` 的 `status/home/jog/abs/goto/lamp/stop`）
抽成可复用方法，console 与 CLI 共用同一层，避免两套动词集漂移。
