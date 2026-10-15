"""存储配置：两套后端共用的连接串解析与配置错误（票 28）。

**为什么连接串解析要单独一个模块**：`analysis/batches.py`（批次台账，票 13）与
`analysis/store.py`（票 28 的存储层）读的是**同一个 pgsql**，优先级只能有一份实现 ——
分两处写，早晚一处"文件优先"、另一处"环境变量优先"，而那个差异只在"文件没挂上"的那台
机器上才现形。

优先级（唯一口径）：**显式参数 → `PGSQL_DSN_FILE` 文件内容 → `PGSQL_DSN` 环境变量**。
都没有就报错，**不退回任何别的通路**。

**为什么推荐文件**：`env_file` 会把值注入 `docker inspect` 的 `Config.Env`（发版官
2026-10-14 实测）；要让口令不在 `docker inspect` 里，只有"DSN 放文件、进程运行时自己读"
这一条路（宿主 0600 文件 bind 进容器 `:ro`）。
"""

from __future__ import annotations

import os
from pathlib import Path

#: 环境变量的名字本身就是契约（compose / systemd / 发版文档都按它给值）。
DEFAULT_DSN_ENV = "PGSQL_DSN"
DEFAULT_DSN_FILE_ENV = "PGSQL_DSN_FILE"


class StoreConfigError(RuntimeError):
    """存储配置错误：缺连接串 / DSN 文件读不了 / schema 名不合法 / 开关值不认识。

    **一律报错，绝不静默退回另一套实现** —— 退回意味着数据写到了别处，而没人知道。
    """


def load_dsn(dsn: str | None = None, *, what: str = "pgsql") -> str:
    """连接串：显式参数 → 文件 → 环境变量。缺了就报错。

    `what` 只影响报错文案（"没有 <what> 连接串"），让日志能指出是哪条链路缺配置。
    """
    if dsn and dsn.strip():
        return dsn.strip()
    path = (os.environ.get(DEFAULT_DSN_FILE_ENV) or "").strip()
    if path:
        try:
            text = Path(path).read_text(encoding="utf-8").strip()
        except OSError as exc:
            raise StoreConfigError(f"读 {path} 失败（{DEFAULT_DSN_FILE_ENV}）：{exc}") from exc
        if text:
            return text
    env = (os.environ.get(DEFAULT_DSN_ENV) or "").strip()
    if env:
        return env
    raise StoreConfigError(
        f"没有 {what} 连接串：设 {DEFAULT_DSN_FILE_ENV}（文件，推荐）或 {DEFAULT_DSN_ENV}（环境变量）。"
        "**不退回别的通路**"
    )
