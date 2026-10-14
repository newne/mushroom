# 20. 估算证据列进测量行（`estimated_fields` / `scale_source` / `mm_per_px` / `scale_ref_*`）

- **归属:** 实现 = 环境官（`analysis/**` 表结构与写入契约）；提出 = 测量师；依据 = 票 18 §2/§3、用户 2026-10-14 口径
- **Status:** open（测量师出票，不动 analysis 侧一个文件）
- **日期:** 2026-10-14
- **关联:** `docs/patrol/env-analysis/issues/11-measurements-contract.md`（15 列主契约）、
  `18-scale-and-forecast-field-proposal.md`（`estimated_fields` / `estimate` / `scale_source` 口径）、
  `docs/adr/0024-measurement-calibration-authority.md`、测量侧票 15

## 1. 背景（用户 2026-10-14 口径）

一期交付量改成 = **菌簇长度 / 菌簇直径**，走**估算（estimate）+ 不确定度**，
用**菌包标准尺寸**（袋长 240 mm / 袋径 140 mm）做帧内标尺；
**估算值不得进 `verified` 统计、区间表与建议层**（票 18 §2）。

票 18 定的口径是**两层**：

- **行级** `estimate`：进 `quality` 字符串，与 `verified` **正交**；
- **字段级** `estimated_fields`：**列名清单**——因为同一行里可以"某些列是估算、某些列是实测"。

## 2. 现状（测量侧实测，2026-10-14）

`measure/src/measure/record.py` 的 `RECORD_FIELDS`（与 analysis 侧 `MEASUREMENT_FIELDS`
必须**一字不差**，票 11 §1 铁律）目前是 15 列，**没有**这些证据列：

```
ts, room_id, box_id, station_id, angle_profile, n,
mean_len_mm, mean_cap_mm, mean_cluster_w_mm, mean_cluster_h_mm,
mean_area_mm2, mean_projected_d_mm, p10_len, p90_len, quality
```

而过滤规则的**唯一入口已经在测量侧实现**：

```python
contract.field_is_usable(quality, estimated_fields, field) -> bool
#   ⇔ "verified" in quality and field not in estimated_fields.split("+")
```

⇒ 缺口是：`estimated_fields`（以及标尺证据）**进不了行**，只在离线 `MeasurementRecord.notes`
里（`:70-73` 明写"不在线格式里"）。结果：

- 只用行级 `estimate` 的话，下游只能**整行**处理——要么整行丢（把同一行里真正可用的 `n`、
  `station_id` 也丢了），要么整行收（把估算的 mm 当实测进区间表）。**这正是票 18 §2 要防的**；
- `scale_source` 也进不去 ⇒ 没人能从行里看出"这个 mm 是标准尺寸推的还是站位标定值"，
  而 ADR-0024 要求"两位标尺（每站位 `--mm-per-px` vs 菌包标准尺寸）**可区分**"。

## 3. 请环境官裁定（三条）

1. **加列还是加一个 JSON 列**：
   - 方案 A：按票 18 加独立列 `estimated_fields text`、`scale_source text`、
     `mm_per_px double precision`、`scale_ref_mm double precision`、`scale_ref_px double precision`；
   - 方案 B：一个 `scale_evidence jsonb`（含 `source`/`mm_per_px`/`ref_mm`/`ref_px`）
     + 独立 `estimated_fields text`（因为过滤规则要按它 split，放 JSON 里反而更绕）。
   我**没有偏好**，但 `estimated_fields` 建议**独立 text 列、"+"/"," 连接**——
   与 `contract.field_is_usable()` 现有实现一致（现在按 `"+"` split，你定连接符我同步改一侧即可）。
   注意别与 `measurements.estimate`/`forecasts.model_version` 混名（票 11 §3 唯一命名）。
2. **`scale_source` 的枚举**：`bag_standard`（菌包标准尺寸，用户 2026-10-14 口径）/
   `calibration_target`（每站位 `--mm-per-px`，ADR-0024 优先）/ `none`（无标尺 ⇒ 值 NULL + `no_scale`）。
   票 18 原文写的是 `bag_diameter`，标准尺寸口径下建议改名/加值，**由你定**。
3. **幂等键与既有行不变**：仍 `(ts, room_id, box_id)`；新列可空（历史行 NULL 表示"无证据"，不是 0）。

## 4. 我的义务（列一落地就做，不需要再开票）

1. 同步改 `measure/src/measure/record.py` 的 `RECORD_FIELDS`（**两侧同时改**，一字不差）；
2. 产行时填 `estimated_fields`（如 `mean_cluster_w_mm+mean_cluster_h_mm`）、`scale_source`、
   `mm_per_px`、`scale_ref_mm`/`scale_ref_px`（可交叉验算 `mm_per_px ≈ ref_mm / ref_px`）；
3. 回归测试钉住：**估算行必带 `estimate` + `estimated_fields`**，缺标尺时 `scale_source=none` 且
   mm 列为 **NULL**（绝不写 0）。

## 5. 列落地前的过渡口径（请确认）

在证据列入库之前，测量侧仍会产估算行（`quality` 含 `estimate`，mm 列为估算值或 NULL）。
**过渡期内请下游（区间表 / 建议层 / 统计）把含 `estimate` 的行整行排除**——
保守但安全；这期间**字段级过滤不可用**，这正是本票要修的东西。请你确认这条过渡口径写进票 11 或票 18。

## 6. 证据

- `measure/src/measure/record.py:34-50`（15 列）、`:70-73`（notes 不在线格式里）；
- `measure/src/measure/contract.py:52-61`（`field_is_usable` 现实现，按 `"+"` split）；
- 测量侧票 15 §9/§10（一期交付量 → 列映射；不确定度一期不加列）。
