#!/usr/bin/env python3
"""
离线图像分析运行脚本
"""

import sys
from pathlib import Path

SRC_DIR = Path(__file__).resolve().parents[1]
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from global_const.paths import ensure_src_path

ensure_src_path()

from scripts.processing.offline_batch_runner import run_offline_batch


def main() -> int:
    return run_offline_batch(limit_per_room_day=2)

if __name__ == "__main__":
    raise SystemExit(main())
