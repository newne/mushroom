"""测试卫生：常驻作业的环境变量在每个用例前清空。

票 12 的装配口径是"设了周期变量就跑"（生产靠 compose 给）。测试机上若有人顺手
`$env:ENV_INGEST_INTERVAL=300`，整套 API 测试就会去连 TDengine / 环控平台 ——
本文件把相关变量清掉，让测试保持封闭。
"""

from __future__ import annotations

import pytest

INGEST_ENV = ("ENV_INGEST_INTERVAL", "AUDIT_INTERVAL", "BATCH_AT", "ANALYSIS_DB")


@pytest.fixture(autouse=True)
def _no_ingest_jobs(monkeypatch):
    for name in INGEST_ENV:
        monkeypatch.delenv(name, raising=False)
