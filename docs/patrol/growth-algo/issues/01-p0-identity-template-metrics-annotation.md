# 01. P0 四件定义：身份 / 模板 / 指标 / 标注规范（出票，不写码）

- **归属:** 测量师；**状态：待 Chief 批准**——P0 批准前**不写新代码、不改 DDL、不动生产**
- **权威依据:** `docs/patrol/growth-algo/plan-2026-10-14.md`（用户 2026-10-14 给定，Chief 落盘）
- **主交付变更（Plan 开篇）**：**48 个固定锚点的簇级生长趋势**为主交付；**菌帽投影直径**为独立增强项；
  **单杆分割退出本期主链路**；真实现场图离线标注 + 专用模型训练
- **本票只做定义**：把 Plan §4/§5/§8/§11/§12/§15 里"需要落成契约"的四件写死到可开发、可验收的程度
- **标注环境（用户 2026-10-14 15:19/15:45 裁定）**：**X-AnyLabeling 桌面客户端**装在 prod 服务器、标注员走 **RDP**；
  `:9213` 服务端保留给 P2 预标注。客户端部署归运维（票 02），**标签配置见 §4.5 与 `annotation-config/`**

---

## 0. 最小形态（一页 · 效率模式 2026-10-14 16:1x）—— 本节是**权威摘要**，字段级细节查 §1–§4

**0.1 身份（一表）**

| 键 | 表达 | 结论 |
| --- | --- | --- |
| `AnchorKey` | `room_id`=`images.room_id`；`rack_id`=常量 `M1`；`layer_id`=`Station.layer`；`anchor_id`=**`images.station_id`（S101…S412，共 48）** | 现成，48 锚点 ↔ 48 站位一对一 |
| `image_id` | `images` 主键 `(ts, station_id, angle_profile)` | 现成 |
| `ObservationKey` | 追加 `observation_cycle_id` | **新表 `anchor_cycles`**（未批前用 `(room_id,station_id,entry_date,batch_no)` 临时键 + `cycle_id_tentative=true`） |
| `ResultKey` | 版本四元组 `pipeline/model/roi/calibration_version` | `measurements` 表达不了 ⇒ **三层离线表** `frame_results/bag_results/cap_results`（DDL 交环境官） |

**0.2 模板 `AnchorTemplate`（一表 ×48）**
`room/rack/layer/anchor_id`｜`reference_image_id`（质量门过 + 对齐稳 + 无临时遮挡）｜`reference_image_size`=**2880×1616** 否则 `resolution_mismatch`｜
`static_background_mask`（只含货架/管线，**不含生长对象**）｜`front_roi_polygon`（不跨后排，逐帧不变）｜`excluded_regions`（亮斑/管线+原因）｜
`bag_slots[]`（固定观察袋位=分母来源）｜`reference_bag_top_lines`（每槽折线，不可见不猜）｜`registration_config`（**平移优先** + 残差门 + 变换上限）｜
`quality_config`（`lapvar/dark/over/truncation`，**ROI 内算**）｜`roi_version`（任何几何/配置变更递增，进 `ResultKey`）｜`template_sha256`｜`reviewed_by/at`。
生成 = **人工一次 + 另一人按清单复核**；存**离线文件**；天生不可用 ⇒ 显式 `anchor_unusable`，不硬凑。

**0.3 指标取值规则（一表；初值待开发集冻结，**冻结后不回改，选错另开一节**）**

| 规则 | 取值 |
| --- | --- |
| 主指标 | `mean_profile_height_px = (1/\|V\|)Σ[b(x)−t(x)]`，**px**；文案叫「菌簇平均顶部轮廓高度」，**不叫杆长** |
| `supported_columns` | 掩码列连续宽 ≥ `min_col_run_px=3`（先 3×3 开运算） |
| `reliable_top` | 该列前景最高连续段上边界；段间隙 > `max_gap_px=5` ⇒ 该列不可靠 |
| `valid_column_ratio` | 门槛 `min_valid_column_ratio=0.3`，不足 ⇒ `insufficient_contour` |
| 稳健/原始顶高 | `robust=quantile(h, top_quantile=0.9)`；`raw=max(h)`，先按 `outlier_quantile=0.995` 截尾 |
| 横向跨度 | `cluster_width_px` = 有效列跨度（与高度同一套列有效性） |
| 覆盖率 | `visible_coverage_ratio` = 簇掩码面积 / `front_roi` 面积（**仅辅助**） |
| `baseline_valid` | 五条全满足：对齐接受 / 槽位匹配 / 袋口偏移 ≤ `bag_shift_max_px=40` / 基线未遮 / 同一物理位置；任一不满足 ⇒ **高度缺测**（簇跨度另存） |
| `panel_coverage` | `matched_slots / expected_slots`，门槛 `0.8`，不足 ⇒ `panel_coverage_low` + 曲线标不可比 |
| 缺测 vs 0 | **检测失败/不可测 ⇒ NULL**；只有「确认无袋/无菌帽」才写 0 |

**0.4 样本池抽样（一列五条）**
① 先覆盖 **48 锚点**（每锚 ≥1 帧）→ ② 再按**困难类型**扩充（密/疏/粘连/低对比/反光/模糊/截断/空袋/帽三态）→
③ **不承诺固定帧数**（Plan 原话：不承诺多少帧即够训练）→ ④ 划分**按组** `(observation_cycle_id, anchor_id, time_block)`，
断言 `no_source_image_overlap()` / `no_near_duplicate_leakage()`，锁定测试集不调参 → ⑤ 双人独立标注四项：
一袋一簇归属 / 菌簇顶部轮廓 / 袋顶基线 / 菌帽身份与测量资格。

---

## 1. 身份定义与现有表的映射

### 1.1 Plan 给的逻辑身份

```
AnchorKey      = (room_id, rack_id, layer_id, anchor_id)
ObservationKey = (AnchorKey, observation_cycle_id, image_id)
ResultKey      = (image_id, pipeline_version, model_version, roi_version, calibration_version)
```

### 1.2 映射到现有 `rounds/images/measurements`（逐键给"能不能直接表达"）

| 逻辑字段 | 现有表达 | 结论 |
| --- | --- | --- |
| `room_id` | `images.room_id` / `rounds.room_id` / `measurements.room_id` | **可直接表达**（老行 NULL = 那时没记） |
| `layer_id` | `Station.layer`（`patrol/src/patrol/stations.py`）＝ `station_id` 第 1 位 | **可直接表达**（`S101` → layer 1） |
| `col` | `Station.col` ＝ `station_id` 后两位 | 可直接表达 |
| `anchor_id` | **`images.station_id`（`S101`…`S412`，48 个）** | **可直接表达** |
| `rack_id` | **不存在**（现场单台相机 `CAMERA_IP="192.168.1.238"`、一层 12 框 × 4 层） | **需常量兜底**：建议 `rack_id="M1"`；**待确认是否只有一列货架** |
| `image_id` | `images` 主键 **`(ts, station_id, angle_profile)`** | **可直接表达**（就用这个三元组当 `image_id`） |
| `observation_cycle_id` | **不存在** | **需新表/新列**（见 1.4） |
| `pipeline_version` / `model_version` / `roi_version` / `calibration_version` | **都不存在**（`measurements` 主键是 `(ts, room_id, box_id)`，无版本维度） | **`ResultKey` 无法用 `measurements` 表达 ⇒ 必须新表**（见 1.5） |

**48 锚点 ↔ 现役 48 站位：一对一**（`GRID_COLS=12` × `GRID_LAYERS=4`，每框 1 站位）。
`build_grid()` 实测 48 个 `Station(id='S101'…, box_id='B101'…, layer=1, col=1, angle_profile='level0')`。

### 1.3 三条必须写死的语义

1. **锚点 = 相机位置（站位），不是菌包**：`AnchorKey` 对应 `station_id`；
   **同一物理袋可能被相邻锚点拍到**（Plan §11.3）⇒ **锚点数量不可累加为菌包/朵数**。
2. **一袋一簇里的"袋身份"不在 `AnchorKey` 里**，而在锚点模板的 `bag_slots`（§2）+ 每帧 `bag_group_id`；
   跨日身份**不自动继承**（Plan §5.3：`bag_group_id` 只表达同帧归属）。
3. **`rounds`（轮次）≠ `observation_cycle`（周期）**：轮次是采集批次（~3 小时一轮），
   周期是"同一批蘑菇的连续观察段"（换包/采收/视角/ROI/模型/标定变更才切段，Plan §4.3 / §11.2）。

### 1.4 `observation_cycle_id`（需新表，P0 只定义）

**建议**：新表 `anchor_cycles`：

| 列 | 说明 |
| --- | --- |
| `cycle_id` | 主键（如 `611-M1-L1-S101-C01`） |
| `room_id` / `rack_id` / `layer_id` / `anchor_id` | 锚点键 |
| `start_ts` / `end_ts` | 周期起止（`end_ts` 为 NULL = 当前周期） |
| `start_event` | `bag_replacement` / `harvest` / `reposition` / `view_change` / `roi_change` / `model_change` / `calibration_change` |
| `note` / `recorded_by` / `recorded_at` | 事件来源与审计 |

**过渡口径（DDL 未批前）**：离线证据里用 **`(room_id, station_id, entry_date, batch_no)` 派生临时周期键**，
文件名/JSON 字段标注 `cycle_id_tentative=true`，**不得当成权威周期**。
⇒ **待确认**：换包/采收事件现在谁记录（`operations` 表只有环境调控动作，见 `analysis/db.py`）。

### 1.5 `ResultKey`（需新表，P0 只定义）

`measurements` **表达不了版本维度**（主键 `(ts, room_id, box_id)` 固化；加版本会破坏幂等语义）。
按 Plan §12.1/§12.2"三层证据 + 现有契约不能表达的先走离线证据与结果文件"：

| 层 | 建议表 | 主键 |
| --- | --- | --- |
| 帧级 | `frame_results` | `(ts, station_id, angle_profile, pipeline_version, roi_version)` |
| 袋级 | `bag_results` | `frame_result_key + bag_group_id` |
| 帽级 | `cap_results` | `frame_result_key + cap_id` |

**`ResultKey` = 帧级表主键 + 版本四元组**；`model_version` 落在袋级/帽级行（不同模型不同分支）。
**DDL 变更由环境官评审**；本票只给形状，**不建表**。

---

## 2. 锚点模板 `AnchorTemplate`（§4.2 落成字段）

### 2.1 字段表（每个锚点 48 份）

| 字段 | 类型 | 必填 | 说明 / 取值规则 |
| --- | --- | --- | --- |
| `room_id` / `rack_id` / `layer_id` / `anchor_id` | 文本 | ✔ | 锚点键（§1） |
| `reference_image_id` | 文本 | ✔ | = 参考帧的三元组 `(ts, station_id, angle_profile)`；**选取规则**：质量门通过 + 对齐稳定 + 菌包与袋顶清晰 + 不含临时遮挡（人挑，复核） |
| `reference_image_size` | `(w,h)` | ✔ | 必须 **2880×1616**；不符 ⇒ 记 `resolution_mismatch`（Plan §6.1） |
| `static_background_mask` | 掩码文件 + sha256 | ✔ | **只含稳定结构**（货架/管线/背景）；**不得包含菌包、菌簇、菌帽** |
| `front_roi_polygon` | 多边形（N×2，原图坐标） | ✔ | **前排限定**；不跨到后排（Plan §7.2）；**不逐帧随检测结果改变** |
| `excluded_regions` | 多边形列表 + 原因 | ○ | 亮斑/管线/水印/固定遮挡/不可判区 |
| `bag_slots` | 列表（见 2.2） | ✔ | 该锚点**固定观察的袋槽位**（Plan §11.1 的来源） |
| `reference_bag_top_lines` | 每槽位折线（N×2） | ✔ | 袋顶基线（Plan §8.3）；**不可见时不猜**，允许 `baseline_valid=false` |
| `registration_config` | `{motion:"translation", ecc params, residual_max, transform_limits}` | ✔ | Plan §6.2：**平移优先**，不默认透视 |
| `quality_config` | `{lapvar_min, dark_p99_max, over_max, truncation_max, roi_scope:"front_roi"}` | ✔ | Plan §6.1：**在目标 ROI 内计算** |
| `roi_version` | 文本（`v1`, `v2`…） | ✔ | **任何几何/配置变更 ⇒ 递增**；进 `ResultKey`；变更走 §1.4 事件 |
| `template_sha256` | 文本 | ✔ | 整份模板的哈希（追溯"这次结果用的是哪一版模板"） |
| `reviewed_by` / `reviewed_at` | 文本/时刻 | ✔ | **人工一次 + 复核**（复核清单见 2.3） |

### 2.2 `bag_slots`（槽位）

| 字段 | 说明 |
| --- | --- |
| `slot_id` | 锚点内稳定编号（如 `S101-sl01`）——**固定集合的成员标识** |
| `geometry` | 多边形或框（原图坐标） |
| `expected_bags` | 该槽位预期袋数（通常 1；用于 `panel_coverage` 分母） |
| `top_line_ref` | 指向 `reference_bag_top_lines` 的哪条 |
| `note` | 空袋/常年被遮/已废弃等 |

⇒ **固定菌包集合 = 模板里的 `bag_slots`**（Plan §11.1）。缺袋 ⇒ 记 `panel_coverage`，**不删槽位**。

### 2.3 生成方式与复核（P0 只定流程）

1. **人工一次**：逐锚点从候选参考帧里挑 1 张 → 画 `front_roi_polygon` → 画静态背景掩码 →
   标 `bag_slots` → 画各槽位袋顶折线 → 填 `registration_config`/`quality_config`；
2. **复核**：另一人按清单核（2.4）→ 通过才发 `roi_version=v1`；
3. **工具**：可复用票 21 的**离线单文件复核页**思路（点选多边形/折线、导出 JSON）；**P0 不动手做**；
4. **存储**：**离线文件**（`configs/anchor-templates/<anchor_id>.json` + 掩码 PNG），
   带 `roi_version` 与 `template_sha256`；**DB 变更另行评审**（Plan §12.2）。

### 2.4 复核清单（写死，防止模板自己变成误差源）

- 静态背景掩码**不含**任何生长对象；`front_roi` **不跨后排**；排除区**不覆盖**需要量测的槽位；
- 每个槽位**实际有一袋**（或显式记空袋）；袋顶折线**贴在袋口**而不是菌簇上；
- 参考帧**不含**临时遮挡（人手/工具/水汽）；分辨率 2880×1616；
- 锚点若天生不可用（菌包整体出画/严重反光）⇒ **显式记 `anchor_unusable` + 原因**，**不硬凑模板**。

---

## 3. 指标定义（取值规则先定，开发集后冻结）

### 3.1 主指标：平均轮廓高度（Plan §8.1）

```
H_profile = (1/|V|) · Σ_{x∈V} [ b(x) − t(x) ]        # 单位：原图像素 px
```
- `b(x)`：袋顶基线在该列的 y（模板折线经对齐映射后取该列值，且 `baseline_valid=true`）；
- `t(x)`：菌簇**顶部轮廓**在该列的 y；
- `V`：`supported_columns(mask) ∩ {x: t(x) 与 b(x) 均可靠}`；
- **该值表示"菌簇平均顶部轮廓高度"，不表示平均菌杆长度**（写进字段名与文案：`mean_profile_height_px`）。

### 3.2 先定、后冻结的取值规则（**本节就是"先登记"**）

| 规则 | 建议取值（**初值，待开发集冻结**） | 理由 |
| --- | --- | --- |
| `supported_columns(mask)` | 掩码横向投影中**连续宽度 ≥ `min_col_run_px`（初值 3）** 的列；先做**开运算 `open_kernel`（初值 3×3）** 去毛刺 | 去噪点列；列级碎片会把 `b(x)−t(x)` 拉爆 |
| `reliable_top(mask, x)` | 该列前景的**最高连续段**上边界；若最高段与下方段的间隙 > `max_gap_px`（初值 5）⇒ 该列标**不可靠** | 防止把远端菌簇/反光噪点当自身顶部 |
| `valid_column_ratio` | `|V| / |supported_columns|`；门槛 `min_valid_column_ratio`（初值 **0.3**） | 不足 ⇒ `insufficient_contour`（Plan §8.2 伪代码） |
| `robust_top_height_px` | `quantile(h, top_quantile)`，`top_quantile` 初值 **0.9** | Plan 未定 ⇒ 我先给值并标注待冻结 |
| `raw_top_height_px` | `max(h)`，但**先按 `outlier_quantile`（初值 0.995）截尾** | 单品噪点会污染最大值 |
| `cluster_width_px` | 掩码横向投影的**有效跨度**（去掉 < `min_col_run_px` 的孤立列） | 与高度用同一套列有效性，口径一致 |
| `visible_coverage_ratio` | `菌簇掩码面积 / front_roi 面积`（无量纲） | Plan §8.2"固定 ROI 内可见菌体覆盖率"；**只作辅助** |
| `baseline_valid` | **五条全满足**：① 对齐被接受；② 该槽位匹配（`slot_id` 一致）；③ 袋口偏移 ≤ `bag_shift_max_px`（初值 40）；④ 基线未被遮挡；⑤ 仍对应同一物理位置 | 任一不满足 ⇒ **高度缺测**（Plan §8.3），**簇自身跨度另存**但不冒名 |
| `panel_coverage` | `matched_slots / expected_slots`（模板分母）；门槛 `panel_coverage_min` 初值 **0.8** | 偏低 ⇒ 该锚点该次 `panel_coverage_low`，曲线**标不可比**（Plan §11.1） |
| 缺测 vs 0 | "**确认无袋/无菌帽**"⇒ 计数 0；**检测失败/不可测** ⇒ NULL | Plan §12.2：`0 只表示已确认的零` |

**冻结纪律（与票 15 §13.5 同一条）**：本节数值在**开发集分析后、查看锁定测试集结果前冻结**；
冻结后**不得因结果不好回改**；若发现规则本身选错 ⇒ **另开一节写"算不出来 + 为什么"**。

### 3.3 辅助指标（Plan §8.2 全收）

`robust_top_height_px` / `raw_top_height_px` / `cluster_width_px` / `valid_column_count` /
`valid_column_ratio` / `visible_coverage_ratio` / `baseline_valid` / `association_valid` /
`measurement_valid` / `rejection_reason`（**逐袋**，不得只留一个总数）。

---

## 4. 标注规范与样本池（Plan §5.2/5.3/5.4/5.5 落成可执行）

### 4.1 对象与属性字段（**直接采用 Plan §5.2/§5.3**，落成三张表）

| 表 | 字段（Plan 原样） |
| --- | --- |
| `ObjectAnnotation` | `image_id, object_id, object_type(bag/cluster/cap), bag_group_id, visible_mask, occluded, truncated, association_valid, measurement_valid, rejection_reason, review_status` |
| `BagTopAnnotation` | `bag_group_id, polyline, baseline_valid` |
| `FrameAnnotation` | `anchor_id, observation_cycle_id, cap_state(clear/not_clearly_formed/indeterminate), quality_tags` |

**与票 21 复核页 schema 的关系**（复用工具、不造第二套）：
`review_status` 同名同义；`occluded/truncated` ↔ 票 21 的 `occluded_frac`/`truncated`；
`measurement_valid` ↔ 票 21 的 `height_valid`（重新命名以对齐 Plan）；`bag_group_id` ↔ `cluster_id`。
⇒ **票 21 的 CSV/复核页可作为 `ObjectAnnotation` 的一种载体**，字段需按本节改名对齐（**待批后再动**）。
**客户端落点（类别/属性/帧级 flags/分组怎么在 X-AnyLabeling 里表达）见 §4.5**——本节只定义字段语义。

### 4.2 标注四条硬规则（Plan §5.2/§5.3 的"不许"）

1. 菌簇**一袋一实例可见掩码**；**不分单杆、不填满内部背景**；
2. 菌包掩码**不包含上方菌簇**；袋顶基线**不可见时不猜**（`baseline_valid=false`）；
3. 菌帽**不补全遮挡轮廓**；未明显分化 ⇒ 记 `cap_state`，**不生成虚构菌帽**；
4. 跨袋归属不明确 / 模糊 / 粘连 ⇒ 标**不可靠**（`association_valid=false` + `rejection_reason`）。

### 4.3 样本池抽样口径

**第一轮：覆盖 48 锚点**（每锚点至少 1 帧质量门通过的帧）⇒ 形成"代表样本池"；
**第二轮：按困难类型扩充**（Plan §5.4）：稀疏/密集/倾斜/跨袋菌簇；清晰/低对比/反光/模糊/截断；
袋顶清晰 vs 被遮挡；`cap_state` 三类；**空袋、包装亮斑等困难负样本**。
⇒ **不承诺"固定帧数即可满足训练"**（Plan 原话）。

### 4.4 双人复核与数据划分

- **小批双人独立标注**，重点四项：一袋一簇归属 / 菌簇顶部轮廓 / 袋顶基线 / 菌帽身份与测量资格；
  **分歧率与仲裁规则要写进结果**（谁仲裁、如何记）；
- **划分按组分，不按图片随机**：`group = (observation_cycle_id, anchor_id, time_block)`；
  `time_block` **待确认定义**（建议：同日 + 同采集轮次窗口，如 ±1 小时）；
- **断言**：`assert no_source_image_overlap()` / `assert no_near_duplicate_leakage()`
  （近重复判据 = **图像哈希 + 采集元数据**，不凭相似度拒绝短间隔真实图像，Plan §6.1）；
- **两类测试**：已知锚点不同时段 / 未见锚点或独立批次；**锁定测试集不用于调参**；
- 标注主档案保留**无损掩码 + 元数据**，转格式后**必须回读校验**。

---

## 4.5 X-AnyLabeling 标签配置（Chief 2026-10-14 15:45 追加；**只出定义，不写码**）

- **环境裁定（用户）**：桌面客户端装在 **prod 服务器**、标注员走 **RDP**；`:9213` 那套保留给 P2 预标注。
  ⇒ 客户端部署/推送/目录/备份归**运维（票 02）**；我出标签配置与标注规范，**不接生产、不点客户端**。
- **配置已落盘**（**数据文件，不是代码**）：`docs/patrol/growth-algo/annotation-config/`
  = `labels.txt` + `attributes.json` + `image_flags.txt` + `example-frame.xlabel.json` + `README.md`（含逐条硬规则与出处）。

### 4.5.1 类别清单与 ID（`labels.txt`，**行序 = 类别 ID，只追加不改序**）

| 类别 | ID | 形态（X-AnyLabeling） | 与 Plan §5.2 的对应 | 画法要点 |
| --- | --- | --- | --- | --- |
| `bag` | 0 | `polygon`（`P`／Brush `Ctrl+N`） | 菌包：可见框 + 掩码 | **只画 polygon**；可见框由脚本外接得到（不并标两套） |
| `cluster` | 1 | `polygon` | 菌簇：**一袋一实例**可见掩码 | 不填内部背景、不分单杆 |
| `cap` | 2 | `polygon` | 菌帽：单帽可见掩码 | 只在 Cap 可辨认时画；不补全遮挡 |
| `bag_top` | 3 | **`linestrip`（`Shift+S`）** | 袋顶基线：折线 + 有效性 | 贴袋口，不贴菌簇；不可见 ⇒ `baseline_valid=false` |
| `void` | 4 | `polygon` | 不可判定区域：区域 + 原因 | 严重遮挡/粘连/归属歧义/亮斑误判区 |

**形态核实（上游 main，抓取 commit `a7d2765`，`docs/en/user_guide.md` §2.1）**：支持 `rectangle(R)`、`rotation(O)`、
`polygon(P)`、`quadrilateral(T)`、`point(Shift+P)`、`line(Shift+L)`、`linestrip(Shift+S)`、`circle(Shift+C)`、`cuboid(Ctrl+R)`，
外加 **Brush Polygon(`Ctrl+N`)**、**Magic Wand(`Shift+W`)**、**Edit Brush(`Shift+B`)**——后三者产出的**仍是 polygon**。
⇒ **掩码 = polygon**（画笔/Magic Wand 不是独立形态）；**折线 = `linestrip`**（`line` 只有两点，只能表达直线段，**不采用**）；
`rotation / quadrilateral / circle / point / cuboid` 本期**不用**。
`bag_top` **做成独立类别而不是属性**：导出与筛选都按 `label + shape_type` 走，做成属性无法表达"折线挂在哪个袋上"。

### 4.5.2 属性字段在客户端里的表达（Plan §5.3 逐条落点）

| Plan §5.3 字段 | 客户端落点 | 词表 / 控件 | 备注 |
| --- | --- | --- | --- |
| `bag_group_id` | **原生 `shapes[].group_id`**（选中同袋对象按 `G`） | 整数 | **不设同名属性**；同袋的 `bag+cluster+bag_top+cap` 同组 |
| `occluded` | `attributes` | `no/partial/heavy`（radio） | 与票 21 `occluded_frac` 的 1/2/3 档对齐 |
| `truncated` | `attributes` | `no/yes`（radio） | `yes ⇒ measurement_valid=false` |
| `association_valid` | `attributes` | `true/false`（radio） | `false` ⇒ **不分组**（`group_id=null`） |
| `measurement_valid` | `attributes` | `true/false`（radio） | 归属不清/顶端不可见/截断 ⇒ false |
| `rejection_reason` | `attributes` | 枚举（combobox，见配置） | `none` + 8 类原因 + `other` |
| `review_status` | `attributes` | `confirmed/edited/rejected/human_added`（radio） | **拒掉的行必须留**（删行=洗白假阳） |
| `baseline_valid` | `bag_top` 的 `attributes` | `true/false`（radio） | 只在 `bag_top` 上 |
| `cap_state` | **image-level flags** | `cap_clear / cap_not_clearly_formed / cap_indeterminate` | **三选一**（恰好一个为真） |
| `quality_tags` | **image-level flags** | `q_low_contrast / q_bright_spot / q_blur / q_camera_moving / q_front_truncated / q_static_occlusion / q_empty_bag_present` | 可多选、可全假 |
| 帧级复核完成 | **原生 `checked`**（`Ctrl+Alt+K`） | 布尔 | 与对象级 `review_status` 是两件事 |
| （检索代理） | **`difficult`** | 布尔 | `difficult=1 ⇔ rejected 或 association_valid=false 或 measurement_valid=false` |

- **为什么用 `difficult`**：客户端搜索框只认 `difficult/gid/shape/label/type/score/description/checked`（`user_guide.md` §2.5），
  **自定义属性与帧级 flags 都搜不到**；`difficult::1` 是客户端里**唯一**能捞"问题行"的入口。属性仍是权威，`difficult` 只求可搜。
- **属性值形态**：`"true"/"false"` 是**字符串**（属性面板只吃词表值），布尔映射由转换脚本做。

**"导出 COCO 后还能回读"的正面回答**：

1. COCO 标准**没有属性位**；上游只承诺**原生 XLABEL JSON** 保属性（FAQ Q1 原文含 `attributes` 与 metadata）。
   ⇒ **主档案 = XLABEL JSON**（无损：`attributes` + `group_id` + 顶层 `flags/checked/description` 都在），COCO 是**派生物**。
2. **禁止把语义塞进文件名或类别名拼串**（不可回读、污染类别名，且改文件名=改图身份）。
3. 回读判据按票 02 §6.2 四条执行：对象数与类别逐条一致；几何在容差内；**属性键值逐条一致或显式产出丢失清单（不许静默丢）**；坐标恒 `2880×1616`。
4. **实测结论（2026-10-14，运维跑 `xlabel2coco --mode segment`）**：COCO 的 annotation **只剩
   `area/bbox/category_id/id/ignore/image_id/iscrowd/segmentation`** ⇒ **`attributes`、`group_id`、帧级 `flags` 全丢**。因此：
   - **主档案 = XLABEL JSON（无损，唯一权威）**；COCO 只是训练派生物，**不许当归档**；
   - **必须自建 join 转换器**（P1 待建；**写码按红线需 Chief 批，未批不写**）：
     输入 = 图目录 + XLABEL JSON + `labels.txt`；输出 = COCO `segment` + 侧车 `objects.json`
     （`filename` / `shape_index` / `label` / `group_id` / 全部属性 / 帧级 flags）；
   - 硬规则：join 键 `(filename, shape_index)` **1:1 可断言**（对不上报错，不许静默配错）；
     `review_status=rejected` 的对象**要么落 `iscrowd=1`、要么落侧车标记**，不许悄悄消失；类别顺序按 `labels.txt` 行序固定。
5. **未实测项（P1 第一件）**：~~客户端实际版本与文档是否一致~~ ⇒ **已核（2026-10-14，无需 GUI）**：
   本文档依据的上游 commit `a7d2765` 里 `anylabeling/app_info.py` 的 `__version__ = "4.1.1"`，与客户端实测 `4.1.1 / CPU` 一致；
   仍需实测（**被 GUI 的 GL 阻塞**，等窗口能开）：**帧级 flags 的手工设置路径**（文档只写模型预测后到 `Ctrl+3` 改，纯人工怎么设要实测；不行就退到 `View -> Image Tags`（`Ctrl+Shift+T`）写 `cap_state:clear` 这类固定串）。

### 4.6 标注操作规范一页（给标注人，工具步骤 + 六条判据）

**开工五步**：① `Open Dir`(`Ctrl+U`) 选帧目录 → ② `Upload -> Upload Label Classes File` 载 `labels.txt`
→ ③ `Upload -> Upload Attributes File` 载 `attributes.json` → ④ `Upload -> Upload Image Flags File` 载 `image_flags.txt`
→ ⑤ `Ctrl+Shift+L` 打开属性显示、`Ctrl+Shift+T` 打开 Image Tags/属性面板。

**六条判据（每条都能被复核）**：

1. **什么算一袋一簇**：一个菌包上可判为同一簇的可见菌体 = **1 个 `cluster`**；同袋对象用 `G` 分组。
   相邻两袋分不开 ⇒ 画成 1 个袋 + `rejection_reason=merged_multiple_bags`（**不要按想象拆袋**）。
2. **菌簇掩码不许填内部背景**：用 Brush(`Ctrl+N`)/Edit Brush(`Shift+B`)/Magic Wand(`Shift+W`) 贴轮廓；
   内部空隙留空；**禁止**沿框画个矩形当掩码。
3. **顶部轮廓怎么画**：只画**能看见的**顶部边界；顶端出画/被遮 ⇒ 仍画可见部分 + `truncated/occluded` 属性 + `measurement_valid=false`；**不补全**。
4. **袋顶折线何时 `baseline_valid=false`**：袋口被菌簇/水汽/QP 遮挡、或袋口出画 ⇒ 折线**不猜**（可只画可见段并标 `false`）；
   折线**贴袋口**，不许贴到菌簇顶部（否则整条高度基线失真）。
5. **帽在什么状态下不画**：`cap_state=not_clearly_formed` 或 `indeterminate` ⇒ **一个 `cap` 都不画**（只标帧级 flag）；
   只有 `clear` 才画可见单帽掩码，且**不补全遮挡轮廓**。
6. **`association_valid=false` 的典型场景**：跨袋（一簇压在两袋之间）、多袋粘连分不开、簇与袋顶归属不明、被遮挡到看不出附着位置。
   ⇒ 不分组 + `false` + `rejection_reason`；**行必须留**。

**范围与前后排**：画面内所有可见菌包/菌簇/帽**都要标**（Plan §7.2：**不得把未标注对象无意当背景**）；
**前/后排不靠标注属性区分**——由模板 `front_roi` + 掩码几何在转换时计算（避免人为不一致）；
**属性齐备只要求 `front_roi` 内对象**，后排对象画掩码即可。

**禁止**：把亮面/管线/水印/顶棚当对象；把单杆分开标；补全遮挡；改文件名表达属性；用 `0` 冒充缺测。

### 4.7 验收样本（2–3 张真机帧，含一张困难负样本）

| # | 帧（**干净原图**） | 内容 | 教学目的 |
| --- | --- | --- | --- |
| S1 | `.scratch/frame-quality-samples/samples/ok/20261013_S406_level0_092634.jpg`（工单 F06 / S406 / 10-13 09:26 白天） | 前排密集、袋口较清 | 一袋一簇 + 袋顶折线 + `cap_state=not_clearly_formed` |
| S2 | `.../samples/ok/20261013_S209_level0_152232.jpg`（F18 / S209 / 10-13 15:22 夜间） | 密集、交叉遮挡多 | 粘连仍按一袋一簇；跨袋不清 ⇒ `association_valid=false` + 行保留 |
| S3（**困难负样本**） | `.../samples/ok/20261013_S108_level0_145300.jpg`（F13 / S108 / 10-13 14:53，**相机未稳定：大面积亮面/空区**） | 旧 L1 把亮面误检成大菌包 | **亮面不是菌包**；不编造对象；标 `q_camera_moving`/`q_bright_spot` |
| 备选负样本 | `.../samples/ok/20261013_S206_level0_145442.jpg`（F14 / S206，低对比/朦胧） | 旧 L1 把顶棚误检成袋 | 低对比帧只标真实可见对象 |

- **只给干净原图**：`.scratch/annotation-20/overlays/*`（旧检测框 overlay）**只用来挑帧**，不许发给标注人（会锚定、且含旧模型错误）。
- 参考量级（**只用于教学，不是标准答案**，别当验收线）：S1 ≈25 袋/231 菇候选；S2 ≈23 袋/217；S3 ≈5 袋（绝大部分是误检）。
- **诚实边界**：上面是"**已选定 + 已写好逐对象画法**"的样例，**不是"已试画"**——试画必须在客户端里由人做（我不接生产、点不到客户端）。
  ⇒ 列为 **P1 第一件**：人在客户端按 §4.6 画这 3 张，回 3 个 XLABEL JSON；测量师按票 02 §6.2 四条回读校验。
  试画若与本节配置对不上 ⇒ **改配置**，不改规范；对得上 ⇒ 这份配置才算"可加载、可回读"（P0 门的一部分）。

### 4.8 交给运维（票 02）的落地项（**跨域，我不做**）

| # | 项 | 验收证据 |
| --- | --- | --- |
| X1 | 三个配置文件推到客户端所在服务器并在客户端加载成功 | 客户端截图（类别列表 + 属性面板可见） |
| X2 | 帧目录：从生产**只读搬运**干净原图到 `images/<date>/`（不进仓库、不进镜像） | `dir` 清单 + 尺寸 2880×1616 |
| X3 | 标注人账号、工作目录、**备份点**（人工标注是唯一不可再生资产） | 备份路径 + 一次恢复演练 |
| X4 | 离线口径：`--no-auto-update-check`；不装内置模型（人工标注零联网） | 启动命令 + 断网一次能开能存 |
| X5 | 试画 3 张（S1–S3）并把 JSON 回给测量师校验 | 3 个 `.json` + §4.5.2 回读四条 |

---

## 5. 与现有代码的差异清单（**作废 / 保留 / 待建**）

### 5.1 作废（本期不再作为交付路径）

| # | 作废项 | 依据 | 现有落点 |
| --- | --- | --- | --- |
| V1 | **单杆实例出量当 mm**（杆长路线） | Plan 开篇：单杆分割退出本期主链路 | `measure/estimate.estimate_stipe_length`、`mean_len_mm` 路线 |
| V2 | **袋尺度 mm 换算当主交付**（240/140 + θ） | Plan §10/§12.2：像素值不写毫米列；mm 需五条件解锁 | `estimate.bag_scale_refs`、`l3._scales`、`scale_source` 主线用法 |
| V3 | **写 mm 列**（`mean_cluster_w_mm`/`h_mm`/`area_mm2`/`projected_d_mm` 作交付） | Plan §12.2 | `measure/record.py` 的四个簇列 |
| V4 | **`--measure` 生产开启**（连同开启清单里的 mm 口径） | 同上；且红线"不动生产" | 票 15 §17、`deploy/measure_wire.py` |
| V5 | 票 15 §9 的"菌簇长度→`mean_cluster_h_mm`"映射 | 交付定义变更（改 px 指标） | 票 15 §9、界面师 `ce0a461` |
| V6 | 我票 22 §1–§9 中的"簇级 mm 链路"作为**主交付** | 本 Plan 取代 | 票 22（转为"作废/保留"说明） |

### 5.2 保留（可复用，不删）

| # | 保留项 | 复用方式 |
| --- | --- | --- |
| K1 | **菌包定位** `measure/bag_ruler.find_bags` | 作 **P2 的对照基线**（Plan §7.1 首选专用模型，但基线可用于冷启动/回归对照） |
| K2 | **质量门** `measure/quality.py`（lapvar 45.0 / 近黑 p99 / 过曝，标定出处） | Plan §6.1 同思想；**但计算域要改成 front_roi**（待批后改） |
| K3 | **三码/证据思路**（`no_detection`/`unreliable`/`no_scale` 不混、`estimated_fields`） | Plan §12.2"检测失败与尺度失败分开"的现成词汇；作为**离线证据 flag 词汇表** |
| K4 | **可复跑工具思想**：批量运行器、`evaluate_l1_l3.py`、票 21 离线复核页 | Plan §14 评估、§5 标注的载体 |
| K5 | `contract.QUALITY_FLAGS` 枚举与 `+` 串形状 | 离线证据沿用；**暂不写库** |
| K6 | 内容完整性排除（10-09 窗口 / `LEGACY_EXCLUSIONS` / `frame_dup`） | Plan §6.1 内容完整性一节 |

### 5.3 待建（Plan 对应节，**P0 之后按阶段门**）

| # | 待建项 | Plan 节 | 备注 |
| --- | --- | --- | --- |
| B1 | **ECC 对齐**（平移优先、残差门、变换上限） | §6.2 | 需 **OpenCV 新依赖** |
| B2 | **锚点模板 48 份 + `roi_version`** 与生成/复核工具 | §4.2 | P0 定形状，P1 才做 |
| B3 | **袋顶折线基线**（映射 + 逐帧验证 + `baseline_valid`） | §8.3 | 高度起点 |
| B4 | **一袋一簇关联**（多线索 one-to-one + 歧义/截断 ⇒ `measurement_valid=false`） | §7.3 | **不只按水平最近** |
| B5 | **簇级量测**（`mean_profile_height_px` 等 px 指标） | §8 | 主交付核心 |
| B6 | **固定集合聚合 + 四层汇总**（`panel_coverage`、分段、原始点保留） | §11 | 曲线可比性 |
| B7 | **三层证据表 + `ResultKey` 版本维度** | §12.1/§12.2 | **需 DDL 评审（环境官）** |
| B8 | **菌帽实例模型与帽径**（增强项，P5） | §9 | 与主交付解耦，互不阻塞 |
| B9 | **离线标注环境 = X-AnyLabeling 桌面客户端**（用户 2026-10-14 15:19 改裁，取代 CVAT；客户端装在 prod 服务器、标注员走 RDP）+ 标签配置见 §4.5 / `annotation-config/` | §5.1/§5.5 | 内部数据不出境；导出/回读校验归票 02 |
| B10 | **模型运行时**（YOLO26s-seg / SAM 2.1） | §3 | **GPU/torch**；现场机仅 numpy+Pillow ⇒ 离线跑 |

### 5.4 阶段门与停止规则（Plan §15，原样引用，作为准入）

- 阶段：**P0 定义 → P1 离线样本与人工复核 → P2 定位模型 → P3 簇高量测与单锚点曲线 →
  P4 48 锚点覆盖 → P5 菌帽模型与帽径 → P6 标定与生产适配**；
- 停止/降级：菌簇归属不清 ⇒ 不出该袋指标；袋顶不可靠 ⇒ 基线高度缺测（簇跨度另存）；
  菌帽不可辨认 ⇒ 不出帽径；视角不可比 ⇒ 不连接趋势；尺度未确认 ⇒ 不出确定性毫米值；
  **模型误差主要来自图像信息不足 ⇒ 改采集，不继续堆模型**。

---

## 6. 待确认（请 Chief/用户逐条回；未答不影响 P0 备审，但会影响 P1 开工）

| # | 问题 | 我的建议 |
| --- | --- | --- |
| Q1 | 是否只有一列货架（`rack_id` 用常量 `M1`）？48 锚点是否跨库房？ | 常量 `M1`；锚点键含 `room_id` 以支持多库房 |
| Q2 | "换包/采收"事件现在谁记录、记在哪？ | 目前 `operations` 只有环境调控动作 ⇒ 需新记录（`anchor_cycles`） |
| Q3 | `time_block` 的定义（用于数据划分的分组键） | 同日 + 同采集轮次窗口（±1 小时） |
| Q4 | §3.2 的初值（`min_valid_column_ratio=0.3`、`top_quantile=0.9`、`panel_coverage_min=0.8`、`bag_shift_max_px=40`） | 先按初值，**开发集后冻结** |
| Q5 | 三层证据表的 DDL 谁批、何时批？ | 环境官；**P0 不建表** |
| Q6 | 标注客户端与 GPU 运行时归属（谁维护、在哪跑） | 客户端=**运维**（装 prod 服务器 + RDP，票 02）；GPU/训练运行时仍待定 ⇒ 离线批处理 |
| Q7 | 是否允许新增依赖 **OpenCV**（ECC 对齐必需） | 需要；否则 §6.2 无法实现 |
| Q8 | 票 22（量测链路方案）是否**整体转为本票的"作废/保留"依据**，不再作为独立路线？ | 是；票 22 标注被本 Plan 取代 |

## 7. 本票的完成判据（P0 门）

- 四条定义**无歧义**：任一字段有人能照着实现而不需要再问（Q1–Q8 有答复即可）；
- **标注配置可用**：§4.5 三件配置文件通过结构校验（JSON 可解析、类别与属性引用一致、类别 ID 只追加），
  且**客户端实际版本与帧级 flags 手工设置路径、COCO 回读**已有实测结论（P1 第一件，见 §4.5.2 第 5 条 / §4.8 X5）；
- 作废/保留/待建三张表**与现有代码路径一一对应**（本票 §5 已给落点，需 Chief 确认）；
- **P0 结束前不写新代码、不改 DDL、不动生产**（红线）。