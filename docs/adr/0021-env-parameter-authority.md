# ADR-0021：环境参数的权威口径，以及"光照"用什么量

日期：2026-10-12
状态：建议（随 `docs/patrol/env-analysis/spec.md` 一起评审）
相关：`fmc4030-camera-scan/spec.md` §6、ADR-0020、ADR-0022

## 背景

环境数据今天散在四处：

- Postgres 的日聚合 `mushroom_env_daily_stats`（每天 01:03:20 由算法侧任务写，只有 median/min/max/q25/q75/count，没有光照列）
- 每图快照 JSON `mushroom_embedding.env_sensor_status`
- SQLite 的原始序列表 `analysis.environment`（有表、无写入、无读接口）
- SCADA / TDengine 的原始时序（可直读，但今天没有代码在用）

同时，`fmc4030 spec` §6 定的契约 `environment(ts, zone, temp_c, rh_pct, co2_ppm, light_lux)` 里，`light_lux` **没有测量源**：设备侧补光灯只有模式、开停分钟设定与四路开关，TDengine 侧只有 `Tatmosp/Hatmosp/Co2`。用户要求"光照也要考虑"，不改这个变量就等于永远算不出光照区间。

## 决定

1. **原始序列的唯一写入口**是 `analysis.environment`，数据来自 TDengine。界面与区间分析只读这一处。
2. **光照变量 = 补光时长 `lamp_on_min`**（单位时间窗内累计点亮分钟），由 grow_light 设备点时序派生。`light_lux` 列保留给将来的照度传感器，无源期间一律 NULL，界面标注"无数据源"，不许显示成 0。
3. **`zone` 存库房号**（611/612/607/608），不是自由文本。适配器里 `"default"` 的兜底只允许出现在 CSV 演练，不允许进生产库。
4. **产量只是验证列，不是优化目标**。区间表按阶段 × 参数 × 分箱切，而产量是每批次一个数，现有批次数下直接用它做优化必然过拟合。这与 `decision_prompt.jinja` 里"产量/品质数据仅用于回溯分析，不参与当前调控决策"的既有约定一致。
5. **缺失不等于 0**。环境缺档不插值，产量缺行不当 0，区间表样本不足的箱不输出。

## 后果

- `environment` 契约里的 `light_lux` 变成预留列；`ranges.param` 的取值集合改为 `temp_c / rh_pct / co2_ppm / lamp_on_min`，`align.PARAM_COLS` 同步改名。
- v1 的取数路径有两条，对页面契约没有影响，但覆盖的参数不同：日聚合路线今天就有数、表里没有光照列；TDengine 直读路线四维齐全、依赖 01 号票。**若"光照也要考虑"是硬要求，v1 只能走后者。**
- 想要真正的照度区间，需要现场新增光照传感器。在那之前，本仓库只能给"累计补光时长区间"。
- 日聚合表继续由算法侧每日任务产出，作为对照与回溯，不作为界面主数据源。
