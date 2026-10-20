# 13. P3-3a 每帧主指标：`H_profile` / `H_p50` / `H_p90` 同生同灭

Status: implemented（2026-10-20，算法；12 条单测；误差门槛待票 18 冻结）
归属：算法
写域：`measure/**`（`measure/src/measure/**`、`measure/tests/**`）＋本票
依据：`docs/patrol/growth-algo/spec.md` §4.1（三式与 `V` 三规则）、§4.4（逐指标独立有效性）、
§4.5（初值表）、§4.7（Level 1）、§9.1、§9.4
依赖：12（`b(x)` + `baseline_valid`）；真实帧误差验收依赖 17 的标注与 18 的门槛

## 1. 交付物

1. 新模块 `measure/cluster_profile.py`：由**去袋/未去袋的簇掩码** + `b(x)` 算出逐列高度与三个主指标。
2. `V` 的三条合成规则（§4.1）与逐列证据（`t(x)`、`h(x)`、`V`、`interpolated`）一起返回，
   供 §9.5 逐项算误差——**不许只返回三个标量**。
3. 一个**统一谓词** `height_valid`，三个指标只认它（见 §2）。`baseline_valid=false` 时三者**同时 `None`**。
4. 字段名归一：`H_profile` ≡ 票 01 的 `mean_profile_height_px`；`H_p90` **吸收**原 `robust_top_height_px`，
   两者不得同时出现在任何输出里。

## 2. 接口与口径（写死）

```python
profile_heights(cluster_mask, baseline, *, association_valid=True, config=None) -> HeightProfile
```

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `h_profile` | `float \| None` | `mean_{x∈V}[b(x)−t(x)]`，原图 px |
| `h_p50` | `float \| None` | `median_{x∈V}[b(x)−t(x)]` |
| `h_p90` | `float \| None` | `quantile_{x∈V}[b(x)−t(x)], 0.9`（numpy `linear`，写进 docstring） |
| `raw_top_height_px` | `float \| None` | 辅助：`max(h)` 先按 0.995 截尾；**不是主指标** |
| `column_heights` | `float[]`，`NaN` 出 V | 逐列 `b−t`；用于误差分解与画图 |
| `top_envelope` | `float[]`，`NaN` 出 V | 逐列 `t(x)`，原图 px |
| `valid_columns` | `bool[]` | `V` 的最终集合（已含三条规则） |
| `valid_column_count` / `valid_column_ratio` | `int` / `float` | 比值分母 = 簇掩码支撑列数（与 §4.2 同一套列有效性） |
| `status` | `ok` / `insufficient_contour` | 后者由 `valid_column_ratio < min_valid_column_ratio` 触发 |
| `reasons` | `tuple[str, ...]` | 逐列规则被哪一条拒掉，要能分别计数 |

**`V` 三条规则（§4.1，缺一不可）**

1. 掩码先 3×3 开运算；列连续宽度 ≥ `min_col_run_px=3`；
2. 该列前景**最高连续段**上边界可取；段间隙 > `max_gap_px=5` ⇒ 该列不可靠；
3. 该列必须存在**从 `t(x)` 向下连通到 `b(x)`** 的前景竖直路径（容差 `reach_tol_px=8`）。

**统一谓词（本票新增，需回写 §4.1/§4.4）**

```
height_valid = baseline_valid
             ∧ association_valid
             ∧ measurement_valid
             ∧ valid_column_ratio ≥ min_valid_column_ratio
```

`height_valid=false` ⇒ `h_profile=h_p50=h_p90=None`，**三者同生同灭**，且 `cluster_height_usable=false`。
理由：§4.1 只写了 `baseline_valid`，§4.4 又说 `cluster_height_usable` 由五条合成——两处必须收敛成**一个**谓词，
否则实现者会各自挑一条。

**插值列是否进 `V`（spec 未定 ⇒ 本票给默认，待裁）**

`baseline.interpolated=true` 的列**默认不进 `V`**（保守），同时用同一批列再算一档 `h_profile_with_interp`
只作敏感性证据，**不进主输出**。两档差异写进 §9.5 的报告，冻结时由 Chief 裁一次。

## 3. 验收（缺一条不算过）

- [ ] 合成：`b≡100`、`t≡60`、50 列全连通 ⇒ `h_profile=h_p50=h_p90=40`（±0.5 px）
- [ ] 合成：倾斜基线 + 斜顶 ⇒ 三值与手算逐列均值/中位/0.9 分位一致（容差 0.5 px）
- [ ] **半空簇必红**：簇掩码悬空（与 `b(x)` 间隙 12 px > `reach_tol_px`）⇒ 该列不进 `V`；
      把规则 3 关掉后同一用例**必须失败**（把"改坏了会红"跑出来，命令记进 Comments）
- [ ] `baseline_valid=false` ⇒ 三个主指标**全 None**、`cluster_height_usable=false`，
      且**不出现 0**（断言里显式 `is None`）
- [ ] `valid_column_ratio=0.29` ⇒ `insufficient_contour` + 三者 None；`=0.30` ⇒ 出值（边界值测试）
- [ ] 无效列**不得**被 `np.interp` 跨洞补进统计（与探索链路同一个 bug，票 11 同款断言）
- [ ] `H_p90` 与"簇掩码外接框高度"在同一合成图上**必须不同**（构造成 40 vs 55），
      并断言输出里不存在 `robust_top_height_px` 这个并列字段
- [ ] 同图重复两次 ⇒ 输出逐位一致；`quantile` 的 `method="linear"` 写进 docstring 与测试名
- [ ] `measure/tests` 全绿；不新增依赖；不改 DDL、不写毫米列

## 4. 不做什么

- **不算区域指标**（宽度/面积/等效径，票 14）；本票只吃掩码，不负责去袋
- **不做基线映射**（票 12）；测试用构造的 `BaselineProfile`
- **不做曲线/聚合**（Level 0 与锚点级聚合在票 15；曲线在 P3-4）
- 不做 mm、不做帽径、不接模型

## 5. 复跑

```bash
uv run --no-project --offline --with pytest --with numpy --with pillow \
  pytest measure/tests/test_cluster_profile.py -q
```

## Comments

- 2026-10-20 开票：v6 把 `H_p50`/`H_p90` 提到主指标，且要求"同一帧、同一 `b(x)`、同一批 `V`"。
  本票是按 §4.1 的唯一落点；`height_valid` 与"插值列是否进 V"两处 spec 没写死，已在 §2 给出默认并标注待裁。

### 实现证据（2026-10-20）

- 落点：`measure/src/measure/cluster_profile.py`（`ProfileConfig` / `HeightProfile` / `profile_heights`）。
- 单测：`measure/tests/test_cluster_profile.py` **12 passed**。
  命令：`uv run --no-project --offline --with pytest --with numpy --with pillow pytest measure/tests/test_cluster_profile.py -q`
- 已钉住：平顶三值相等；斜基线时 `H_p90` 与外接框高度不同；半空簇按 `top_gap` 被拒；
  `max_gap_px` 与 `reach_tol_px` 各自独立可测（间隙 6 / 两档容差）；`valid_column_ratio` 0.29/0.30 边界；
  插值基线列不进 `V`、只进 `h_profile_with_interp`；`baseline_invalid` 三值全 `None`；
  缺失列不被 `np.interp` 补进统计；同输入逐位一致。
- review 修复：`supported_columns` 改为**簇掩码支撑列**（不再受基线槽位宽窄影响，
  槽位外的簇列也进分母）；理由码统一为 `association_ambiguous`。
- 实现口径：`height_valid` 用 §2 的统一谓词；插值列**默认不进 `V`**（待裁），
  `quantile(method="linear")` 已写进 docstring 与测试名。
