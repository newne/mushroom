"""
CLIP 微调 CLI
"""

from __future__ import annotations

import argparse
import json

from .convert import convert_checkpoint
from .evaluate import run_evaluation, run_inference
from .train import run_training


def main() -> None:
    """统一的训练、评估、推理命令入口。"""
    parser = argparse.ArgumentParser(description="CLIP Fine-tuning CLI")
    subparsers = parser.add_subparsers(dest="command", required=True)

    train_parser = subparsers.add_parser("train", help="训练微调模型")
    train_parser.add_argument("--config", required=True, help="YAML 配置路径")
    train_parser.add_argument("--override", action="append", default=[], help="覆盖配置项")

    eval_parser = subparsers.add_parser("evaluate", help="评估模型")
    eval_parser.add_argument("--config", required=True, help="YAML 配置路径")
    eval_parser.add_argument("--checkpoint", default=None, help="模型 checkpoint 路径")
    eval_parser.add_argument("--override", action="append", default=[], help="覆盖配置项")
    eval_parser.add_argument("--baseline-report", default=None, help="基线评估报告路径，用于对比")

    infer_parser = subparsers.add_parser("infer", help="单次推理")
    infer_parser.add_argument("--config", required=True, help="YAML 配置路径")
    infer_parser.add_argument("--checkpoint", required=True, help="模型 checkpoint 路径")
    infer_parser.add_argument("--image", required=True, help="图像路径")
    infer_parser.add_argument("--text", required=True, help="文本输入")

    convert_parser = subparsers.add_parser("convert", help="导出微调模型")
    convert_parser.add_argument("--config", required=True, help="YAML 配置路径")
    convert_parser.add_argument("--checkpoint", required=True, help="模型 checkpoint 路径")
    convert_parser.add_argument("--output-dir", required=True, help="导出目录")
    convert_parser.add_argument("--no-merge", action="store_true", help="不合并 LoRA 权重")

    args = parser.parse_args()

    if args.command == "train":
        run_training(args.config, args.override)
        return
    if args.command == "evaluate":
        run_evaluation(args.config, args.checkpoint, args.override, args.baseline_report)
        return
    if args.command == "infer":
        result = run_inference(args.config, args.checkpoint, args.image, args.text)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return
    if args.command == "convert":
        convert_checkpoint(args.config, args.checkpoint, args.output_dir, merge=not args.no_merge)
        return


if __name__ == "__main__":
    main()
