# 14. L2/L2b 去袋与区域指标口径（`mask_source`）

Status: implemented（2026-10-20，算法；11 条单测；真实掩码验收等 P2-1/票 19）
归属：算法
写域：`measure/**`（`measure/src/measure/**`、`measure/tests/**`）＋本票
依据：`docs/patrol/growth-algo/spec.md` §4.2（区域指标的硬前提）、§6 L2 与 L2b 行、
§4.7（Level 0/1）、用户故事 35/37
依赖：实例掩码来源 = P2-1 的模型产物（**该工作包尚无票**）；
本票用合成掩码 / `measure.baseline` 的区域实例 / 包络掩码开工，模型到位后只换输入

## 1. 交付物

1. `mask_source` 词表与落盘约束（§2）：区域指标**必须**带 `mask_source`，缺了就是契约违规。
2. 新模块 `measure/debag.py`：`cluster_mask \ bag_mask`（含袋面可见部分），并返回
   `removed_area_ratio`（被减掉的面积占比）——它是"袋簇是否粘连"的第一手诊断。
3. 区域指标改由**公开函数**计算（现有 `baseline._blob_metrics` 是私有实现）：
   `region_metrics(mask, *, mask_source) -> RegionMetrics`，字段与 §4.2 对齐。
4. `cluster_h_px` 的语义钉死：**外接框跨度、仅形态摘要**，不得当簇高、不得替代 `H_profile`。

## 2. 接口与口径（写死）

**`mask_source` 三个值（本票补登记，需回写 §4.2）**

| 值 | 何时用 | 区域指标 | 备注 |
| --- | --- | --- | --- |
| `cluster_minus_bag` | 有 bag 实例掩码 | **唯一合法**用于交付的区域指标 | 交付口径 |
| `cluster_raw` | 无 bag 掩码，但有 cluster 实例 | 只作形态摘要 | 宽度会被袋宽污染，`cluster_h_px` 含袋的可见高度 |
| `envelope` | 无模型兜底（§6 L2 的 H1 亮实体包络） | 粗，只作降级证据 | 同时记 `fallback=envelope` |

> spec §4.2 只写了 `cluster_minus_bag` 一个值，但 §4.7 明确要求"没模型时仍出锚点级宽度/面积"，
> 而那条路径的掩码**不可能**是去袋的。两个值不补，实现者只能要么写错 `mask_source`，要么不出数。

```python
subtract_bag(cluster_mask, bag_masks) -> DebagedMask
region_metrics(mask, *, mask_source, column_validity=None) -> RegionMetrics
```

| 字段 | 说明 |
| --- | --- |
| `area_px` | 去袋后掩码面积 |
| `eq_diameter_px` | `2√(A/π)` |
| `cluster_width_px` | 去袋后掩码最宽行跨度；与高度**同一套列有效性** |
| `cluster_h_px` | 上下跨度，**仅形态摘要**（`mask_source` 必带） |
| `removed_area_ratio` | `1 − A_clean / A_raw`；> 阈值只报警、不自动判失效 |
| `valid_column_count` / `valid_column_ratio` | 与票 13 同一套口径，供 `V` 复用 |
| `bag_mask_missing` | 有 cluster 无 bag ⇒ **区域指标 NULL**；**高度不受影响**（§6 L2b） |

**四条硬规则**

1. `bag` 掩码要减的是**实例掩码全集**（含袋面可见部分），不是只减袋口那条线。
2. 多袋合并进同一个 `cluster` 实例 ⇒ **不拆、不按面积分摊**；标 `merged_multiple_bags`，
   由票 15 的路由降级——本票只负责"不去猜"。
3. `bag_mask_missing` 只杀区域指标，**不杀 `H_profile`**（高度只依赖 `b(x)` 与簇顶）。
4. 包络兜底路径必须让 `mask_source=envelope` 且 `fallback=envelope` **同时**出现，便于事后筛掉。

## 3. 验收（缺一条不算过）

- [ ] 合成图：底部一块 100×60 的袋 + 上方簇；原始掩码最宽行 = 袋宽；
      去袋后最宽行 = 簇宽（**严格小于**袋宽），且 `removed_area_ratio > 0`
- [ ] 去袋后掩码不连通成两块时：不合并、不补洞，`area_px` 如实变小，`removed_area_ratio` 记录
- [ ] `bag_mask_missing` ⇒ 四个区域指标全 `None`、`mask_source=cluster_raw`；
      同一帧的 `H_profile`（接票 13）**仍有值**（集成用例）
- [ ] 每个区域指标行缺 `mask_source` ⇒ 断言失败（契约级测试，不是文档约定）
- [ ] `cluster_raw` 的 `cluster_h_px` 与 `H_profile` 在同一合成图上**必须不同**；
      断言消费侧不会拿 `cluster_h_px` 当高度（与票 16 的断言同一条，避免两边各写一套）
- [ ] 多袋合并用例：输出里**没有**任何按面积/比例拆出的袋级指标
- [ ] 不改 DDL、不写毫米列、不新增依赖

## 4. 不做什么

- **不训练/不接模型**（P2-1 尚无票；本票只定义"拿到掩码之后"）
- **不做归属**（一袋一簇在 L3，票 15 只消费 `association_valid`）
- **不算 `H_profile`**（票 13）
- 不做掩码后处理美化（不补洞、不膨胀、不做形态学闭运算去"修复"边界）

## 5. 复跑

```bash
uv run --no-project --offline --with pytest --with numpy --with pillow \
  pytest measure/tests/test_debag.py measure/tests/test_region_metrics.py -q
```

## Comments

- 2026-10-20 开票：v6 把"去袋"从一句提醒升成**硬前提**（§4.2 + §6 L2b 两行）。
  现有 `baseline.BlobMetrics` 直接在原始连通域上算，宽度最宽行往往是袋宽——本票是那条口径的替代路径。

### 实现证据（2026-10-20）

- 落点：`measure/src/measure/debag.py`（`MASK_SOURCES` / `DebagedMask` / `RegionMetrics` / `debag` / `region_metrics` / `measure_region`）。
- 单测：`measure/tests/test_debag.py` **11 passed**。
  命令：`uv run --no-project --offline --with pytest --with numpy --with pillow pytest measure/tests/test_debag.py -q`
- 已钉住：原始掩码最宽行 = 袋宽 100、去袋后 = 簇宽 40；`removed_area_ratio` > 0.5；
  缺袋掩码 ⇒ 区域指标 `None` + `bag_mask_missing`；整簇落在袋内 ⇒ `no_region_after_debag`；
  多袋只取并集、**没有 per-bag 拆分**；`mask_source` 非法值直接 `ValueError`；形状不一致直接拒绝。
- review 修复：补 `valid_column_count` / `valid_column_ratio`（与票 13 规则 1 同一口径）、
  `visible_coverage_ratio`（需传 `roi_area_px`）、`envelope_region()` 显式 `fallback=envelope`；
  直调 `region_metrics()` 时 `removed_area_ratio` 保持 `None`，不伪造 0。
- 未做：真实模型掩码（等票 19）；`envelope` 的真实 H1 包络输入。
