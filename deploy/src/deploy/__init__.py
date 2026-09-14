"""部署侧胶水：把 patrol（纯逻辑库）接到真实世界（HTTP、SDK、时刻表）。

**为什么单独成包**：``patrol`` 有一条安全基线——**库内零网络调用**（见
``patrol.links`` 与 ``patrol.capture_client`` 的 docstring）。真实 HTTP 只由部署侧
以 ``patrol.links.Transport`` 的唯一形状注入。把这一层独立成 ``deploy`` 包，patrol
就能脱离网络被单测、审计与静态扫描，而网络相关的东西全在这里。

本包不做业务逻辑，只做**装配**：读配置 → 构造依赖 → 交给 ``patrol.daemon``。
"""

__all__ = ["m1", "transport"]
