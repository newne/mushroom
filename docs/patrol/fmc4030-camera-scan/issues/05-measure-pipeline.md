# 05 — 测量管线：照片 → 每框 mm 级参数

**Spec:** ../spec.md §5.1、§5.2

**What to build:** 每站位棋盘格标定（mm/px、畸变系数），检出/分割模型输出每朵菇的柄轴向与盖轮廓，标定换算 mm 后按框聚合，写入 measurements 表（均值/P10/P90/样本数/质量门控）。模型训练可提前用旧项目数据准备，验收必须用真机巡检图。端到端行为：对一轮巡检照片跑管线，得到每个框的定量生长参数。

**Blocked by:** 04 — 全库站位示教 + 一轮完整巡检（M1）

**Status:** ready-for-agent

- [ ] 各站位标定文件可用，现场验证标定误差 ≤2 mm
- [ ] 顶拍档输出菌盖直径、45° 斜拍档输出菇体长度，含角度补偿
- [ ] 每框聚合指标（均值/P10/P90/n）写入 measurements
- [ ] 模糊/过曝/遮挡帧被 quality_flags 门控，不入统计
- [ ] 与人工抽测对照：抽样框测量误差 ≤2 mm

## Comments

- 2026-08-30 代码完成（commit `54e80a4`）：`measure/calib.py`（Kasa 圆拟合、mm/px 换算、45° 斜拍长度补偿）、`measure/quality.py`（拉普拉斯方差模糊检测、过曝占比、frame_quality 门控）、`measure/pipeline.py`（Detection/Detector 协议 + DummyDetector、按框聚合 mean/P10/P90/quality_flags），16 项单测（合成圆误差 <1e-6）。
- **待现场**：各站位棋盘格标定文件、真机图误差 ≤2 mm 验证、人工抽测对照；真实检出模型接入 Detector 协议（可从 legacy/mushroom-cls 数据重标分割训练）。
