"""惰性再导出：给 `storage/models/*` 这层兼容 shim 用（PEP 562）。

## 为什么必须惰性

重构把模型按域拆到 `storage/models/<域>.py`，但**定义**目前还留在 `utils/create_table.py`，
各 shim 只是把它再导出一次。而 `utils/create_table.py` 自己要
``from storage.models.base import Base`` —— 导入一个子模块会**先执行父包的 ``__init__``**，
于是形成：

    utils.create_table → storage.models.__init__ → storage.models.batch_yield
                       → utils.create_table（半初始化，名字还没定义）→ ImportError

2026-09-15 实测：`import main`、`import scheduling`、`python src/main.py --help` 全部
以 `cannot import name 'MushroomBatchYield' from partially initialized module` 失败；
把 shim 改成惰性之后两条导入路径都成立：

    import utils.create_table                     # 直接用它（现存 17 处调用方）
    from storage.models import MushroomBatchYield # 走包接口（重构的目标形态）

**这是权宜之计**：等模型定义真正搬进 `storage/models/<域>.py`，shim 与本文件都该删掉
（那时 `create_table` 反过来从 `storage.models` 取类，依赖方向就正了）。
"""

from __future__ import annotations

from importlib import import_module


def reexport(module_name: str, source: str, names: list[str]):
    """返回一个模块级 ``__getattr__``：访问 ``names`` 里的名字时才去 ``source`` 取。"""

    def __getattr__(name: str):
        if name in names:
            return getattr(import_module(source), name)
        raise AttributeError(f"module {module_name!r} has no attribute {name!r}")

    return __getattr__
