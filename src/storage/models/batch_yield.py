"""兼容 shim：这些模型目前仍定义在 `utils.create_table` 里（重构进行中）。

**必须惰性再导出**（原因与实测见 `storage/models/_lazy.py`）：在导入期就去取
`utils.create_table` 的名字会与 `create_table` 自己的
`from storage.models.base import Base` 形成循环，导致 `import storage.models` 直接失败。
"""

from __future__ import annotations

from storage.models._lazy import reexport

__all__ = [
    "MushroomBatchYield",
    "MushroomBatchYieldAudit",
]

__getattr__ = reexport(__name__, "utils.create_table", __all__)