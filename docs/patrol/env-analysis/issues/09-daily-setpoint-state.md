# 09 — 调整动作登记：变更事件流与每日设定末态

**Spec:** ../spec.md §2.4、§2.5

**What to build:** 把 `device_setpoint_changes` 里的变更整理成两个可用形状：**事件流**（谁、何时、哪点位、从 → 到）与**每日设定末态**（当时运行在什么设定上）。端到端行为：给定批次，能拿到时间轴标记用的变更流，以及（生长天数 × 设备 × 点位 → 当日末态）的表，且"未变更"与"未知"分得开。

**Blocked by:** 无

**Status:** ready-for-agent（登记部分已由审计日志落地，见 Comments）

- [ ] 参数集读 `src/configs/monitoring_points_config.json`，不另立一份
- [x] ~~事件流：`GET /setpoints/changes`~~ —— **砍掉（wontfix，Chief 2026-10-13）**：变更历史的权威源是
      `operations`（逐条，`GET /operations`），`device_setpoint_changes` 已降级为交叉验证；
      再做一个读旧源的接口就是两套来源打架。时间轴标记与"通常怎么调"的频次/幅度直接读 `/operations`
- [ ] 末态规则：当日最后一次变更的 `current_value`；当天无变更沿前一日；批次首日前标"未知"
- [ ] 阶段汇总：每个 D 分档内的变更频次与幅度（复用 `setpoint_analytics` 的 `avg_magnitude`），供设定区间表
- [ ] 核对 `device_setpoint_changes` 是否仍在更新（实际参数停在 2026-04-09，设定点监控是否同样停更）
- [ ] 单测：合成变更序列覆盖"一天多改 / 跨日未改 / 批次首日"三种情况

## Comments

- 2026-10-12 晚：**登记部分已换权威来源并落地**——用户给出环控平台命令审计接口
  （`/service/monitor/command/audit/log/page/list`，登录 `/service/security/auth/login`，
  口令 MD5，业务接口带 `X-AUTH-TOKEN`）。`analysis.audit` + `analysis.audit_ingest` +
  `operations` 表 + `GET /operations` 已上机；定时器 `mushroom-audit-ingest.timer`
  每 10 分钟拉一次，凭据走 `/etc/mushroom/ems.env`（不进仓库）。首次回填 16 条真实操作
  （607 库 7 条、608 库 5 条、611/612 各 2 条）。本票只剩"每日设定末态"那一半。
- 2026-10-12 写票。这一票是"调整方式"的数据底座：区间表里回答"通常怎么调"的那半边全依赖它。它同时是界面时间轴标记的来源。
