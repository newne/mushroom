"""PG 用例的公共装置（票 28）：测试专用 DSN 的解析 + 独立 schema 的新建/删除/复查。

**只认测试专用的两个变量**（`ANALYSIS_TEST_DSN` / `ANALYSIS_TEST_DSN_FILE`），**故意不复用
`PGSQL_DSN*`**：那两个是应用跑生产用的，本机 shell 里挂着生产 DSN 时，跑一次测试不该往生产库里建 schema。

**硬门（票 28）**：PG 用例只在自己建的 `analysis_test_<随机>` schema 里建表、只在自己的 schema 里读写，
跑完 `DROP SCHEMA … CASCADE` 并断言它不存在；**不碰 public、不碰遗留表、不碰农场库、不碰 TDengine**
（ADR-0026）。连接串只在本进程里用：不打印、不进仓库。
"""

from __future__ import annotations

import os
import uuid
from pathlib import Path

#: 测试专用变量（**不是**应用跑生产的 `PGSQL_DSN*`）。
DSN_ENV = "ANALYSIS_TEST_DSN"
DSN_FILE_ENV = "ANALYSIS_TEST_DSN_FILE"

#: 每个用例一个 schema，统一前缀便于"跑完复查残留为 0"。
SCHEMA_PREFIX = "analysis_test_"


def test_dsn() -> str | None:
    """测试用的连接串；两个变量都没设（或文件读不出来）⇒ `None`（调用方整组 skip）。"""
    dsn = (os.environ.get(DSN_ENV) or "").strip()
    if dsn:
        return dsn
    path = (os.environ.get(DSN_FILE_ENV) or "").strip()
    if not path:
        return None
    try:
        text = Path(path).read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return text or None


def new_schema_name() -> str:
    """本用例独占的 schema 名（随机，不撞车、不撞上一次跑剩的）。"""
    return f"{SCHEMA_PREFIX}{uuid.uuid4().hex[:12]}"


def connect(dsn: str):
    """**独立连接**（不经过被测对象）：查目录/核对用，避免"自己验自己"。"""
    import psycopg

    return psycopg.connect(dsn, autocommit=True)


def drop_schema(dsn: str, name: str) -> None:
    """删掉用例自己的 schema 并断言不存在。删不掉就报错（不许留垃圾）。"""
    assert name.startswith(SCHEMA_PREFIX), f"只删测试 schema，收到 {name!r}"
    with connect(dsn) as conn, conn.cursor() as cur:
        cur.execute(f'DROP SCHEMA IF EXISTS "{name}" CASCADE')
        cur.execute(
            "SELECT 1 FROM information_schema.schemata WHERE schema_name = %s", (name,)
        )
        assert cur.fetchone() is None, f"测试 schema {name} 没删干净"


def test_schemas(dsn: str) -> list[str]:
    """目标库里现存的测试 schema 名（**只读** `information_schema`）。

    **不是用例**：给"跑完自己复查一次残留为 0"用的（每次改完这里跑一遍，结果写进报告）。
    逐用例的清理由 `drop_schema` 断言（删不掉就当场红），这里看的是**整轮跑完之后**的库面。
    """
    with connect(dsn) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT schema_name FROM information_schema.schemata WHERE schema_name LIKE %s "
            "ORDER BY schema_name",
            (SCHEMA_PREFIX + "%",),
        )
        return [row[0] for row in cur.fetchall()]
