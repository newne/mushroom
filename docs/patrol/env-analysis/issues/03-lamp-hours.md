# 03 — 补光时长（`lamp_on_min`）：光照变量的落地

**Spec:** ../spec.md §2.1

**What to build:** 从 grow_light 设备点（`TD1_Q{1..4}MDLIGHT01`）的时序派生"单位时间窗内累计点亮分钟数"，作为区间分析的第四个参数，并完成契约列改名。

**Blocked by:** 01

**Status:** ready-for-agent（点名、列名、数据都已核实）

- [x] 点名列名已由用户给定（2026-10-12）：`Running` / `Model` / `OnOff1..4` / `Choose1..4` / `OnMSet` / `OffMSet` / `OnSSet` / `OffSSet`
- [x] 确认 TDengine 里有历史数据：`TD1_Q1MDLIGHT01` 199,225 行
- [x] 确认列名（引擎侧 24 列）：`Running` / `Model` / `OnOff1..4` / `Choose1..4` / `OnMSet` / `OnSSet` / `OffMSet` / `OffSSet` / `OnAlarm1..4` / `LeftAlarm` / `RightAlarm` / `LeftAlarmOnoff` / `RightAlarmOnOff`
- [ ] `OnSSet`/`OffSSet` 的语义仍待现场解释（引擎里存在，仓库文档没登记）；先只用 `Running` 积分，不依赖它们
- [ ] 采用 `Running` 积分实际点亮分钟（`Choose1..4` 是白/蓝光源选择，`OnOff1..4` 的 1 = 自动，两者都不代表点亮）
- [ ] 定义"点亮分钟"：由设备状态时序直接累计，还是由补光窗口推导
- [ ] 契约列名 `light_lux` → `lamp_on_min`；`light_lux` 保留给将来的照度传感器，恒 NULL
- [ ] `align.PARAM_COLS` 同步改名
- [ ] 单测：合成开关序列 → 累计分钟

## Comments

- 2026-10-12 写票。`docs/technical/reference/设备监控点参考.md` §4 明确补光灯只有控制点（模式、开停分钟设定、四路开关），没有照度测量点。这一票的意义就是把"光照"换成一个真能测到的量。
