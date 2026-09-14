# mushroom-cls（已冻结归档）

> **状态：已过时，代码冻结不再维护**（2026-08-30，随仓库治理归档）。
> 本目录是上一阶段"YOLO 按周龄分类"方案的训练代码，已被 `docs/patrol/fmc4030-camera-scan/spec.md`
> 描述的新一代巡检测量系统取代。

保留价值（供新系统 `measure/` 包复用，见票 05）：

- `datasets/`（本机路径，未入库）：按 week 分级的蘑菇图像数据集，可重标为分割/检测训练集
- `src/models/` 下的预训练权重（`yolo11n-cls.pt` 等，未入库）：作为迁移学习的起点

已知问题（不再修复）：`src/make_dataset/make_dataset.py` 存在两份重复实现与无用依赖
（wandb/comet/pyarmor）；`main.py` 为脚手架桩。
