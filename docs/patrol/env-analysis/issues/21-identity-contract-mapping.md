# 21 — 身份与契约映射票（Plan 2026-10-14 ↔ 现有 `images/rounds/measurements/environment`）

**归属:** 环境官（`analysis/**` 的**表结构与写入契约**侧；只出方案）
**Status:** proposal（**不写码、不动 DDL、不出接口实现**）
**依据:** `docs/patrol/growth-algo/plan-2026-10-14.md`（权威；由 Chief 落盘）
**关联:** 票 11（measurements 契约）、票 18/20（估算与证据列）、票 19（环境记录/首日剔除）、
`docs/adr/0024-measurement-calibration-authority.md`
**P0 前置:** 本票的"待裁定"清单没冻结前，**不进入任何实现**（用户 2026-10-14：先方案后代码）。

---

## 0. 一句话结论

Plan 的三把 key 里，**只有 `AnchorKey.room_id` 现有契约直接可表达**；
`layer_id`/`anchor_id` 只能从 `station_id`/`box_id` **派生**（解析规则要写死、空值语义要写清）；
`image_id`、`observation_cycle_id`、**四个版本**、以及 Plan 的全部 px 指标
**现有契约都表达不了** ⇒ 本期（P0–P5）一律走**离线结果文件 + 证据 manifest**，
DB 变更留到 **P6 另行评审**（Plan §12.2 的原话）。

同时有三个**现有契约会出错的地方**必须先记下来（§4）：px 不许进 mm 列、
`mean_cluster_h_mm` 与 Plan 的 `H_profile` **不是同一个量**、
`measurements` 主键不含版本 ⇒ **换版重算会覆盖历史**。

---

## 1. 现有落点（本票引用的"事实"，都是已核过的代码位置）

| 表 | 主键 | 与本票相关的列 | 出处 |
| --- | --- | --- | --- |
| `images` | `(ts, station_id, angle_profile)` | `station_id`、`box_id`、`angle_profile`、`imaging_profile`、`object_name`、`object_name_source`、`content_sha256`、`frame_dup`/`dup_group`/`same_as_previous`/`duplicate_streak`、`round_id`、`room_id`/`entry_date`/`batch_no`、`y`/`z`、`ok`/`error`/`elapsed_s` | `analysis/src/analysis/db.py:93-136`、`IMAGE_FIELDS` `:680-705` |
| `rounds` | `ts` | `round_id`、`room_id`/`entry_date`/`batch_no`、`ok`/`n_results`/`n_failures`/`aborted`、`suspect_duplicate`/`dup_groups`/`content_check_error`/`n_duplicate`/`n_frames_recorded` | `:68-92` |
| `measurements` | `(ts, room_id, box_id)` | `station_id`、`angle_profile`（**不在主键**）、`n`、`mean_len_mm`/`mean_cap_mm`、`mean_cluster_w_mm`/`mean_cluster_h_mm`/`mean_area_mm2`/`mean_projected_d_mm`、`p10_len`/`p90_len`、`quality`、`estimated_fields`/`scale_source`/`mm_per_px`/`scale_ref_mm`/`scale_ref_px` | `:38-65`、`MEASUREMENT_FIELDS` `:595-600` |
| `environment` | `(ts, zone)` | 小时聚合的温度/湿度/CO₂/补光（**与 Plan 无直接耦合**） | `:12-29` |
| `batches` / `batches_state` | `(room_id, entry_date)` / 单行 | 批次台账 + 新鲜度（票 13 §3.5） | `:158-177` |

**站位/框号编码（派生的唯一依据）**：`Station.id = f"S{layer}{col:02d}"`、
`box_id = f"B{layer}{col:02d}"`，`layer ∈ 1..4`、`col ∈ 1..12`（`GRID_LAYERS=4`、
`GRID_COLS=12` ⇒ **48 个锚点**，与 Plan §1.1 一致）；双角度档时 id 带后缀（`S105-top0`）。
出处：`patrol/src/patrol/stations.py:26-27`、`:275-293`、`:567-572`。
**注意**：`Station` 上有 `layer`/`col` 字段，但**两张表里都没有这两列** —— 现在它们只活在字符串里。

---

## 2. `AnchorKey = (room_id, rack_id, layer_id, anchor_id)`（Plan §4.1）

| 分量 | 现有落点 | 判定 | 说明 / 风险 |
| --- | --- | --- | --- |
| `room_id` | `images.room_id`、`rounds.room_id`、`measurements.room_id` | **直接可表达** | 与 Plan 的 4 库房口径一致 |
| `layer_id` | 无独立列；编码在 `station_id`/`box_id` 第 1 位（`S**1**05`/`B**1**05`） | **派生可表达** | 解析函数必须写死规则：**先从 `station_id` 取，缺失时从 `box_id` 取**；两位都缺 ⇒ 该行**没有锚点身份**（不许猜层号）。解析要容忍 `-top0` 这类后缀 |
| `anchor_id` | 无独立列；编码在 `station_id`/`box_id` 后两位（= `col`） | **派生可表达** | 同上；`anchor_id` 就是 Plan 说的"每层 12 个拍照锚点"的 `col`。**它不等于 `box_id` 这个字符串**，只是同源于编码 |
| `rack_id` | **全仓零落点**（`stations.py` 里没有货架维；现场是每库房一层 4×12 的单货架） | **现在不可表达** | 建议 P0 明确口径：**单货架 ⇒ 该维省略/常量 1，不新增列**；将来多货架再评审（见 §5 待裁 2） |

**判定汇总**：AnchorKey 可以**完全用派生方式**表达（`room_id` 直取 + `station_id`/`box_id` 解析），
不需要新表 —— **前提是 P0 把"解析规则 + 缺值语义"冻结**，并且承认
**"缺 `station_id` 且缺 `box_id` 的历史行没有锚点身份"**（票 11 允许这两列可空）。

---

## 3. `ObservationKey` 与 `ResultKey`

### 3.1 `ObservationKey = (AnchorKey, observation_cycle_id, image_id)`

| 分量 | 现有落点 | 判定 | 说明 / 风险 |
| --- | --- | --- | --- |
| AnchorKey | 见 §2 | 派生 | — |
| `image_id` | **没有 `image_id` 列**；`images` 主键是 `(ts, station_id, angle_profile)`，另有 `object_name`（MinIO key）与 `content_sha256` | **需定义**（不需新表，需冻结口径） | 建议 `image_id := (ts, station_id, angle_profile)`（DB 侧身份）+ `object_name` 作 blob key。**`content_sha256` 绝不能当 `image_id`**：复制帧共享同一 sha（2026-10-09 那轮 35 帧同 sha）⇒ 不同帧会被认成同一张。⚠️ 既有隐患：这张表的主键含**可空**的 `station_id`/`angle_profile`，而 `db.py:36-37` 已记过同类教训（**主键含 NULL 列时 SQLite 认为"每行都不同"，幂等当场失效**）——`measurements` 靠 `room_id NOT NULL` 躲开了，`images` **没有**这层保护 |
| `observation_cycle_id` | **零落点**。`batches.(room_id, entry_date)` 是**入库批次**，不是锚点级观察周期 | **需新表**（P6）／P0–P5 先离线 | Plan §4.3：换包、采收、重新摆放、视角明显改变、ROI 定义改变、算法/模型/标定版本改变都开**新周期**；且**模型只能提出疑似、不自动解释为换包** ⇒ 周期必须是**人工确认事件**，不是算法推断值 |

### 3.2 `ResultKey = (image_id, pipeline_version, model_version, roi_version, calibration_version)`

| 分量 | 现有落点 | 判定 |
| --- | --- | --- |
| 四个版本 | **零落点**（`images.imaging_profile` 是**成像参数快照**，不是算法/模型/ROI/标定版本；`angle_profile` 是角度档） | **需新表或新列**，P6 评审 |
| 与现有主键的冲突 | `measurements` 主键 `(ts, room_id, box_id)` **不含任何版本**，写入是 `INSERT OR REPLACE` | **会覆盖**：同一帧换 pipeline/模型版本重算 ⇒ 旧结果被静默替换，而 Plan §4.3 要求"复算历史**或**显示版本分界" |

**⇒ 本票的硬结论**：**在版本进主键之前，Plan 的结果不许写进 `measurements`**（会互相覆盖）。
P6 二选一：① 新建"结果表/分区"且**版本进主键**；② 结果只落离线文件，DB 只存引用与索引。

---

## 4. 现有契约会出错的三处（先记住，别踩）

1. **像素值不许写进毫米列**（Plan §10、§12.2）：v1 主交付全是 **px**
   （`mean_profile_height_px`/`robust_top_height_px`/`raw_top_height_px`/`cluster_width_px`/
   `mean_cap_projected_d_px`/`median_cap_projected_d_px`），而 `measurements` 的对应列叫
   `mean_cluster_w_mm`/`mean_cluster_h_mm`（**mm**）。**名字与单位都不匹配 ⇒ 本期不进这张表**。
2. **"菌簇高度"与我们的 `mean_cluster_h_mm` 不是同一个量**：
   Plan §8.1 的 `H_profile = (1/|V|)Σ[b(x) − t(x)]` 是**相对袋顶基线的平均顶部轮廓高度**；
   `mean_cluster_h_mm` 是**菌簇竖直跨度（掩码/外接框高）**。**两者绝不许同名互指**，
   契约里一律用 Plan 的名字，`mean_cluster_h_mm` 保持它自己的语义。
3. **`measurements` 没有锚点维度**：它只有 `(room_id, box_id)` 与可空的 `station_id`，
   而 Plan 的曲线是**按锚点**（`layer` + `col`）组织的。**层内 12 锚点聚合、四层总览**
   （Plan §11.3）在没有 `layer_id`/`anchor_id` 落点之前只能靠字符串解析。

---

## 5. 三层证据的存储与追溯方案（Plan §12.1）

**Plan 的要求**：每个曲线点可追溯到**原图、掩码、基线**与**算法配置**。

| 层级 | Plan 保存内容 | 现有可用 | 缺什么 |
| --- | --- | --- | --- |
| 帧级 | 原图 ID、锚点、周期、质量、对齐、版本 | 原图（`images.object_name`/`cloud_url` → MinIO）、帧完整性（`content_sha256`/`frame_dup`/`content_check_error`）、成像快照（`imaging_profile`）、`ts`/`station_id`/`angle_profile` | **对齐矩阵与对齐判据**、`cycle_id`、四个版本 |
| 袋级 | 掩码、归属、基线、簇指标、有效性 | `measurements` 的簇级**聚合值**（且是 mm 通道） | **逐袋身份**、掩码引用、基线引用、归属/有效性、`bag_group_id` |
| 帽级 | 掩码、直径、资格、拒绝原因 | 无（`mean_cap_mm` 是旧口径的 mm 均值） | 全部 |

**方案（本期）**：证据是**大对象 + 索引**，不是 SQLite 行。

- **大对象**（掩码/基线折线/对齐变换/拒绝样本裁剪）**走对象存储**，与现存原图同一体系
  （`images.object_name` 已是 MinIO key）——**不在 DB 里存二进制**。
- **索引与状态**先落**离线 manifest（JSON/JSONL）**，一帧一条记录（袋级/帽级嵌在帧记录里）。
  DB 索引表**留到 P6 评审**（Plan §12.2 明说 DB 变更另行评审）。
- **Manifest 最小字段（供 P0 冻结，此处只提案）**：

```
frame: { image_id, anchor_key{room_id,layer_id,anchor_id}, cycle_id,
         quality_tags[], registration{accepted, reject_reason, matrix_ref},
         versions{pipeline,model,roi,calibration},
         blob{object_name}, counts{input,success,failed,skipped} }
bag:   { bag_group_id, slot_ref, association_valid, rejection_reason,
         baseline{valid, polyline_ref}, mask_ref, metrics{..._px}, valid_column_count }
cap:   { cap_id, mask_ref, diameter_px, eligible, reject_reason }
```

- **追溯链（必须能单向走通，不许断链）**：
  `曲线点 → (anchor_key, cycle_id) → 参与袋的 bag_group_id → 袋级记录 → 帧级记录 → 原图 blob + 掩码/基线 blob + 版本四元组`。
  ⇒ 结果文件里必须**冗余带** `cycle_id` 与四个版本（只靠文件名不够，改名就断链）。
- **守恒式**（Plan §12.2）：任何一层都要能验 `input = success + failed + skipped`；
  一袋失败**不清空**其他可靠袋；**检测失败与尺度失败分开计**。

---

## 6. 固定菌包集合与单锚点时序聚合（Plan §11）

**Plan 的要求**：每锚点在周期内有**固定观察集合**（主趋势用它）；缺袋记录**覆盖率**；
样本组成明显变化时曲线**标不可比或分段**；另可附"当次全部有效袋"，但**与主趋势分开**。

**存储口径（本期）**：`fixed_panel` 先落**离线文件**，一条 anchor × cycle 一份：
`{ anchor_key, cycle_id, version, source: human|auto, members[{slot_ref,bag_group_id}], effective_from/to }`。
理由：它需要**版本 + 生效区间 + 人工确认来源**，而 `measurements` 连袋级身份都没有；
DB 表留 P6（与"结果表"一起评审，避免开两次 DDL）。

**聚合输出口径（P0 冻结形状，本期不实现）** —— 直接照 Plan §11.1 的字段，**缺一不可**：

```
anchor_point: { observed_at, cycle_id, anchor_key,
                mean_cluster_height_px, median_cluster_height_px,
                valid_bag_count, panel_coverage,          # ← 覆盖率必带
                cap_summary,
                counts{input,success,failed,skipped},     # ← 守恒式必带
                missing_reason }                          # 例：panel_coverage_low
```

- **覆盖率**：`panel_coverage = matched / panel_size`，**必须与 `valid_bag_count` 同时给**
  （只给覆盖率没法解释"为什么点少了"；只给有效袋数看不出"集合是否变了"）。
  阈值（`panel_coverage_acceptable`）在开发集后冻结（Plan §14.2）。
- **两套口径分开**：主趋势 = 固定集合；"当次全部有效袋"另出一份，**字段名与文件分开**，
  不许合并成一个数（Plan §11.1 原话）。
- **时序规则落进结果文件**：用**采集时间**（`images.ts`/`measurements.ts`）不用处理时间；
  保留原始点；平滑只标 `display_smoothed`（不改 `value`）；缺测**不补**；
  **不跨长缺测区间算速率**；换包/视角/四版本变化 ⇒ `segment_id` 分段。
- **四层总览**：相邻锚点可能拍到同一袋 ⇒ **未去重不许累加为全层数量**（Plan §11.3）：
  总览只出"有效锚点数 / 各锚点相对自身基准的变化 / 层内中位数与离散范围"，
  计数类指标必须带 `dedupe_status: not_deduplicated` 标记。

---

## 7. 与现有票、挂起项的关系

- **票 20（证据列）**：只服务 **P6 的 mm 估算通道**（`estimated_fields`/`scale_source`/`scale_ref_*`）；
  Plan 的 px 主交付**不以它为路径**。**"往 measurements 落 mm"那部分等 P6**（Chief 2026-10-14）。
- **票 11（measurements 契约）**：本票**不改**它；只在 §4.3 记下"没有锚点维 + 主键不含版本"两个既有事实。
- **票 19（环境记录/首日剔除）**：按已裁口径排期，**不抢主线**。
- **票 13（批次台账）**：`batches` 是**入库批次**身份，与 Plan 的 `observation_cycle_id` **不是一回事**，
  本票不合并两者（合并会把"换包"与"入库"混成一个概念）。
- **挂起（等 P6）**：`--measure` 生产开启、mm 列写入、票 20 的 mm 部分。

---

## 8. 待裁定（请 Chief 逐条裁，P0 冻结的前提）

1. **`image_id` 取什么**：组合键 `(ts, station_id, angle_profile)`（我倾向）+ `object_name` 作 blob key，
   还是引入 surrogate id？（另需处理 `angle_profile` 可空对主键唯一性的既有影响。）
2. **`rack_id`**：确认"每库房单货架"⇒ 该维省略；否则要么常量列、要么等真有多货架再评审。
3. **`layer_id`/`anchor_id`**：只冻结**解析规则**（`S<layer><col>`，容忍 `-top0` 后缀，
   `station_id` 缺失时回退 `box_id`，两者皆缺 ⇒ 无锚点身份），还是要落独立列？
4. **`observation_cycle_id`**：谁产生、以什么事件为准（人工确认换包/采收/重摆）；要不要一张事件表？
5. **四个版本**：P6 进 DB（**必须进主键/分区键**）还是全程离线？
6. **px 指标**：P6 加 `*_px` 列还是新表？（本票不预设，只标"不许进 mm 列"。）
7. **`fixed_panel`**：谁维护（人工文件 vs 表）、覆盖率阈值谁冻结。
8. **证据大对象**：沿用 MinIO（与 `object_name` 同体系，我倾向）还是落文件系统目录？

---

## 9. 本票**不做**的事（明确边界）

- 不写任何代码、不动任何 DDL、不开任何端点（P0 未冻结前不出实现：用户 2026-10-14）。
- 不替测量师/算法侧决定指标定义（Plan §8.2 的分位数、噪点、有效列规则由开发集冻结后写死）。
- 不把 `batches`/`entry_date` 当作观察周期，也不把 `content_sha256` 当作 `image_id`。

## Comments

- 2026-10-14 写票：起因是用户给出完整算法 Plan 并收窄口径（v1 只出像素；新指标先入离线证据/结果文件；
  DB 变更另行评审）。本票把 Plan 的三把 key 逐分量对到现有契约上，标出"直接可表达 / 派生可表达 /
  需新表"三类，并把三层证据、固定集合与覆盖率口径写成可实现的口径草案。
- 结论先行：**AnchorKey 能派生表达；ObservationKey 的 `image_id` 需冻结口径、`cycle_id` 需新表；
  ResultKey 的四个版本零落点，且现有 `measurements` 主键不含版本 ⇒ 换版重算会覆盖历史** ——
  这三条是 P0 阶段必须先裁掉的歧义。
