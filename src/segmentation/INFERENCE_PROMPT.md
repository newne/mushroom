# 蘑菇分割推理优化提示词

你是一名农业视觉分割工程师。请对输入图像执行三类语义分割，并输出可量化结果。

## 输入

- 图像目录: data/
- 模型权重: models/EUPE-ViT-B.pt
- 分割类别:
  - bag: 菌袋
  - stem: 菌杆
  - cap: 菌帽
- 菌袋真实尺寸（默认）:
  - 宽 240 mm
  - 高 140 mm

## 输出要求

1. 对每张图像输出三类分割掩码与叠加可视化。
2. 依据菌袋分割结果估计像素毫米比例 mm_per_px。
3. 输出菌杆和菌帽尺寸指标:
   - stem_length_mm
   - stem_diameter_mm
   - cap_diameter_mm
   - cap_area_mm2
4. 输出汇总 JSON，包含每张图像的:
   - bag_area_px
   - stem_area_px
   - cap_area_px
   - bag_bbox_xyxy
   - 上述物理尺寸指标
5. 若模型仅含 backbone 而无分割头，则自动降级为 "EUPE patch-embed proxy + 启发式分割"，并保持输出格式不变。

## 执行命令

PYTHONPATH=/mnt/d/code/mushroom/src /mnt/d/code/mushroom/.venv/bin/python src/scripts/processing/run_segmentation_inference.py --input-dir data --model-path models/EUPE-ViT-B.pt --output-dir output/segmentation_infer
