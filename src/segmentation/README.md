# 蘑菇生产分割与生长分析系统设计（EUPE + 时序稳定）

本文档给出可直接在本仓库落地的方案，目标是对 **菌袋/菌杆/菌帽** 三类进行分割，并产出真实尺寸与生长速度指标。

## 1. 任务目标到模块映射

- 菌袋分割（尺度基准）
  - 输出 bag mask，用于像素到毫米换算。
- 菌杆/菌帽分割（密集小目标）
  - 输出 stem/cap mask，并做实例提取与小目标增强。
- 单帧真实尺寸估计
  - 由 `geometry.py` 计算 `mm_per_px`、茎长/帽径/面积。
- 跨时间生长速度评估
  - 由 `pipeline.py::compare_growth` 输出 `dL/dt` 等指标。
- 视频时序稳定
  - 由 `temporal.py` 提供 IoU 匹配 + EMA 平滑 + 置信度衰减。

## 2. EUPE 作为主干的技术理由

### 2.1 选择 EUPE 的核心优势

- 多教师蒸馏（CLIP + DINOv3-H+ + PElang）带来更通用的视觉表征。
- 对密集预测更友好：纹理、边缘、小目标区分能力更强，适配菌杆/菌帽这类高相似细粒度对象。
- 模型效率导向：目标是高效通用感知编码器，参数规模可控制在边缘设备可部署区间（<100M 级别的方案可选）。
- EUPE 代码结构天然支持分割任务典型 pipeline：
  - backbone 多尺度特征
  - segmentation decoder
  - sliding-window 推理

### 2.2 与 SAM3 Backbone 的结构对比与局限

SAM3 Vision Backbone（官方公开结构）可抽象为：

- 32-layer ViT 主干
- `head_dim=1024`
- 检测/分割 neck（如 `Sam3DualViTDetNeck`、FPN 风格多尺度输出）

在本场景的局限：

- 训练范式相对单一，跨域融合能力通常弱于 EUPE 的多教师蒸馏范式。
- 大模型推理开销更高，对农业边缘设备（Jetson/工控机）不友好。
- 在密集小目标、遮挡、弱纹理边界下，若无强领域微调，细粒度泛化压力较大。

结论：蘑菇工厂化场景优先选择 EUPE 主干，SAM3 可作为离线教师或高精度对照模型。

## 3. 模型架构设计

## 3.1 主干与解码器组合

推荐优先级：

1. `EUPE ViT-B + UPerHead`：精度/速度平衡，适合主线。
2. `EUPE ViT-S + 轻量解码器`：边缘端实时优先。
3. `EUPE ConvNeXt-Tiny + Mask2Former`：实例分割质量更高，但推理稍重。

本仓库的推理适配接口：

- `pipeline.py::SegmentationPipeline`
- `model_predictor(patch)->logits(C,H,W)` 由外部训练框架提供。

## 3.2 高分辨率滑窗推理

实现位置：`pipeline.py::_sliding_windows`。

建议参数：

- 原图 2K~4K：`crop_size=1024, stride=768`
- 原图 <=1.5K：`crop_size=768, stride=512`
- 重叠率建议 25%~35%，降低接缝伪影。

## 3.3 多尺度训练与推理

训练输入尺度建议：`[256, 384, 512]` 随机采样。

- 训练：随机尺度 + 随机裁剪 + 颜色扰动 + 轻几何增强。
- 推理：优先 `scale=1.0`；资源允许时加 `0.75/1.25` 并融合。

当前仓库代码中，`pipeline.py` 默认启用 `scale=1.0`，多尺度插值需在部署时接入 cv2/torch 插值后打开。

## 4. 三类 mask 生成与后处理

## 4.1 语义到实例

实现位置：`postprocess.py`。

流程：

1. 语义掩码按类别拆分（bag/stem/cap）。
2. 连通域提取实例。
3. 面积过滤：去噪点、去异常大块。
4. 输出每个实例的 `area_px`、`bbox_xyxy`。

## 4.2 几何特征计算

实现位置：`geometry.py`。

- 计算面积
- 二阶矩近似主轴/次轴（等价于拟合椭圆主方向近似）
- 可用于茎长、茎粗、帽径估计

## 4.3 小目标增强策略（训练侧）

- 损失重加权：`w_stem > w_cap > w_bag`
- 小目标重采样：对高密度区域做 zoom-in crop
- Hard example mining：优先采样遮挡、边界模糊帧
- Boundary loss（可选）：提升菌帽边界精细度

## 5. 基于菌袋尺度的真实尺寸换算

## 5.1 像素到毫米转换

固定机位下，使用菌袋标准尺寸作为参考物：

$$
mm\_per\_px^x = \frac{W_{bag}^{mm}}{W_{bag}^{px}},\quad
mm\_per\_px^y = \frac{H_{bag}^{mm}}{H_{bag}^{px}},\quad
mm\_per\_px = \frac{mm\_per\_px^x + mm\_per\_px^y}{2}
$$

代码实现：`geometry.py::estimate_mm_per_pixel_from_bag`。

## 5.2 蘑菇尺寸指标

- 茎长：菌杆主轴长度
- 茎径：菌杆次轴长度
- 帽径：菌帽主轴长度
- 面积：mask 像素面积转毫米平方

$$
L_t = major\_axis_{stem}^{px} \cdot mm\_per\_px
$$
$$
D_t = major\_axis_{cap}^{px} \cdot mm\_per\_px
$$
$$
A_t = area^{px} \cdot (mm\_per\_px)^2
$$

代码实现：`geometry.py::extract_mushroom_size_metrics`。

## 5.3 生长速度指标

$$
\Delta L/\Delta t = \frac{L_t - L_{t-1}}{\Delta t}, \quad
\Delta D/\Delta t = \frac{D_t - D_{t-1}}{\Delta t}
$$

代码实现：`pipeline.py::compare_growth`。

## 6. 跨时间序列生长建模

可在上游统计系统中把每帧结果聚合成时间序列后建模：

- 线性阶段：早期近似线性增长
- 指数阶段：快速扩张
- Logistic：受资源限制后趋缓

建议特征：

- 视觉指标：`L_t, D_t, A_t, 密度, 遮挡率`
- 环境指标：温度、湿度、光照、CO2
- 交互项：`温度*湿度`, `CO2*光照`

训练建议：

- 先做单房间基线模型（线性回归/XGBoost）
- 再做跨房间混合效应模型
- 输出可调参信号：升温/降湿/补光建议阈值

## 7. 视频时序一致性（防 flickering）

实现位置：`temporal.py`。

方法 A：Optical-flow alignment（推荐）

- 用光流把 `t-1` 概率图 warp 到 `t`
- 与当前 logits 融合后再 argmax
- 适合轻微相机抖动

方法 B：Tracking-by-matching（已实现 IoU 版）

- 用 IoU 做实例匹配（可扩展 appearance embedding）
- 保持实例 ID 连续，减少目标“消失-重生”

方法 C：时序平滑（已实现）

- logits EMA：`temporal.py::TemporalSmoother.smooth_logits`
- 置信度衰减：漏检帧不立刻删除目标

## 8. 工程化部署方案

## 8.1 设备建议

- CPU-only 工控机：EUPE ViT-S + INT8/FP16（ONNX/TensorRT）
- Jetson Orin：EUPE ViT-B + FP16 + 滑窗批处理
- 边缘 GPU：EUPE ViT-B + 多尺度推理（1.0 + 1.25）

## 8.2 运行时优化

- 半精度（FP16/BF16）
- 分块推理（已支持）
- 静态相机下缓存背景特征（按需更新）
- 按场景开启多尺度（低负载单尺度，高负载关闭增强）

## 9. 超参数建议（可直接起步）

### 9.1 训练

- 优化器：AdamW
- 初始学习率：`6e-5`（ViT-B），`1e-4`（ViT-S）
- weight decay：`0.05`
- batch size：`16`（按显存缩放）
- 训练轮次：`80~120 epochs`
- 损失：`CE + Dice`（可加 boundary loss）
- 类别权重：`bag:0.5, stem:2.0, cap:1.5`

### 9.2 推理

- `crop_size=1024`
- `stride=768`
- `temporal.ema_alpha=0.65`
- `temporal.iou_match_threshold=0.3`
- `min_stem_area_px=24`
- `min_cap_area_px=32`

## 10. 训练 + 推理完整落地步骤

1. 数据准备

- 标注三类：bag/stem/cap。
- 保证同一机位采样，记录菌袋真实宽高（mm）。

1. 训练 EUPE 分割模型

- 采用 EUPE backbone + UPerHead/Mask2Former。
- 开启多尺度训练（256/384/512）。
- 强化小目标采样与类别重加权。

1. 导出推理接口

- 导出统一 `model_predictor(patch)->logits(C,H,W)`。
- 接入 `create_mushroom_segmentation_pipeline`。

1. 单帧推理

- `infer_semantic_mask` -> `compute_frame_metrics`。
- 输出 `stem_length_mm/cap_diameter_mm/...`。

1. 视频推理

- 帧级循环，启用 `TemporalSmoother`。
- 用 `build_instances` + `match_instances_iou` 保持实例稳定。

1. 生长分析

- 用 `compare_growth` 计算 `ΔL/Δt`、`ΔD/Δt`。
- 融合环境参数建立回归模型，生成生产调参建议。

## 11. 文本流程图

```text
[图像/视频输入]
      |
      v
[EUPE Backbone 特征提取]
      |
      v
[Segmentation Decoder]
      |
      v
[滑窗拼接 + 多尺度融合]
      |
      v
[语义Mask: bag/stem/cap]
      |
      +----> [bag mask] --> [mm_per_px标定]
      |
      +----> [stem/cap mask] --> [实例提取+后处理]
                               |
                               v
                        [几何测量: L_t, D_t, A_t]
                               |
                     +---------+---------+
                     |                   |
                     v                   v
            [时间序列增长建模]      [视频时序稳定]
                     |                   |
                     +---------+---------+
                               |
                               v
                 [生长速度评估与生产调参输出]
```

## 12. 代码入口说明

- `pipeline.py`: 分割总管线（滑窗、多尺度、尺度换算、增长率）
- `postprocess.py`: 连通域实例提取与面积过滤
- `geometry.py`: 像素-毫米换算、几何指标计算
- `temporal.py`: IoU 匹配、EMA 平滑、置信度时序处理

该实现避免依赖不存在 API，采用可替换 predictor 接口，与 EUPE/SAM3 官方仓库解耦，便于你直接接入任意训练产物。
