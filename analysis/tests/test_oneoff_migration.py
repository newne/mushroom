"""一次性搬迁工具（`analysis.tools.oneoff_sqlite_to_pg`）的回归用例：**旧库里有 NULL 行时连跑两遍**。

工具是 SQLite 依赖的**唯一**例外（它要从旧库读一次数据），本文件是它的自测 —— **与工具同生共死**：
`analysis/src/analysis/tools/` 删掉时本文件一起删。这里 `import sqlite3` 只为**造一个旧库夹具**
（形状照生产 `mushrooms.db` 的 `images` / `rounds` 的小库），**不是**给存储层留回退通路：
被测的写入侧仍然只有 PG 一个后端（`analysis.store`），每个用例仍是 `analysis_test_<随机>`
schema、跑完 DROP 并复查。

为什么要有这条：合成旧库一直只有带 `angle_profile` 的行，于是「NULL 行的 upsert 不幂等」从没露头 ——
2026-10-15 拿**生产旧库的真实快照**（6,921 行）彩排，第二遍 PG 侧 6,921 → 6,931，多出来的 10 行
全是 `angle_profile IS NULL` 的采图失败帧。工具本身没写错：错在唯一键的 NULL 语义（见
`test_store_postgres.py` 的同名断言与 `store` 的建表 DDL）。
"""

from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
from pathlib import Path

#: 工具自己找 `analysis/src`；子进程跑 `-m` 也要靠它（`-m` 先解析包名，工具里的 sys.path 补不及）。
ANALYSIS_SRC = Path(__file__).resolve().parents[1] / "src"

#: 九张表（与工具、与 `analysis.store` 的建表一致）—— 第二遍要**逐表**比行数。
TABLES = ("batches", "batches_state", "environment", "images", "measurements",
          "operations", "ranges", "ranges_state", "rounds")

#: 旧库 `images`：两行正常帧 + 两行「采图失败」帧（`angle_profile` 缺测 ⇒ NULL）。
#: 列照生产旧库：ts / round_id / station_id / box_id / angle_profile / y / z / object_name /
#: ok / error / room_id / entry_date / batch_no。
LEGACY_IMAGES = [
    ("2026-09-13T10:00:20", "r-1", "S101", "B101", "level0", -40.0, -90.0,
     "20260913/B101_S101_level0.jpg", 1, None, "611", "2026-09-13", "mogu-1"),
    ("2026-09-13T10:01:20", "r-1", "S102", "B102", "top45", -40.0, -145.0,
     "20260913/B102_S102_top45.jpg", 1, None, "611", "2026-09-13", "mogu-1"),
    ("2026-09-13T10:02:20", "r-1", "S103", "B103", None, None, None, None, 0,
     "采图链路失败: refused", "611", "2026-09-13", "mogu-1"),
    ("2026-09-13T10:03:20", "r-1", "S104", "B104", None, None, None, None, 0,
     "采图链路失败: timeout", "611", "2026-09-13", "mogu-1"),
]

LEGACY_ROUNDS = [
    ("2026-09-13T10:05:20", "r-1", 0, 4),
]


def _build_legacy_db(path: Path) -> None:
    """造一个「旧库」：只建这条用例用到的两张表与列（形状照生产 `mushrooms.db`）。"""
    conn = sqlite3.connect(path)
    try:
        conn.execute(
            "CREATE TABLE images (ts text, round_id text, station_id text, box_id text,"
            " angle_profile text, y real, z real, object_name text, ok integer, error text,"
            " room_id text, entry_date text, batch_no text)"
        )
        conn.executemany(
            "INSERT INTO images VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", LEGACY_IMAGES
        )
        conn.execute(
            "CREATE TABLE rounds (ts text, round_id text, ok integer, n_results integer)"
        )
        conn.executemany("INSERT INTO rounds VALUES (?, ?, ?, ?)", LEGACY_ROUNDS)
        conn.commit()
    finally:
        conn.close()


def _run_tool(dsn: str, schema: str, source: Path, cwd: Path) -> subprocess.CompletedProcess:
    """按彩排的真实形状跑 CLI（子进程 + `python -m`）：**不 import 工具模块**。

    连接串走 `--dsn`（本进程的子进程参数，不过 shell、不打印、不进仓库）；输出强制 UTF-8
    （工具打的是中文，控制台缺省编码在 Windows 上会把它变成乱码甚至解码失败）。
    """
    env = {**os.environ, "PYTHONPATH": str(ANALYSIS_SRC), "PYTHONIOENCODING": "utf-8"}
    return subprocess.run(
        [sys.executable, "-m", "analysis.tools.oneoff_sqlite_to_pg",
         "--source", str(source), "--dsn", dsn, "--schema", schema],
        cwd=cwd, env=env, capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=180,
    )


def _count(catalog, schema: str, table: str) -> int:
    catalog.execute('SELECT COUNT(*) FROM "' + schema + '"."' + table + '"')
    return int(catalog.fetchone()[0])


def _counts(catalog, schema: str) -> dict[str, int]:
    return {table: _count(catalog, schema, table) for table in TABLES}


def test_migration_tool_writes_nothing_on_a_second_run_with_null_frames(
    pg_dsn, pg_schema, catalog, tmp_path
):
    """旧库里有 `angle_profile IS NULL` 的行时，搬迁连跑两遍的第二遍**不改任何一张表的行数**。

    第一遍把旧库的行数搬平（含 NULL 帧 —— 它们照样插得进去）；第二遍是纯粹的重复投递，
    「写入 0 行」的判据就是逐表行数一个都不许涨。
    """
    source = tmp_path / "mushrooms.db"
    _build_legacy_db(source)

    first = _run_tool(pg_dsn, pg_schema, source, tmp_path)
    assert first.returncode == 0, first.stdout + first.stderr
    assert "对账通过" in first.stdout

    after_first = _counts(catalog, pg_schema)
    assert after_first["images"] == len(LEGACY_IMAGES), "第一遍把旧库搬平（含 NULL 帧）"
    assert after_first["rounds"] == len(LEGACY_ROUNDS)
    catalog.execute(
        'SELECT COUNT(*) FROM "' + pg_schema + '".images WHERE angle_profile IS NULL'
    )
    assert catalog.fetchone()[0] == 2, "夹具里确实有两行 NULL 帧，否则这条用例测不到东西"

    second = _run_tool(pg_dsn, pg_schema, source, tmp_path)
    assert second.returncode == 0, second.stdout + second.stderr
    assert "对账通过" in second.stdout

    assert _counts(catalog, pg_schema) == after_first, "第二遍不许在任何一张表里留下新行"
