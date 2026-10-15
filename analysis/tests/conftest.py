"""测试卫生：常驻作业的环境变量在每个用例前清空。

票 12 的装配口径是"设了周期变量就跑"（生产靠 compose 给）。测试机上若有人顺手
`$env:ENV_INGEST_INTERVAL=300`，整套 API 测试就会去连 TDengine / 环控平台 ——
本文件把相关变量清掉，让测试保持封闭。
"""

from __future__ import annotations

import pytest

#: 存储后端开关也一并清掉（票 28）：缺省必须是 SQLite，测试不该被开发机上顺手设的
#: `ANALYSIS_STORE=postgres` 带跑（那会去找 pgsql）。PG 用例**显式**传 DSN 与 schema，
#: 不依赖这两个变量。
INGEST_ENV = (
    "ENV_INGEST_INTERVAL", "AUDIT_INTERVAL", "BATCH_AT", "RANGES_AT", "ANALYSIS_DB",
    "ANALYSIS_STORE", "ANALYSIS_PG_SCHEMA",
)


@pytest.fixture(autouse=True)
def _no_ingest_jobs(monkeypatch):
    for name in INGEST_ENV:
        monkeypatch.delenv(name, raising=False)
