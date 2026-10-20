# 15. P3-3c Level 0/1/2、逐级降级与离线证据行

Status: implemented（2026-10-20，算法；10 条单测；DDL 未动）
归属：算法（契约形状需环境官会签）
写域：`measure/**`（`measure/src/measure/**`、`measure/tests/**`）＋本票
依据：`docs/patrol/growth-algo/spec.md` §4.7（层级与降级表）、§4.4（逐指标独立有效性）、
§7（固定集合、`panel_coverage`、分段）、§8（离线优先）、§9.4
依赖：13（主指标）、14（区域指标）

## 1. 交付物

1. 三层结果对象 + **离线证据行**（JSONL）：帧级 / 袋级 / 锚点级各一行，逐袋不合并成总数。
   **不改 DDL**：先落 `frame_results.jsonl` / `bag_results.jsonl` / `anchor_results.jsonl`（§8 的离线优先）。
2. 降级路由：把 §4.7 的五种失效各映射到**精确的 NULL 集合**，而不是"整行不可用"。
3. 逐指标有效性：`baseline_valid` / `association_valid` / `measurement_valid` /
   `cluster_height_usable` / `region_metrics_usable` 各自独立，一个失败不拖垮整行。
4. 锚点级聚合与 `panel_coverage` 定义（§2 末）。
5. `contract.QUALITY_FLAGS` 补新码（`baseline_invalid`、`association_ambiguous`、`insufficient_contour`、
   `bag_mask_missing`、`merged_multiple_bags`、`baseline_or_mask_inconsistent`），
   但**不把它们塞进 `NON_USABLE_FLAGS`**——那份清单是行级的，v6 要的是指标级。

## 2. 口径（写死）

**层级与字段（照 §4.7，只补存储落点）**

| Level | 粒度 | 必出字段 | 落点 |
| --- | --- | --- | --- |
| 0 | 锚点级，不做袋归属 | `t(x)`、`cluster_width_px`、`area_px`、`eq_diameter_px`、`coverage`、`mask_source` | `anchor_results.jsonl` |
| 1 | 袋级（一袋一簇 + 基线） | `H_profile`、`H_p50`、`H_p90`、`rel_profile_height`、`rel_top_height`、`rel_width`（附 `ref_px`/`ref_kind`） | `bag_results.jsonl` |
| 2 | 帽级（仅帽期） | `cap_count`、`cap_area_fraction`、`cap_diameter_px` | 同袋级行，`cap_*` 前缀 |
| 3 | 杆级 | **本期不出字段**（legacy 见票 16） | — |

**降级路由（与 §4.7 的表逐行一一对应，每行一条测试）**

| 失效 | 置 NULL | 仍出 | 理由码 |
| --- | --- | --- | --- |
| 基线不可用 | `H_profile`/`H_p50`/`H_p90`/`rel_*` | Level 0/1 的宽度、面积、等效径 | `baseline_invalid` |
| 归属歧义 / 多袋合并 | 全部袋级指标 | Level 0 锚点级形态指标 | `association_ambiguous` / `merged_multiple_bags` |
| 簇掩码缺失或低置信 | 该袋全部指标 | 质量标记与缺测统计 | `no_detection` |
| 帽模型不可用 / 无合格帽 | `cap_*` | Level 0/1 全部 | `cap_model_unavailable` / `no_cap`（本票新增码） |
| 主指标全 NULL | 该帧该袋不出点 | 计入 `panel_coverage` | （沿用上游理由码） |

**"确认无袋/无菌帽"才写 0**：新增显式 `confirmed_absent: bool`，
只有"完成有效观察且确认没有"才允许 `0`；检测失败一律 `NULL`。**断言层面禁止把 `None` 转 `0`**。

**逐指标有效性（v6 核心，不许回退到行级）**

| 谓词 | 合成自 | 影响字段 |
| --- | --- | --- |
| `cluster_height_usable` | 质量门 ∧ 对齐接受 ∧ `association_valid` ∧ `baseline_valid` ∧ `valid_column_ratio` 达标 | 只影响 H 三兄弟与 `rel_*` |
| `region_metrics_usable` | 掩码存在 ∧ `mask_source≠None` ∧（`mask_source=cluster_minus_bag` 或显式降级） | 只影响宽度/面积/等效径 |
| `cap_metrics_usable` | 帽模型可用 ∧ 帽实例通过资格 | 只影响 `cap_*` |

**锚点级聚合（spec 未定，本票给默认并标注待裁）**

- `anchor_H = median over bags with cluster_height_usable=true`；
  同时出 `n_bags_valid` / `n_bags_total` / `panel_coverage = n_bags_valid / n_bags_total`。
- **不用 mean**：单袋异常（粘连、遮挡）会被均值放大；中位数与 §8 的"层内趋势中位数"口径一致。
- 缺袋**不删槽位**；`panel_coverage < panel_coverage_min=0.8` ⇒ 该锚点该帧曲线点标 `panel_coverage_low`。
- 每条锚点/曲线点带 `segmentation_status`（§9.4 的既有断言，这里只需把字段透传，不许丢）。

## 3. 验收（缺一条不算过）

- [ ] §2 降级表**每一行一条用例**，断言精确 NULL 集合（不是"有值是 None 就行"）：
      例如基线不可用时宽度/面积**必须仍有值**，`H_*` **必须全 None**
- [ ] "基线不可用"与"归属失败"叠加 ⇒ 袋级全 NULL、Level 0 仍出；顺序无关（两条路由可交换）
- [ ] 任何失败路径都**不出现 0**；`confirmed_absent=false` 时值为 `None`
- [ ] `confirmed_absent=true` 且无袋 ⇒ `n_bags=0` 与相应计数为 0，**不是 NULL**（正向用例）
- [ ] 行级 `NON_USABLE_FLAGS` 不会被新码污染：带 `baseline_invalid` 的行，
      `region_metrics_usable` 仍可为真（回归用例，防"一个失败拖垮整行"）
- [ ] JSONL 回读：三层行可 join（帧 → 袋 → 锚点），键为
      `(image_id, pipeline_version, model_version, roi_version, calibration_version)` + `slot_id`
- [ ] 每袋行的 `mask_source` / `reasons` / 证据指针（掩码路径、基线路径、配置哈希）齐全
- [ ] `panel_coverage` 的边界值测试（0.79 / 0.80）
- [ ] `measure/tests` 全绿；不改 DDL、不写毫米列

## 4. 不做什么

- **不建表、不改 `measurements` 主键**：逐袋粒度与 `(ts, room_id, box_id)` 不匹配，
  硬塞进去会把"每袋"压成"每框"（票 05 的写库路径**不承载**本票的逐袋结果）
- 不做曲线渲染、不做四层总览（P3-4/P4-1）
- 不做 mm、不做帽径 mm、不做事件分段（事件源仍是 B4）

## 5. 复跑

```bash
uv run --no-project --offline --with pytest --with numpy --with pillow \
  pytest measure/tests/test_profile_result.py -q
```

## Comments

- 2026-10-20 开票：v6 新增 Level 0/1/2，但"降级后到底哪些字段是 NULL"只在表里写了一遍，
  没有可执行契约。本票把它变成逐行用例，并把"逐指标有效性"从 `contract` 的行级语义里拆出来。

### 实现证据（2026-10-20）

- 落点：`measure/src/measure/profile_result.py`（`ResultKeys` / `BagResult` / `AnchorResult` /
  `FrameResult` / `build_bag_result` / `aggregate_anchor` / `build_frame_result` / `write_jsonl` / `read_jsonl`）。
- 契约：`measure/src/measure/contract.py` 新增 11 个 v6 理由码，**未加入 `NON_USABLE_FLAGS`**（指标级，不是行级）。
- 单测：`measure/tests/test_profile_result.py` **10 passed**。
  命令：`uv run --no-project --offline --with pytest --with numpy --with pillow pytest measure/tests/test_profile_result.py -q`
- 已钉住：基线不可用只杀 H、不杀区域指标；归属失败袋级全 NULL；检测失败 `no_detection` 且**不写 0**；
  缺袋掩码只杀区域指标；帽模型缺失不阻塞簇级；`confirmed_absent` 是写 0 的唯一入口；
  `rel_*` 缺 `ref_px` ⇒ NULL + 理由；锚点中位只对可用袋取、缺槽位进分母；
  JSONL 回读 `null` 仍是 `None`、五元版本键齐全。
- 未做：DDL（按 §8 离线优先）；`cap_*` 的真实帽模型输入。
