# 20. P3-4 单锚点曲线：离线证据 → 锚点序列 + 序列子集验收

Status: implemented（2026-10-20，算法；10 条单测；序列子集验收等票 17）
归属：算法（曲线与聚合）+ 后端（只读消费，可选）
写域：`measure/**`（`measure/src/measure/curve.py`、`measure/tools/**`、`measure/tests/**`）＋本票
依据：`docs/patrol/growth-algo/spec.md` §7（曲线规则、分段、`panel_coverage`）、§4.7（逐级降级）、
§4.6（`rel_*` 与 `ref_px`）、§9.2（三类测试）、§9.4（`segmentation_status`）
依赖：15（`anchor_results.jsonl` / `bag_results.jsonl`）；曲线级验收另需 17 的**序列子集**
（6 锚点 × 连续 8 轮）；**不依赖** `measurements` 写库（票 05 不是本票输入）

## 1. 交付物

1. 新模块 `measure/curve.py`：把票 15 的锚点/袋级证据行聚成**单锚点序列**，纯函数、无 I/O。
2. `CurvePoint` 与 `Segment`：每个点带 `value`、`n_bags_valid`、`n_bags_total`、
   `panel_coverage`、`segmentation_status`、`status`、`reasons`；缺失点**留在序列里**，不删、不插值。
3. 分段：按 `(pipeline_version, model_version, roi_version, calibration_version, batch_no)`
   切段；切段处**不连点、不跨段算速率**（§7 的降级分段模式）。
4. 速率：只在**同段、相邻、两点都有值、`dt ≥ min_span_h`** 时算；
   跨过长缺测区间（中间有 `missing` 点或 `dt > max_gap_h`）一律不算。
5. `measure/tools/build_anchor_curve.py`：JSONL → 逐锚点 CSV（时间、值、覆盖率、段号、状态），
   可选输出无依赖 SVG（不引入 matplotlib）。
6. 序列子集验收脚本：三类测试（§9.2）在合成序列 + 真实序列子集上各跑一遍。

## 2. 口径（写死）

```python
anchor_series(rows, *, metric="h_anchor") -> list[CurvePoint]
segment(points) -> list[Segment]
rates(points, *, min_span_h=2.0, max_gap_h=12.0) -> list[RatePoint]
layer_median(points_by_anchor) -> list[LayerPoint]
```

| 规则 | 口径 |
| --- | --- |
| 锚点级取值 | `median` over 该帧 `cluster_height_usable=true` 的袋；与 §4.7/票 15 一致 |
| 缺测 | 该帧无有效袋 ⇒ `value=None`、`status="missing"`，点仍保留；**不补 0、不插值** |
| 覆盖率 | `panel_coverage = n_bags_valid / n_bags_total`；<0.8 ⇒ `panel_coverage_low` |
| `usable` vs `verified` | 曲线 v1 用 `usable`；`verified` 仍是抽样验收后的全局标记，**两词不混用** |
| 分段 | 版本四元组或批次变化 ⇒ 新段；`segmentation_status` 透传，不重算 |
| 速率 | 同段 + 相邻 + 都有值 + `dt` 在 `[min_span_h, max_gap_h]`；否则 `None` |
| 层内中位 | 每个时间桶只对**有有效点**的锚点取中位；没有点的锚点**不进分母** |
| 相邻锚点 | **不提供任何求和/去重函数**；数量类统计不经过本模块 |

## 3. 验收（缺一条不算过）

- [ ] 合成序列：3 个袋、2 个可用 ⇒ `value` = 两袋中位；`panel_coverage=2/3`
- [ ] 整帧缺失 ⇒ 序列里出现 `missing` 点（不是被跳过），相邻速率不算
- [ ] 版本四元组变化 ⇒ 两个 `Segment`，段间速率为 `None`；段号单调
- [ ] `dt < min_span_h` ⇒ 速率 `None`（不拿分钟级差分当增长）
- [ ] 中间隔一个 `missing` 点 ⇒ 跨该点不产速率（同段也不行）
- [ ] 层内中位：12 个锚点里 2 个无点 ⇒ 分母是 10，不是 12
- [ ] 只吃 `AnchorResult`/`anchor_results.jsonl` 的行；传入 `delta` 字段**不产生曲线**
      （字段级断言：`delta` 不在 `CurvePoint` 上）
- [ ] `usable` 点与 `verified` 点分开统计，断言不把 `usable` 冒充 `verified`
- [ ] 三类测试（§9.2）：静态重复不产假增长；人工对照方向一致；换包/缺图/版本升级不连续
- [ ] 全量 `measure/tests` 绿；不新增依赖；不改 DDL、不写毫米列

## 4. 不做什么

- **不做前端渲染**（四层总览是 P4-1；本票只到 CSV/SVG 证据）
- **不写 `measurements`**（逐袋/逐帧证据仍在 JSONL；DDL 另行评审）
- 不做事件分段（换包/采收事件源仍是 B4，只用版本 + 批次降级）
- 不做平滑、不强制单调、不跨段比较

## 5. 复跑

```bash
uv run --no-project --offline --with pytest --with numpy \
  pytest measure/tests/test_curve.py -q

uv run --no-project --offline --with numpy \
  python measure/tools/build_anchor_curve.py \
  --rows .scratch/profile-v6/anchor_results.jsonl --out .scratch/profile-v6/curves
```

## Comments

- 2026-10-20 开票：spec 的关键路径终点是 P3-4，但它一直没有票；
  票 15 产出 `anchor_results.jsonl` 之后，没有东西把它变成"一条曲线"。

### 实现证据（2026-10-20）

- 落点：`measure/src/measure/curve.py`（`CurvePoint` / `RatePoint` / `LayerPoint` /
  `anchor_series` / `rates` / `layer_median` / `assert_no_delta_in_curve`）；
  工具 `measure/tools/build_anchor_curve.py`。
- 单测：`measure/tests/test_curve.py` **10 passed**。
  命令：`uv run --no-project --offline --with pytest --with numpy --with pillow pytest measure/tests/test_curve.py -q`
- 工具冒烟：两行 JSONL（一个有效点 + 一个缺测点）→ CSV 两行，缺测行 `value` 为空、
  `status=missing`、`usable=false`，未补 0。
- 已钉住：缺测留点、输入排序、版本变化分段、`segment_changed` 不产速率、
  跨缺测点不产速率、`dt<min_span_h` 与 `dt>max_gap_h` 两档拒绝、层内中位分母只数有值锚点、
  `CurvePoint` 不携带 `delta`、`usable` 不被改名成 `verified`。
- 未做：真实序列子集（票 17）；SVG（留给 P4-1，不引入 matplotlib）。
