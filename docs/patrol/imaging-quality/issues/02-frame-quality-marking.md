# 02 把"可用帧"标记落到图像索引，并补一条同站位相对判据

- 提出：**step code**（外部协作者）　执行：**巡检手**（仓库侧）　状态：ready-for-agent
- 立项：2026-10-13　来源：`01-camera-imaging-quality.md` 第 2/4 项

## 为什么要做

`patrol.framing.judge_frame` 已经能判"这一帧能不能用"（阈值与人群证据见该函数
docstring），但**判定结果现在没有落点**——不落点就只有两个结局：静默丢弃（票里明令禁止，
坏帧会静默进入积累数据），或者永远只活在日志里（下游看不见）。

## 交付

1. **加列**：`analysis` 的 `images` 表加 `frame_quality TEXT`（与 `imaging_profile`
   同一套口径：**空 = 那时没判过，不编值**；老行 NULL）。DDL 在
   `analysis/src/analysis/db.py:73-97`，`imaging_profile` 是怎么加的，这个就怎么加。
2. **逐帧写**：`patrol.orchestrator` 采图元数据里带上标记（`imaging_profile` 旁边，
   `orchestrator.py:156-158` 那块），`deploy.manual_exec` 的手动抓拍同样带上
   （`manual_exec.py:424` 已有先例）。判定函数：
   `patrol.framing.judge_frame(gray)`，字节版用 `frame_quality_gate(imread)` 包。
   标记串是**逗号分隔**（与 `measure.quality.frame_quality` 同形）；要写进
   `measurements.quality` 枚举时按 `measure.contract.JOINER`（`+`）接，并先找测量师
   登记枚举值（见票 03 的联系方式）。
3. **相对判据（雾的补救）**：单帧门控抓不到加湿雾——人群证据：正常帧 σ 22.5–46.1、
   雾帧 σ 31.2–37.0，**完全重叠**，任何绝对阈值都会误杀正常帧。所以雾要**相对判**：
   按 `(station_id, angle_profile)` 取最近 N 帧（建议 N=6，跨 3 天）的 mean/std/p99 中位数，
   当 |Δmean| > 20% 或 std 低于基线 25% 时，给该帧打 `low_contrast`。
   基线数据来自 `judge_frame` 已经带回的统计指纹（`FrameQuality.mean/std/p99`），
   **不要二次解码**。
4. **与 hold 的分工写进文档**：hold 是预防（加湿段不拍），相对判据是补救（没设 hold
   又撞上）。两者都在 `CONTEXT.md` 的「加湿保持」词条里说清楚。

## 验收

- `images.frame_quality` 加列 + 写入 + 老行 NULL（新增用例，对齐 `imaging_profile` 那三条）；
- 一轮巡检后，`SELECT frame_quality, count(*) FROM images WHERE ts > …` 能看到标记分布；
- 合成一轮：人为把某帧压黑/打亮，确认标记进得去、下游 `is_usable` 认它；
- `patrol-workspace` 全量 pytest 绿。

## 不做

- 不做静默丢弃：判坏的帧**照旧入库**，只多一个标记（票 01 第 2 项的硬要求）；
- 不改阈值——阈值归 `patrol.framing.judge_frame`（step code 领地），要改走票据。
