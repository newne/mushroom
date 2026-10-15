# 11. L1 对齐：平移优先的 ECC 配准（变换上限 + 残差门）

Status: implemented（2026-10-15，算法；10 条验收逐条核过；已合并 feat/env-baseline）
归属：算法
写域：`measure/**`（含 `measure/tests/**`、`measure/tools/**`）＋本票
依据：`docs/patrol/growth-algo/spec.md` §6「链路分解与模块接口」L1 行
（OpenCV ECC、**平移优先**、用**稳定背景**掩码（不用菌簇）；低分辨率估变换 → 映射 ROI → **在原图坐标量测**；
失败码 `registration_failed`/`view_changed`/`registration_unreliable`）
＋ §6.1 性能预算（**单帧 ≤3.75 s**、单轮 ≤180 s；「ECC 必须在低分辨率上估变换」）
依赖：OpenCV —— 用户 2026-10-14/15 已批准走 `uv` + 国内源
（`uv pip install --index-url https://mirrors.aliyun.com/pypi/simple opencv-python-headless`）。
**本票不改任何 `pyproject.toml` / `uv.lock`**（共享文件，W0-5 由 Chief 决定何时进依赖表）
⇒ 模块对 `cv2` **惰性导入**并给可操作的报错；测试用 `uv run --no-project --offline --with opencv-python-headless`。

## 1. 要交付的端到端行为

同一锚点的两帧（机位只有**小范围平移**）：

1. 在**静态背景掩码**上估一个**平移优先**的配准变换 `T`；
2. 用 `T` 把**第二帧（本帧原图）坐标映射回第一帧（参考帧）**；
3. 位移已知时，坐标还原误差 ≤ **1 px**；
4. 位移或残差**超限时明确拒绝**：返回失败状态 + `transform=None`，**不硬凑一个变换**、不用抛异常了事
   （下游按 spec §6 的失败码拒绝，落 `rejected` 行）。

## 2. 接口（写死；方向最容易搞反，先看这张表）

`measure/src/measure/align.py`

| 名字 | 方向 / 单位 | 说明 |
| --- | --- | --- |
| `estimate_translation(reference, moving, background_mask, *, config=None)` | → `Alignment` | `reference` = 第一帧/参考帧（模板参考帧）；`moving` = 第二帧/本帧原图；`background_mask` **必填**，与参考帧同形 |
| `Alignment.transform_ref_to_moving` | 第一帧 → 第二帧，2×3，**原图像素** | `p2 = M @ [x1, y1, 1]`；与 OpenCV 同向（`findTransformECC(reference, moving, …)` 返回的就是它，`warpAffine(moving, M, …, WARP_INVERSE_MAP)` 用它采样） |
| `Alignment.transform_moving_to_ref` | 第二帧 → 第一帧，2×3，**原图像素** | 本票的交付口径「把第二帧坐标映射回第一帧」 |
| `Alignment.to_reference(x, y)` / `to_reference_points(pts)` | 第二帧 → 第一帧 | 失败态调用 ⇒ `ValueError`（下游必须先看 `ok`） |
| `Alignment.to_moving(x, y)` / `to_moving_points(pts)` | 第一帧 → 第二帧 | 下游把模板几何/ROI 映射到本帧时用（P3-2/P3-3） |
| `Alignment.map_roi(box)` | 参考帧 ROI → 第二帧，**整数像素** | `box` = `(x0, y0, x1, y1)` 半开区间（可直接切片）；向下/向上取整向外扩、夹到第二帧边界 |
| `Alignment.status` | `ok` / 三个失败码 | 见下表 |
| `Alignment.score` | ECC 相关系数 ∈[-1,1] | **不作唯一放行条件**（P3-1 验收） |
| `Alignment.shift_px` / `estimate_scale` / `elapsed_s` / `reasons` | 诊断 | `shift_px` = 参考帧→第二帧的平移（原图像素，失败为 `None`）；`estimate_scale` = 原图/估计图倍率 |

**失败状态（`transform_*` 一起置 `None`）**

| status | 触发条件 | 下游含义 |
| --- | --- | --- |
| `ok` | 全部门过 | 变换可用 |
| `registration_failed` | ECC 不收敛/数值退化（空白帧、无梯度）、线性部分不是单位阵 | 这一帧没配上，别用 |
| `view_changed` | `|shift| > max_shift_px` | 机位动太多，**不能复用参考帧几何** |
| `registration_unreliable` | 收敛但 `score < min_score`，或静态背景覆盖率 < `min_mask_frac` | 几何对不上/没资格配准 |

**平移优先、不用透视**：v1 写死 `cv2.MOTION_TRANSLATION`，并要求返回矩阵的线性部分**恒为单位阵**
（不是就判 `registration_failed`）。将来要小角度旋转时**只换 motion 类型**，2×3 接口不变；
**不默认用透视变换**修视角变化。

**低分辨率估、原图出**：长边块平均（整数因子）降到 ≤ `max_estimate_edge_px` 估变换，
平移量 × 因子回到**原图像素**；下游量测一律在原图坐标（spec §6.2 同一条原则）。

**掩码口径**：`background_mask` 是**模板的静态背景掩码**（货架、管线；True=参与配准），
**参考帧坐标系**。菌簇一直在长 ⇒ 把菌簇算进来等于拿变化的东西当基准，**明令禁止**。
掩码为空/过小 ⇒ `registration_unreliable`。

## 3. 阈值初值（与 §4.5 同一套冻结纪律：开发集后冻结，冻结后不回改）

| 键 | 初值 | 含义 |
| --- | --- | --- |
| `max_estimate_edge_px` | 720 | 估变换用的长边上限（低分辨率预算） |
| `ecc_iterations` / `ecc_eps` | 100 / 1e-6 | ECC 终止条件 |
| `ecc_gauss_filter` | 5 | ECC 梯度平滑核 |
| `max_shift_px` | 64 | 平移上限（原图像素）；超过 ⇒ `view_changed` |
| `min_score` | 0.85 | 残差门（ECC 相关系数下界） |
| `min_mask_frac` | 0.05 | 静态背景覆盖率下界 |

## 4. 验收（缺一条不算过）

- [x] 合成图已知平移（整数 N px）⇒ `ok`，`to_reference` 还原误差 **≤1 px**，符号方向正确
      （`test_known_translation_is_recovered_and_maps_back_within_one_pixel`；亚像素 `13.4/-7.3` 亦 ≤1 px）
- [x] 变换**位移上限**：超过 `max_shift_px` ⇒ `view_changed` + `transform=None`
      （`test_shift_beyond_limit_is_rejected_as_view_changed`；同对帧放宽上限即 `ok` ⇒ 拒绝来自门，不是配准本身不行）
- [x] **残差门**：两帧内容对不上 ⇒ `registration_unreliable`/`registration_failed` + `transform=None`
      （`test_unrelated_frames_never_return_a_transform`、`test_residual_gate_rejects_when_score_below_threshold`）
- [x] 退化输入（空白/无梯度）⇒ `registration_failed`（**不抛异常**）
      （`test_blank_frames_are_registration_failed_not_an_exception`；OpenCV 这里抛 `-7 NaN`，已捕获成状态）
- [x] 平移优先：返回矩阵线性部分为单位阵（无旋转/缩放/透视）
      （`test_transform_is_pure_translation_no_rotation_scale_or_perspective`；motion 写死 `MOTION_TRANSLATION`）
- [x] 掩码纪律：掩码外的变化内容（模拟**在长的菌簇**）**不影响**位移估计
      （`test_growing_cluster_outside_mask_does_not_move_the_estimate`：加/不加菌簇，位移差 ≤1 px）
- [x] 低分辨率估、原图出：2880×1616 帧 `estimate_scale=4`，`shift_px`/`map_roi` 都是**原图像素**
      （`test_low_resolution_estimate_maps_back_to_original_pixels`、`test_map_roi_maps_reference_box_into_moving_frame_in_original_pixels`）
- [x] 单帧总耗时 **≤3.75 s**（2880×1616 合成帧；对齐自身 **中位 0.105 s**，见 §Comments）
- [x] `measure/tests` 全绿（含既有用例，无回归）：**163 passed**
- [x] 不改 `pyproject.toml`/`uv.lock`（本票 diff 只有 `measure/**` 与本票）；不读生产帧做单测
      （`measure.align` 对 cv2 惰性导入：只装 numpy 时 `import measure.align` 仍可，`cv2 loaded = False`）

## 5. 不做什么

- **不做旋转/相似/透视**（v1 平移优先；先验证平移，再谈小角度旋转）
- **不拿菌簇配准**（像素级趋势口径不许把生长当位移）
- **不接线** L3/L4/L5（本票只出 `T` 与坐标映射；`map_roi` 是给它们用的口子）
- **不动** `analysis/`、`src/`、`web/`、`docker/`、`deploy/`（别的写者）
- **不读生产帧**、不连 Postgres/TDengine/MinIO（票 01 的 P0 红线）
- 不把阈值写进生产配置或 DDL（DDL 另行评审）

## 6. 复跑

```bash
# 单测（一次性离线环境；PYTHONPATH 指向本 worktree 的 measure/src）
uv run --no-project --offline --with pytest --with numpy --with pillow --with opencv-python-headless \
  pytest measure/tests/test_align.py -q
# 单帧预算基准（合成帧，不读生产帧）
uv run --no-project --offline --with numpy --with opencv-python-headless \
  python measure/tools/bench_alignment.py
```

## Comments

- 2026-10-15 开票（实现者）：票先于实现落文件；阈值初值来自 spec §6.1 预算与 §4.5 初值表纪律。
### 实现证据（2026-10-15，实现者；worktree `algo-ecc`）

**跑的命令（真实输出摘录）**

```bash
# 1) 本票单测
uv run --no-project --offline --with pytest --with numpy --with pillow --with opencv-python-headless \
  pytest measure/tests/test_align.py -q          # → 15 passed in 8.02s

# 2) 回归（PYTHONPATH 加 analysis/src，见下方"环境坑"）
#    measure/src;analysis/src → 163 passed in 88.11s（0 failed）
#    只给 measure/src → 161 passed, 2 failed（test_record.py 的 import analysis 失败）

# 3) 单帧预算基准（合成帧，2880×1616，5 位移 × 3 次）
uv run --no-project --offline --with numpy --with opencv-python-headless \
  python measure/tools/bench_alignment.py --runs 3
```

对齐自身耗时：**min 0.0843 s｜median 0.1052 s｜p95 0.2635 s｜max 0.5192 s**（门 3.75 s ⇒ 中位余量 **≈36×**）；
定向 **15/15** 次 `ok` 且位移误差 ≤1 px（估到 `9.008/5.017`、`-17.005/10.961`、`22.999/-10.999`、`-29.007/-18.999`）。

**两条实测结论（写进阈值冻结前的话）**

1. **捕获范围来自低频结构，不是来自像素噪声**：1 px 白噪声底 + 全分辨率时，13 px 平移**直接不收敛**
   （ECC `-7` 报错）；换成货架式低频结构后，小帧全分辨率可锁到 ~40 px，**4× 降采样后 120 px 仍锁得住、
   误差 ~0.1 px**。⇒ spec §6.1「ECC 必须在低分辨率上估变换」不只省时间，也是**位移上限可达**的前提。
   **合成测试图必须保持真机帧的低频性格**，否则测的是假难度（test_align 的 `synth_background` 已按此写）。
2. **score 的判别力有限，不能当唯一放行条件**：同一货架布局、只换噪声/斑驳的两帧，ECC 给 **cc≈0.8515**
   —— 就在 `min_score=0.85` 门线上（本票的 ok 判定在这两帧上其实是**对的**：同一场景本来就该配上）。
   反过来，**周期结构**下"错锁但分高"是可能的（实测 60 px 超捕获范围时出现过 cc≈0.99 的错值）。
   ⇒ 将来若在开发集上看到错锁，**另开票**加一道独立残差门（例如掩码内"应用 T 后是否比零位移更匹配"），
   本票不臆造第二个阈值。`min_score` 属 §4.5 初值，冻结前按开发集核。

**限制（本票不做的，别误读）**

- 只出 `T` 与坐标映射，**没接** L3/L4/L5；`map_roi` 与 `shift_px` 是给它们用的口子。
- `max_shift_px=64` 是**原图像素**上限；超过即 `view_changed`。真机 2880×1616 在 4× 估计下捕获范围
  远大于此，但**没在真实帧上量过**——没有生产帧可用（票 01 的 P0 红线），必须在 P3-2 用开发集核。
- 依赖 `opencv-python-headless` **没进**任何 `pyproject.toml`（共享文件，W0-5 归 Chief）；调用前需按 §2 的
  一行命令装好，否则 `ImportError` 会带这条命令。

**环境坑（非本票引入）**：只把 `measure/src` 放进 `PYTHONPATH` 时，`measure/tests/test_record.py` 的
`import analysis.db` / `import analysis.evidence` 会 `ModuleNotFoundError`（`analysis` 是 `analysis/src` 下的
命名空间包）。加上 `analysis/src` 后 **163/163 全绿**。与本次改动无关（本票 diff 不含 `analysis/**` 与 `test_record.py`）。

