"""Database model declarations.

## 为什么这里也是惰性的（PEP 562）

`utils/create_table.py` 里还有一句 ``from storage.models.base import Base``，而导入子模块会
**先执行本文件**。所以本文件在导入期**不能**去取 `utils/create_table` 的名字——那正是循环的
那一环（2026-09-15 实测：`import storage.models` / `import main` / `python src/main.py --help`
全部以 ImportError 失败）。

于是：``Base`` 照常直接导入（它由 `storage/models/base.py` 自己定义，不参与循环），其余模型名
走模块级 ``__getattr__``，**用到时才取**。两条导入路径都成立：

    from storage.models import MushroomBatchYield      # 包接口（重构的目标形态）
    from utils.create_table import MushroomBatchYield  # 旧路径（现存 17 处调用方）

定义真正搬进 `storage/models/<域>.py` 之后，本文件的 `__getattr__` 与各 shim 都该删掉。
"""

from __future__ import annotations

from storage.models._lazy import reexport
from storage.models.base import Base

__all__ = [
    "Base",
    "ControlStrategyKnowledgeBaseClusterMeta",
    "ControlStrategyKnowledgeBaseClusterRule",
    "ControlStrategyKnowledgeBaseRun",
    "ControlStrategyKnowledgeBaseStageRule",
    "DecisionAnalysisBatchStatus",
    "DecisionAnalysisDynamicResult",
    "DecisionAnalysisSkillAudit",
    "DecisionAnalysisStaticConfig",
    "DeviceSetpointChange",
    "ImageTextQuality",
    "MushroomBatchYield",
    "MushroomBatchYieldAudit",
    "MushroomEnvDailyStats",
    "MushroomImageEmbedding",
]

__getattr__ = reexport(__name__, "utils.create_table", [n for n in __all__ if n != "Base"])
