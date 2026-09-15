"""导入期的循环依赖回归：`storage.models` ↔ `utils.create_table`。

2026-09-15 实测（重构进行中）：两边互相导入——
`utils/create_table.py` 要 `from storage.models.base import Base`，而导入子模块会先执行
`storage/models/__init__.py`，它又去 `from storage.models.batch_yield import ...`，后者回头
`from utils.create_table import ...`（半初始化）——于是

    import storage.models          → ImportError
    import main / import scheduling → ImportError
    python src/main.py             → ImportError

入口恰好是"先导入 create_table"的那个顺序，所以这个循环**平时看不出来**，只在启动时炸。
现在两边都改成惰性再导出（PEP 562，见 `storage/models/_lazy.py`），两条路径都必须成立。
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

SRC = Path(__file__).resolve().parents[2] / "src"


def test_storage_models_package_imports():
    import storage.models as models

    assert models.Base is not None
    assert models.MushroomBatchYield.__name__ == "MushroomBatchYield"


def test_both_import_paths_give_the_same_class():
    """旧路径（`utils.create_table`，17 处调用方）与新路径（包接口）必须是同一个类。"""
    from storage.models import MushroomBatchYield as via_package
    from utils.create_table import MushroomBatchYield as via_module

    assert via_package is via_module


def test_create_table_first_in_a_fresh_interpreter():
    """**入口的顺序**（先 create_table、后 storage.models）单独跑一次。

    本进程里别的测试可能已经导入了 storage.models，把顺序掩盖掉；所以用子进程还原
    prod 的导入顺序。子进程只导入模块，不建表、不连库（引擎与连接池都是惰性的）。
    """
    code = "import utils.create_table; import storage.models; print('IMPORT-OK')"
    env = {**os.environ, "PYTHONPATH": str(SRC)}
    got = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                         env=env, timeout=180, cwd=str(SRC.parent))
    assert "IMPORT-OK" in got.stdout, f"导入顺序复现失败：\n{got.stdout}\n{got.stderr[-1500:]}"
