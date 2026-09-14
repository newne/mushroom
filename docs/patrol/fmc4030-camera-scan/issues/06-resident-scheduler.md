# 06 — 7×24 常驻调度：时刻表、看门狗重连、同步 prod

**Spec:** ../spec.md §2.3、§3、§4.2

**What to build:** 巡检调度器以守护进程常驻：按时刻表自动巡检；心跳看门狗监测连接（连续失败即 Stop_Run 并告警）；断线自动重连 + 重新回零后继续；测量结果与元数据同步到 prod 服务器（10.77.77.39，凭据一律走环境变量）。端到端行为：过夜自动运行多轮巡检，人为拔网线能自愈，数据不丢。

**Blocked by:** 04 — 全库站位示教 + 一轮完整巡检（M1）；05 — 测量管线

**Status:** ready-for-agent

- [ ] 按配置时刻表自动触发巡检，无需人工干预
- [ ] 心跳看门狗工作：模拟拔网线/重启控制器后自动恢复巡检
- [ ] 测量结果与元数据在 prod 可查询，主机端断网补传不丢数据
- [ ] 连续 7×24 运行 ≥72h 无需人工干预（验收期）
- [ ] 故障告警（状态灯 OUT2 + 日志/通知）可用

## Comments

- 2026-08-30 代码完成（commit `5028a65`）：`patrol/store.py`（JSONL outbox）、`patrol/sync.py`（批量推送 + 失败保留补传）、`patrol/daemon.py`（PatrolDaemon：连接重试/退避、回零、run_round、round+measurement 记录、flush）、`analysis/api.py`（POST /ingest、GET /measurements、/rounds、/healthz，单 worker + WAL），17 项单测（重连、补传、幂等）。
- **心跳设计说明**：守护每轮"连接→工作→关闭"，连接期间指令/状态查询持续交互（>1min 无交互断线不触发）；无空闲长连接，无需独立心跳线程。
- **待现场**：看门狗实测（拔网线自愈）、72h 连续运行、OUT2 告警链、prod 服务器部署（uvicorn + 传输函数注入）。
