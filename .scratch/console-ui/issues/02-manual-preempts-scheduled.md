# 02 · 手动/巡检互斥：让位握手与 MANUAL⇄SCHEDULED 状态机

Status: needs-info

## 背景

FMC4030 是单一 TCP 会话的控制器，M1 巡检守护常驻占用它。实时模式要驱动同一台控制器，
两者必须互斥（ADR-0004）。

## 目标

- console 维护 `SCHEDULED / YIELDING / MANUAL / FAULT` 四态；`MANUAL` 需持有会话。
- 进入实时模式：`stop_all()` → 请求守护让位 → 成功进 `MANUAL`（会话 300s，命令续期）；
  被拒（正处采图窗口）回 `409 round_in_progress` + `retry_after_s`，最长等待 30s。
- 让位发生在**站间空程**；该轮 `PatrolRound` 标记 `yielded`，退出后从下一站位续跑。
- 退出实时模式：先 `home()` 再放开；浏览器断线/会话超时自动退回 `SCHEDULED`。
- 急停 `POST /api/stop` 幂等、任何状态可调，命中后进 `FAULT`，需显式复位。

## 未决（阻塞开工）

`patrol` 库禁止库内发起网络请求、守护是独立进程，console 与守护的同机控制通道形态未定。
默认取 **unix socket**（比回环 TCP 更贴合既有基线）。需确认：

1. 守护进程能否接受新增一个 unix socket 监听；
2. 让位是否允许"拒绝"（采图窗口内），拒绝时的 `retry_after_s` 取多少；
3. `yielded` 轮次在 `rounds` 表里怎么表达（新增列，还是 `quality` 字段复用）。

## 验收

- [ ] 巡检轮次运行中进入实时模式，导轨在站间停稳后才交出控制权，本轮已采站位不重拍。
- [ ] 采图窗口内请求让位被拒，界面按 `retry_after_s` 重试并留痕（409 日志）。
- [ ] 会话 300s 无命令自动退回 `SCHEDULED`，守护可恢复。
- [ ] 急停在任何状态（含 `SCHEDULED`、`YIELDING`）都能立即中止运动。

## 依赖

- ADR-0004、ADR-0006；设计 `../spec.md` §4.1

## 备注

安全链不止软件：IN1 急停/门磁与主机看门狗（连续 N 次心跳失败 → `Stop_Run`）是独立于
console 的硬保障，本票不替代它们。
