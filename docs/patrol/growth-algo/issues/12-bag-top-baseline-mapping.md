# 12. L4 袋顶基线：模板折线经 `T` 映射 + 两级可用性

Status: implemented（2026-10-20，算法；11 条单测；真实帧验收仍等票 08 的模板）
归属：算法
写域：`measure/**`（`measure/src/measure/**`、`measure/tests/**`）＋本票
依据：`docs/patrol/growth-algo/spec.md` §4.1（`b(x)` 定义）、§4.3（两级可用性）、§4.5（初值表）、
§5.1-2（`top_line_ref` 允许 `baseline_valid=false`）、§6 L4 行
依赖：11（`measure.align`，已 implemented）；真实帧验收依赖 08（模板 v1）；
**本票先用合成模板 + 合成帧开工**，不等 08

## 1. 交付物

1. 新模块 `measure/bag_top.py`：把模板里某槽位的**袋顶折线**经票 11 的 `Alignment` 映射到本帧原图坐标，
   产出逐列基线 `b(x)` 与 `baseline_valid`。
2. 两级可用性的判定与理由码（§4.3 五条），**不可用时不猜**（`b=None`），可用的不可见列由折线插值。
3. 与票 11 的方向钉子一致：模板在**参考帧**坐标，映射用 `Alignment.to_moving_points()`；
   禁止自己重写矩阵方向。

## 2. 接口与口径（写死）

```python
map_bag_top(top_line_ref, alignment, *, slot_id, columns, config=None) -> BaselineProfile
evaluate_baseline(profile, *, bag_mask=None, front_roi=None, config=None) -> BaselineProfile
```

| 字段 | 单位 / 类型 | 说明 |
| --- | --- | --- |
| `b` | 原图 px，`float32`，长 = 槽位列数 | 每列袋顶 y（向下为正）；区间外与无效列为 `NaN` |
| `valid_columns` | `bool[]` | 折线真正可见的列（**不含插值列**） |
| `interpolated` | `bool[]` | 由折线插值补出的列；与 `valid_columns` 互斥 |
| `interpolated_ratio` | `float` | 插值列 / 槽位列数；进证据行，供 §9.5 判"基线是否被自己挡死" |
| `baseline_valid` | `bool` | 五条全过才为真（见下表） |
| `reasons` | `tuple[str, ...]` | 失败原因，逐条可读，不合并成一个布尔 |
| `shift_px` | `(dx, dy)` 原图 px | 本帧槽位相对模板槽位的袋口偏移（**不是**整帧机位位移） |
| `slot_id` / `roi_version` | `str` | 归属与版本，必须随结果一起出 |

**两级可用性（照 §4.3，不许加戏）**

| 级别 | 条件 | 处置 |
| --- | --- | --- |
| 可用 | 对齐接受 ∧ 槽位匹配 ∧ 袋口偏移 ≤ `bag_shift_max_px=40` ∧ 可见列数 ≥ `min_baseline_columns=8` ∧ 覆盖槽宽 ≥ `min_baseline_coverage=0.2` | 出 `b(x)`；不可见列**在槽位区间内**线性插值，记 `interpolated` |
| 不可用 | 上列任一不满足 | `baseline_valid=false`、`b=None`；下游 `H_profile`/`H_p50`/`H_p90` 全 NULL |

**三条容易搞错的边界**

1. **`bag_shift_max_px`（40）≠ 票 11 的 `max_shift_px`（64）**：前者是"本帧袋口相对模板槽位"的偏移，
   后者是"整帧机位平移"的上限。两者各自独立，**不许用一个代替另一个**。
2. **菌簇自遮挡不算 `occluded`**（§1.4-G2）：长势越好越会挡住袋口，这是预期内自遮挡；
   只有人手/管线/水汽这类**外部**遮挡才记 `occluded`，也才可能触发不可用。
3. **插值只在槽位区间内做**，两端不外推；区间外一律 `NaN`，下游不得把 `NaN` 当 0。

**理由码**（`reasons` 用，与 §6 L4 的失败码同一套词）

`registration_failed` / `view_changed` / `registration_unreliable`（透传票 11）/
`slot_missing` / `bag_shift_exceeded` / `too_few_columns` / `coverage_low` ⇒ 汇总为 `baseline_invalid`

## 3. 验收（缺一条不算过）

- [ ] 合成帧已知平移（±整数 px，含 y 方向）：`b(x)` 还原误差 **≤1 px**，符号方向正确
      （复用 `test_align` 的合成背景，不另造一套）
- [ ] `max_shift_px` 放开、`bag_shift_max_px` 收紧到 1 px：人为把槽位错 5 px ⇒
      `baseline_invalid` + `bag_shift_exceeded`，**且理由里不出现 `view_changed`**（证明两个门没混）
- [ ] 槽位 `slot_id` 对不上 ⇒ `slot_missing` + `b=None`（**不跨槽借折线**）
- [ ] 边界值：可见列 **=8** 且覆盖 **=0.2** ⇒ 可用；各少 1 ⇒ 不可用（边界不许用 `>` 与 `>=` 混写）
- [ ] 菌簇盖住袋顶中部（自遮挡）⇒ **仍 `baseline_valid=true`**，被盖列进 `interpolated`，
      `interpolated_ratio` > 0
- [ ] 外部遮挡（合成一块人手）覆盖到可见列 <8 ⇒ 不可用
- [ ] 插值不外推：槽位外 `b` 必须全 `NaN`；用 `np.interp` 时必须显式给 `left/right=np.nan`
- [ ] 同一输入两次调用结果逐位一致（无随机性）
- [ ] `measure/tests` 全绿、无回归；不新增第三方依赖（numpy 够用）
- [ ] 不改 DDL、不写毫米列、不读生产帧

## 4. 不做什么

- **不算 `H_profile`/`H_p50`/`H_p90`**（票 13）
- **不生成模板、不做半自动引导**（票 08）
- **不接模型**；`bag_mask` 只是可选输入，用于判"外部遮挡 vs 自遮挡"
- 不做透视/旋转/缩放；对齐一律走票 11 的平移优先实现

## 5. 复跑

```bash
# 单测（一次性离线环境；与票 11 同一条命令形状）
uv run --no-project --offline --with pytest --with numpy --with pillow --with opencv-python-headless \
  pytest measure/tests/test_bag_top.py -q
```

## Comments

- 2026-10-20 开票：v6 把 `H_profile` 定为每帧主指标，而它的**唯一前置**是 `b(x)`；
  spec §10.2 的 P3-2 一直没有票，本票补上。先用合成模板实现，等 08 的 48 份模板 v1 再做真实帧验收。

### 实现证据（2026-10-20）

- 落点：`measure/src/measure/bag_top.py`（`BaselineConfig` / `BaselineProfile` / `map_bag_top` / `evaluate_baseline`）。
- 单测：`measure/tests/test_bag_top.py` **11 passed**。
  命令：`uv run --no-project --offline --with pytest --with numpy --with pillow pytest measure/tests/test_bag_top.py -q`
- 已钉住：映射方向（+7/−5 平移）、失败对齐透传、`slot_missing`、`bag_shift` 与票 11 的
  `max_shift_px` 互不替代、自遮挡不参与（簇掩码不是本模块输入）、可见 8 列/覆盖 0.2 的边界、
  槽位外不外推、同输入逐位一致。
- 未做：真实帧（等票 08 的模板 v1）；真实袋口偏移的分布与 `bag_shift_max_px=40` 的标定。
