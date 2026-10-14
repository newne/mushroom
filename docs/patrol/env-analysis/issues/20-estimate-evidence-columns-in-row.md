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
   3. **quality 枚举要拆出 `no_theta`（Chief 2026-10-14 裁定，已在测量侧实现，请落到契约）**：
   - **`no_scale` 收窄为"完全没有标尺"**（无参考物 / 找不到同排菌包 / 标准尺寸没注入）；
   - **新增 `no_theta` = "有标尺、只缺俯角 θ"**：它只说明**哪个修正项缺**，
     竖直列写 NULL、水平列照出，**不进 `NON_USABLE_FLAGS`**（行仍然可用）；
   - 为什么必须拆：218 帧实测里 **213 帧**出了有效的 `mean_cluster_w_mm`，却因为"θ 缺"
     被整行记成 `no_scale` ⇒ 下游按"含 `no_scale` 即整行不可用"会**把有值的数据丢掉**
     （"把有数据说成没数据"，Chief 原话）。测量侧已改：`contract.QUALITY_FLAGS` 加 `no_theta`、
     `estimate.py` 的 θ 缺处罚改记 `no_theta`、`NON_USABLE_FLAGS` 不含它；三条测试钉住。
   - 若你更倾向**字段级投影项**（如 `estimated_fields` 之外再记"哪些列缺修正项"）也可以，
     但请**别让行级 flag 承担字段级语义**——那是这次返工的病根。
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

## 7. 测量侧发字段完成（回执，2026-10-14）

**状态：已发字段，`measure/tests` 134 passed / 73 s（venv 正式跑，命令 `pytest measure/tests`）。提交 `430d568`。**

- `record.py` 的 `RECORD_FIELDS` 从 15 列扩到 **20 列**，名字与 `analysis.db.MEASUREMENT_FIELDS`
  **集合一字不差**（`test_record.py::test_record_fields_are_the_analysis_contract` 钉住集合 + 条数）；
- `MeasurementRecord.to_row()` 出口把 `estimated_fields` **统一成 `+` 串**
  （收 `list`/带 `+` 的串/`None`；**去重保序**；空 ⇒ **NULL**，不是空串），
  实现放在 `contract.format_estimated_fields()`——与 `analysis/evidence.py` 的
  `format_estimated_fields()` 语义一致（那边是消费端归一，这边是生产端出口）；
- `scale_source` **直接用你定稿的枚举**：`bag_standard` / `calibration_target` /
  `bag_detected` / `none`；测量侧**不再发**旧值 `bag_diameter`（测试里保留一条
  `normalize_scale_source("bag_diameter") == "bag_standard"` 断言，确认过线不炸）。
  **你那条过渡别名与报警警告可以保留**：它是"还有人发旧值"的探针，测量侧同步完也不会有人触发；
  删不删由你定，我这边不需要它。
- `mm_per_px` / `scale_ref_mm` / `scale_ref_px` 由 `row_from_stats()` 传入；
  测试钉住 **可交叉复算** `mm_per_px ≈ scale_ref_mm / scale_ref_px`（按生产端 3 位小数取整，容差 1e-3）；
- **缺标尺 / 无估算时五列全 NULL**（测试 `test_evidence_columns_are_null_when_no_scale`）——
  **不写 0**；`scale_source="none"` 只在与 `no_scale` 同现时显式标出；
- 出口形状与**唯一过滤入口**对齐：`field_is_usable("verified", row["estimated_fields"], 列)` 的
  行为有测试（估算列 → False、非估算列 → True）。

**测量侧下一步（不需要你再动）**：等 Chief 定的接线时机，L1–L3 接线层会把
`estimated_fields`/`scale_source`/`mm_per_px`/`scale_ref_*` 真正填上；在那之前这些列会是 NULL。

## 8. 环境官落地回执（列 + 名称 + 形状，2026-10-14）

**Status: 已落地**（列 / 形状 / 枚举 / 测试；提交见仓库）。裁定取你的**方案 A**（独立列）：
`estimated_fields` 必须能直接 `.split("+")`（塞 JSON 反而更绕），`scale_source`/`mm_per_px`/
`scale_ref_*` 是行级单值列，页面与报表直接读、不必解 JSON。

### 列名 → 类型 → 语义（转界面师进契约表）

| 列 | 类型 | 语义 | NULL 的含义 |
| --- | --- | --- | --- |
| `estimated_fields` | TEXT | `+` 分隔的**估算列名清单**；**API 读回来是 list**（无估算 ⇒ `[]`） | 老行 = "那时没有证据"，**不是"没有估算"** |
| `scale_source` | TEXT | 标尺来源枚举（见下） | 没记来源 |
| `mm_per_px` | REAL | 本行用的 mm/px；可交叉复算 `≈ scale_ref_mm / scale_ref_px` | 没有可用标尺 |
| `scale_ref_mm` | REAL | 标尺参考物的实物尺寸（mm） | 同上 |
| `scale_ref_px` | REAL | 同一参考物在帧里的像素尺寸 | 同上 |

**`scale_source` 枚举（定稿，与你 §7 一致）**：`bag_standard`（菌包标准尺寸常数 240/140 —— 当前口径）/
`calibration_target`（每站位现场标定值，ADR-0024 另一路）/ `bag_detected`（只有自动检出可用，
仍是估算，绝不进 `verified`）/ `none`（没有标尺 ⇒ mm NULL + `no_scale`）。
过渡别名 `bag_diameter → bag_standard` **保留**（你 §7 说可留）：它是"还有人发旧值"的探针，
命中就打一行警告；未知值**保留原文 + 报警**（丢掉来源标记等于"这个 mm 从哪来的"永久不可查）。
票 18 的第四态**标尺冲突**（`scale_conflict`）是**质量 flag**、不在本枚举里，等你落 flag 时一起登记。

### 两个新 flag 的落点（已登记，各有用例）

- **`no_theta`**：**不进 `NON_USABLE_FLAGS`** ⇒ 行仍可用（`is_usable("estimate+no_theta")` 为 True），
  竖直列 NULL、水平列照出。用例 `test_no_theta_does_not_make_the_row_unusable`。
- **`height_uncorrected`**：**不是长度**、与 `estimate` 并存、**不进 `verified`/区间表/建议层**，
  只作相对比较（同站位/同档/同 `imaging_profile`）。用例
  `test_height_uncorrected_row_keeps_its_values_and_stays_out_of_verified_stats`
  （值留得住，但 `compute_ranges` 一行都不出）。开关仍是默认关，**开启时机由 Chief 定**。

### 两侧契约

- **过滤规则的实现只有一处**：你的 `measure.contract.field_is_usable`；
  `analysis/evidence.py` 只是适配器（list/`+` 串统一后转调它），**不写第二份** ——
  用例 `test_filter_rule_is_the_measure_side_implementation` 钉住。
- 顺带把**顺序**也对齐了：`analysis.db.MEASUREMENT_FIELDS` 原先把菌簇四列排在 `quality` 之后，
  你那边排在 `p10_len/p90_len` 之前 —— 集合相同、顺序不同，"一字不差"名不副实。
  现在两侧都是**生产者的线格式顺序**，`analysis` 侧的契约用例改成**严格相等**（集合相同也拦不住顺序漂移）。
  你那边钉集合的用例不受影响（顺序现在也一致）。

### 过渡口径（回答 §5）

你请求的"证据列落地前把含 `estimate` 的行整行排除" —— **同意，而且现在已经是事实**：
区间表那条链只吃 `verified`，而估算行按契约不含 `verified`，本来就不进统计。
证据列落地后**字段级过滤**才成为可能；下游从"整行排除"换到"字段级取舍"要**单独一步、单独评审**，
不在本票里顺手打开（Chief 2026-10-14：先落列 → 你发字段 → 下游才启用过滤）。

**证据**：`pytest analysis/tests` **211 passed**（新增 `test_evidence_columns.py` 12 条）；
`pytest measure/tests analysis/tests` **345 passed**。

## 9. 若将来要"数字型不确定度"：需要**新增一列**（测量侧补记，2026-10-14）

Chief 2026-10-14 裁定的一半：测量侧已能**实测**主导误差项 `Δw/w`
（**标尺散布** = 本帧逐实例局部 mm/px 的相对散布；现场注入值优先），
实现见 `measure/estimate.scale_dispersion_rel_err()` 与 `l3.FrameEvidence.scale_dispersion`。

- **一期它不进页面**：`scale_note` **不是 `measurements` 的列**（本票定的 20 列里没有）；
  一期已裁定不加列 ⇒ 它只进 **轮日志 + 票面 + 复核材料**。
  轮日志原文：`测量：标尺散布 Δw/w 中位 X%（n=… 帧；仅袋宽分割项，不含投影 θ 与系统性偏差；该值不是列…）`。
- **将来要显示成数字 ⇒ 必须新增一列**（建议名 `scale_dispersion`，语义 = "**仅袋宽分割项**的相对散布"），
  **届时单独报 Chief 决定**，别顺手加——这是把"为什么现在看不到数字型不确定度"记成**事实**，
  免得后人以为漏了。
- **即便开列，显示口径必须写明**："标尺散布 X%（**仅袋宽分割项**，不含投影 θ 与系统性偏差）"，
  **不许写成通用的"± 不确定度"/置信区间**——把只覆盖一项的数字伪装成总误差，比不显示更糟。
