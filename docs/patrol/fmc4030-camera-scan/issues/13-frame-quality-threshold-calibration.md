# 13 — 帧质量门阈值标定：模糊/过曝/近黑（用真实历史帧统计，不拍脑袋）

**Spec:** ../spec.md §5.2（质量门控）；`env-analysis/issues/11-measurements-contract.md`

**归属：** 测量师（`measure/quality.py` 是**唯一尺度**——Chief 2026-10-13 裁定：
step code 的"帧级可用帧判定"必须对齐这一份，不许造第二套；它只能读，要改写票据）。

**Blocked by:** 真实历史帧样本（找发版官只读抽取，见下"要什么样本"）。
**不许**自己连生产 10.77.77.39。

**Status:** ready-for-agent（等样本）

## 为什么必须标定

真机帧 `data/S306-candidate-Y2058.833-Z117.5-20261008.jpg`（1343×754）实测
`laplacian_variance = 70.8`，而 `measure/quality.py` 的默认模糊阈值是 **100**
⇒ **现役默认门会把真机帧判为"模糊不可用"**。平视单档 + 矮菇（用户口径：第 13 天、
菌杆矮且无帽）本身就是低纹理画面，拍脑袋定的常数 100 在这里大概率过严——
但也不能顺手调小：**阈值决定"哪些帧进测量"，调错等于给整条生长曲线换口径。**

## 要什么样本（找发版官）

- **只读**抽取、**脱敏**（不带库房凭据/URL），文件名保留 `station_id/angle_profile/ts`；
- 覆盖：**跨轮次**（至少 3 轮，间隔 ≥3 小时）、**跨天**、**跨生长阶段**（当前阶段 +
  之后长高的阶段各一批）；
- 每类各取 ≥20 帧，且必须包含**已知的坏帧**作为对照：
  - 加湿保持期内/刚结束的过曝帧（CONTEXT.md:22 明说"水汽重、过曝、对比度低"）
  - 灯未亮/近黑帧
  - 相机未锁焦/抖动帧（如果现场有过）
- 交付形态：一个目录 + 一份 `labels.csv`（`file,label`，label ∈
  `ok / blurry / overexposed / dark / unknown`，人工标注优先，没有就至少给"拍坏的"清单）。

## 要产出的东西

1. **分布统计**：`laplacian_variance`、`overexposed_fraction`、平均亮度/近黑占比，
   按 label 分组给分位数（p10/p50/p90），把"ok 与 bad 的分界"画出来；
2. **两个候选阈值** + 理由：
   - 模糊阈值：以 **ok 的 p10**（或更保守的 ok 最小值）为下限参考，取"尽量不误杀 ok、
     又能拦住已知坏帧"的那个值；
   - 过曝比例阈值：同理（现默认 0.05）；
   - 近黑：是否新增一个判据（现役 `quality.py` **没有**近黑判据，只有模糊+过曝）。
3. **处置口径**：明确每个判据触发是 **"不可用"（不产行）** 还是 **"降权"
   （产行 + quality 标记）**，并写进 `measure/quality.py` 的 docstring 与票 11 §3。
   当前实现：模糊/过曝 ⇒ 不产行（`frame_quality` 返回 False）；
   若某判据改为"降权"，要同步加 quality 值并通知环境官（枚举变更）。
4. **回归**：把 `data/S306-...jpg` 作为已知帧进单测（`lapvar` 具体值 + 门判定结论），
   保证阈值以后改动时"为什么改"是可复现的。

## 与现有实现的关系

- `measure/quality.py::frame_quality(gray, blur_threshold=100.0, max_overexposed=0.05)`
  是唯一入口；标定后**改默认值**，不改接口；
- `measure/production.py::rows_from_frames(quality_gate=…)` 允许注入别的门（测试用），
  **生产必须用同一份默认门**，不许在 `deploy` 侧另写判据（Chief 裁定）。

## Comments

- 2026-10-13 写票（测量师，按 Chief 裁定）。起因：真机帧实测 `lapvar=70.8 < 100`，
  默认门会拒真机帧；证据与行样例见 `env-analysis/issues/11-measurements-contract.md`
  Comments。**不擅自改默认值**：改了就等于给生长曲线换口径，必须有分布统计支撑。
