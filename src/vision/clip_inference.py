#!/usr/bin/env python3
"""
CLIP推理调度器入口 - 开发环境使用
重定向到src/clip/clip_inference_scheduler.py
"""

import subprocess
import sys
from pathlib import Path

from utils import log_task_event


def _clip_inference_log(event: str, message: str, level: str = "INFO", **context):
    """输出 CLIP 推理入口事件日志。"""
    log_task_event(
        "VISION_CLIP_INFERENCE_ENTRY",
        event,
        message,
        level=level,
        task_type="script",
        **context,
    )


def main():
    """主函数 - 重定向到CLIP推理调度器"""

    # 获取CLIP推理调度器路径
    clip_scheduler_path = (
        Path(__file__).parent / "src" / "clip" / "clip_inference_scheduler.py"
    )

    if not clip_scheduler_path.exists():
        _clip_inference_log(
            "VISION_CLIP_INFERENCE_SCHEDULER_MISSING",
            "找不到 CLIP 推理调度器文件",
            level="ERROR",
            scheduler_path=str(clip_scheduler_path),
            status="failed",
        )
        sys.exit(1)

    # 重定向所有参数到CLIP推理调度器
    try:
        # 构建命令
        cmd = [sys.executable, str(clip_scheduler_path)] + sys.argv[1:]
        _clip_inference_log(
            "VISION_CLIP_INFERENCE_FORWARD_START",
            "开始转发到 CLIP 推理调度器",
            scheduler_path=str(clip_scheduler_path),
            args=" ".join(sys.argv[1:]),
            status="running",
        )

        # 执行命令
        result = subprocess.run(cmd, check=False)
        _clip_inference_log(
            "VISION_CLIP_INFERENCE_FORWARD_FINISH",
            "CLIP 推理调度器执行结束",
            scheduler_path=str(clip_scheduler_path),
            exit_code=result.returncode,
            status="success" if result.returncode == 0 else "failed",
        )
        sys.exit(result.returncode)

    except KeyboardInterrupt:
        _clip_inference_log(
            "VISION_CLIP_INFERENCE_INTERRUPTED",
            "用户中断 CLIP 推理入口执行",
            level="WARNING",
            status="failed",
        )
        sys.exit(1)
    except Exception as e:
        _clip_inference_log(
            "VISION_CLIP_INFERENCE_FORWARD_FAILED",
            "转发执行 CLIP 推理调度器失败",
            level="ERROR",
            status="failed",
            error_type=type(e).__name__,
            error_message=str(e),
        )
        sys.exit(1)


if __name__ == "__main__":
    main()
