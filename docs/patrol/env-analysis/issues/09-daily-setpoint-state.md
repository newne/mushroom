# 09 — 调整动作登记：变更事件流与每日设定末态

**Spec:** ../spec.md §2.4、§2.5

**What to build:** 把 `device_setpoint_changes` 里的变更整理成两个可用形状：**事件流**（谁、何时、哪点位、从 → 到）与**每日设定末态**（当时运行在什么设定上）。端到端行为：给定批次，能拿到时间轴标记用的变更流，以及（生长天数 × 设备 × 点位 → 当日末态）的表，且"未变更"与"未知"分得开。

**Blocked by:** 无

**Status:** in-progress —— **每日设定末态（`GET /setpoints/daily`）已落地**（2026-10-14，见 §2）；
阶段汇总、`device_setpoint_changes` 停更核对仍待做。

- [x] ~~参数集读 `src/configs/monitoring_points_config.json`~~ —— **改为从数据取**（2026-10-14）：
      A′ 实测失败（镜像里没有 `src/**`/`utils`，票 13 §3.7）⇒ analysis 容器**拿不到那个文件**。
      v1 的键集合 = `operations` 里实际出现过的 `(device_code, point_code)`（**不另立第二份参数清单**）。
      等 A′ 或算法侧只读出口落实再对齐配置。
- [x] ~~事件流：`GET /setpoints/changes`~~ —— **砍掉（wontfix，Chief 2026-10-13）**：变更历史的权威源是
      `operations`（逐条，`GET /operations`），`device_setpoint_changes` 已降级为交叉验证；
      再做一个读旧源的接口就是两套来源打架。时间轴标记与"通常怎么调"的频次/幅度直接读 `/operations`
- [x] 末态规则：当日最后一次变更的 `value`；当天无变更沿前一日；批次首日前标"未知" · 见 §2
- [ ] 阶段汇总：每个 D 分档内的变更频次与幅度（复用 `setpoint_analytics` 的 `avg_magnitude`），供设定区间表
- [ ] 核对 `device_setpoint_changes` 是否仍在更新（实际参数停在 2026-04-09，设定点监控是否同样停更）
- [x] 单测：合成变更序列覆盖"一天多改 / 跨日未改 / 批次首日"三种情况 —— `analysis/tests/test_setpoints.py`（10 条）

## 2. 每日设定末态（2026-10-14 落地）

**入口**：`GET /setpoints/daily?zone=&start=&end=[&entry_date=]`；实现 `analysis/src/analysis/setpoints.py`。
**事件源只有 `operations`**（ADR-0022）；本模块不读 `device_setpoint_changes`（有用例钉住产物里不出现它的列名）。

三态**分得开**（票面验收原话，也是这一票最容易做错的地方）：

| 状态 | 含义 | `value` |
| --- | --- | --- |
| `changed` | 当天有变更 ⇒ 取**当天最后一条**（不是第一条、不是平均）；带 `n_changes_today` | 值 |
| `carried` | 当天没改 ⇒ **沿用**更早那条，`since`/`since_day` 指出是哪天的 | 值 |
| `unknown` | **从没见过**该点位的变更 | **NULL**（不写 0、不填默认） |

把 `carried` 与 `unknown` 混成一个"没有值"，就是把"一直在用某个设定"报成"不知道" —— 方向刚好反了。
为此窗口**起点之前**要取一份基线（`db.fetch_latest_operations_before`，按 `(device, point)` 取
`before` 之前最后一条）；否则窗口第一天全是 `unknown`。行形状：
`{day, growth_day, growth_day_prod, first_day, device_code, device_name, point_code, point_name,
state, value, since, since_day, operator, n_changes_today}`。

**生长天数与首日（票 19 口径）**：`entry_date` 给了就并列 `growth_day`（入库当天 = 0，我方）
与 `growth_day_prod`（入库当天 = `01`，生产口径）+ `first_day`；**三值并列、绝不平均、绝不合并**；
**首日行一条不删**（剔除只发生在统计/学习侧）。`summary` 给 `n_changed/n_carried/n_unknown/...`
与 `first_day_rows`，让"沿用/未知"的占比在页面上看得见。

**窗口护栏**：`MAX_DAYS = 180`，超了报 400（不静默截断 —— 截断的表看起来是对的）。
`start > end`、非法日期同样报 400。

## Comments

- 2026-10-12 晚：**登记部分已换权威来源并落地**——用户给出环控平台命令审计接口
  （`/service/monitor/command/audit/log/page/list`，登录 `/service/security/auth/login`，
  口令 MD5，业务接口带 `X-AUTH-TOKEN`）。`analysis.audit` + `analysis.audit_ingest` +
  `operations` 表 + `GET /operations` 已上机；定时器 `mushroom-audit-ingest.timer`
  每 10 分钟拉一次，凭据走 `/etc/mushroom/ems.env`（不进仓库）。首次回填 16 条真实操作
  （607 库 7 条、608 库 5 条、611/612 各 2 条）。本票只剩"每日设定末态"那一半。
- 2026-10-12 写票。这一票是"调整方式"的数据底座：区间表里回答"通常怎么调"的那半边全依赖它。它同时是界面时间轴标记的来源。
