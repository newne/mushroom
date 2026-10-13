# 16 — 长势判定口径：平视单档下的可用量与"未定标"阈值

**Spec:** ../spec.md §2.7、§5；票 11 §1/§9；`CONTEXT.md` 的「长势」词条

**归属：** 环境官（`analysis/src/analysis/growth.py`、`analysis/tests/test_growth.py`）

**Blocked by:** 无（阈值定标另见 §4，不阻塞代码）
**Status:** resolved（代码 + 测试已落；阈值待现场定标）

**What to build:** 平视单档下 `mean_len_mm` 恒 NULL（票 11 §9），而 `growth.GrowthPoint.usable`
原来要求 `mean_len_mm is not None`（`growth.py:85`）⇒ **每一行都只能给 `not_comparable`**，
数据到位了也判不出长势。改成"两点**同时有值**的最高优先长势量"来比，并如实带回量纲。

（缺口由测量师在核对票 11 时发现；他未改代码，只报。）

## 1. 量纲优先级与"不许相减"

```
mean_len_mm (mm)  >  mean_projected_d_mm (mm)  >  mean_area_mm2 (mm2)
```

- 逐个往下找**两点同时有值**的第一个量；找到就是本次比较的 `metric`/`unit`。
- 一个点只有杆长、另一个点只有面积 ⇒ **`not_comparable`**，note 写出两边各有什么。
  **绝不拿两个不同的量相减**（那正是"单侧/异量凑数"）。
- 平视实际会落到 `mean_projected_d_mm`（有值就优先）或 `mean_area_mm2`。

## 2. 只吃 `verified`

`compare(require_verified=True)` 是默认：质量标记里必须含 `verified`（票 11 §3）——
整簇不可分时那些面积/投影径是**估出来的**。老行没有质量标记 ⇒ `not_comparable`，
note 说明"质量标记不可用（无标记）"，**不静默当可用**。

## 3. 响应字段（`/growth`、`/growth/room`）

新增：`metric`、`unit`、`d_value`、`rate_per_h`、`band`、`band_source`。
旧的 `d_len_mm` / `rate_len_mm_per_h` **只在 `metric == 'mean_len_mm'` 时非空** ——
一个叫 `_mm_` 的字段不许承载 mm²（票 05 的量纲铁律同源）。
对既有消费方是**兼容新增**（旧字段仍在，取值语义更严格）。

## 4. 阈值：默认值**未定标**（要现场数据）

| 量纲 | 带宽 | `band_source` |
| --- | --- | --- |
| `mm`（杆长/投影径） | 0.1 mm/h（沿用旧默认） | `default_uncalibrated` |
| `mm2`（面积） | 上一时间点的 **1%/h**（绝对带宽对二维量没有意义） | `default_uncalibrated_relative` |

两个默认值都**不是**定标结果：与票 13 的帧质量门、票 14 的加湿阈值同批现场定标；
定标数据（同一站位多轮的真实菌簇量序列）由现场/发版官抽样提供。

## 5. 判词不变，但不再说错话

- `shrinking` 对**长度量**仍说"菇不会变短，先看图核对口径"；
- 对**菌簇量**改说"菌簇量变小可能是检出差异/遮挡/视角变化，先看图核对" ——
  面积变小不等于菇变短，不能套用同一句话。

## 6. 验收

- [x] `test_flat_view_growth_uses_projected_diameter_and_carries_units`（平视 → 投影径 + `mm/h`）
- [x] `test_flat_view_area_falls_back_and_is_marked_uncalibrated`（面积回退 + `default_uncalibrated_relative`）
- [x] `test_two_different_metrics_are_never_subtracted`（异量 → `not_comparable`）
- [x] `test_not_verified_is_not_comparable`（只吃 `verified`）
- [x] `test_flat_view_note_does_not_claim_the_mushroom_shrank`（面积变小不套长度的话）
- [x] `test_missing_every_metric_is_not_comparable`
- [x] 全量：analysis **132 passed**

## Comments

- 2026-10-13 开票并落地（同日）。响应新增字段属兼容变更；界面师那边 `/growth` 的展示
  建议直接显示 `metric`/`unit`（"每天 +320 mm²" vs "每天 +1.2 mm"）。
- 旧行为对照：改之前，平视数据下 `usable=False` ⇒ 每个框都是 `not_comparable`，
  这次修复把"判定不可用"变成"能判且带量纲"。
