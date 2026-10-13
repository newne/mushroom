# 15 — 帧质量判定统一到 `measure/quality.py`（schema 与阈值对齐）

**Spec:** `../../patrol/fmc4030-camera-scan/spec.md` §5.2；`env-analysis/issues/11-measurements-contract.md` §3

**归属：** `measure/**` = 测量师（**唯一尺度**）；`patrol/src/patrol/framing.py` = step code
（改成**调用**，不许自带名字与常数）。

**Blocked by:** 无——`measure/quality.py` 侧已实现并可调用（提交见 `_commit_msg3` 那条）。
**Status:** ready-for-agent

## 背景（Chief 2026-10-13 裁定 ①）

帧级质量判定今天有**两套**：

| 位置 | 标记名 | 阈值 | 连接符 |
| --- | --- | --- | --- |
| `measure/src/measure/quality.py` | `blurry` / `overexposed` / `no_light` | 标定值（见下） | `+` |
| `patrol/src/patrol/framing.py::judge_frame` | `underexposed` / `overexposed` | `MIN_P99=150`、`LEVEL=250`、`MAX=0.05` | `,` |

两套语义重叠但不相等 ⇒ **同一列里混两种语言**，而 `quality` 串原样落库（巡检手不解析），
分叉会完整传进数据。Chief 裁定：**统一到 `measure/quality.py`，`framing.py` 改成调用。**

## 要 step code 做的（改动很小）

`patrol/src/patrol/framing.py` 里：

1. **删掉本地的阈值常数**（`MIN_P99` / `OVEREXPOSED_LEVEL` / `MAX_OVEREXPOSED`）与
   自造的标记名（`FLAG_UNDEREXPOSED`）；
2. 改成调用唯一入口：

   ```python
   from measure.quality import assess_exposure, frame_quality

   exp = assess_exposure(gray)          # 曝光轴：欠曝端 + 过曝端
   exp.flags            # ("no_light",) / ("overexposed",) / ()  —— 唯一名字
   exp.p99              # 0–1，取景判断可以直接读这个量
   exp.overexposed_fraction
   exp.underexposed / exp.overexposed    # 布尔，供"多等一会儿灯"这类取景逻辑
   ```

   `judge_frame` 需要的 `FrameQuality` 里的 `mean/std/p99/over` 统计量，继续自己算没问题
   （那是**统计量**不是判定），但**判定与标记必须走上面的函数**；
3. 若取景需要"比测量门更早/更宽"的**表现层**判断，请用 `exp.p99` 自己做展示判断，
   **不要**再引入新的**质量标记名**（质量标记只有一套）。

## 为什么不能沿用 `MIN_P99=150`

218 张线上真机帧实测：**正常帧的 `p99` 最低 184**（p50=225.5，灰阶口径），
两张真暗帧 p99 = 2–3。所以 `p99 < 150` 会**误杀一部分正常帧**；
统一后的欠曝阈值是 **`p99 < 0.12`（≈ 灰阶 31）**——落在 2–3 与 184 之间的巨大空隙里。

## 统一后的阈值（`measure/quality.py` 的 DEFAULT_*）

| 判据 | 值 | 依据 |
| --- | --- | --- |
| 清晰度绝对下限 | `lapvar < 45.0`（**0–255 灰度刻度**） | 218 张 ok 帧 min=48.0、p05=52.6，误杀 0/218 |
| 欠曝 `no_light` | `p99 < 0.12` | ok 帧 p99 min=0.722；真暗帧 0.008/0.012 |
| 过曝 `overexposed` | 占比 > 0.05 | ⚠️ 样本期内全为 0.00，**该阈值尚未标定** |

**刻度提醒**：`lapvar` 必须在 **0–255** 灰度上算（`np.asarray(img).mean(axis=2)`），
在 0–1 上算会小 65025 倍，绝对下限失效。

**限制（Chief 要求写明）**：218 帧是**单批次、单生长阶段**样本 ⇒ 阈值可作初值，
**待跨阶段样本复核**；换角/换灯/相机参数变更后按
`measure/tools/calibrate_quality_thresholds.py` 重算。

## 验收

- [ ] `patrol/framing.py` 里**不再出现**任何质量标记名与阈值常数（`grep` 可查）
- [ ] `judge_frame` 的判定结果与 `frame_quality` 在同一帧上一致（含 `no_light` 命名）
- [ ] `patrol/tests/test_framing.py` 覆盖"欠曝帧 → `no_light`"、"过曝帧 → `overexposed`"
- [ ] `deploy/src/deploy/measure_wire.py` 的 `--measure-blur-threshold` 默认值由 **100.0 改 45.0**
      （100 是**已被标定否掉**的旧值：实测拒 78.4% 正常帧；现在默认关，一开就会踩）

## Comments

- 2026-10-13 写票（测量师）。Chief 裁定 ①：统一到 `measure/quality.py`；
  测量师侧已实现 `assess_exposure()`（唯一曝光判定入口，带出 p99/过曝占比供取景只读使用），
  `frame_quality()` 的曝光两端改为调用它，历史 `underexposed` 只在**读**时兼容、产出只写
  `no_light`；measure 93 passed。
