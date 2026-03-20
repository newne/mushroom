"""路径相关基础设施。"""

import sys
from pathlib import Path

BASE_DIR = Path(__file__).absolute().parent.parent
IMAGE_DIR = BASE_DIR.parent / "data"


def ensure_src_path() -> Path:
    """确保 src 目录在 Python 路径中。"""
    src_path = str(BASE_DIR)
    if src_path not in sys.path:
        sys.path.insert(0, src_path)
    return BASE_DIR
