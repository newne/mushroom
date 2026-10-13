"""票 12：容器内常驻作业的装配与 lifespan 行为。

要钉住的三件事：
1. **不设周期环境变量就不跑**（开发/测试里不该有后台网络请求）；
2. 作业由 `analysis.api` 的 lifespan 在**同一个进程**里起停 —— 不靠容器重启当调度；
3. 退出时任务被取消，不会留下野循环。
"""

from __future__ import annotations

import threading
import time

import pytest
from analysis.api import create_app
from analysis.db import connect
from analysis.jobs import IngestJob, default_jobs
from fastapi.testclient import TestClient


def test_without_interval_env_no_job_runs(tmp_path):
    path = str(tmp_path / "quiet.db")
    assert default_jobs(connect(path), path) == []


def test_jobs_are_assembled_from_env(monkeypatch, tmp_path):
    monkeypatch.setenv("ENV_INGEST_INTERVAL", "300")
    monkeypatch.setenv("AUDIT_INTERVAL", "600")
    monkeypatch.setenv("EMS_ACCOUNT", "acct")
    monkeypatch.setenv("EMS_PASSWORD_MD5", "md5")
    monkeypatch.setenv("BATCH_AT", "05:20")
    path = str(tmp_path / "jobs.db")

    jobs = {job.name: job for job in default_jobs(connect(path), path)}
    assert set(jobs) == {"env", "audit", "batch"}
    assert jobs["env"].interval_s == 300
    assert jobs["audit"].interval_s == 600
    assert jobs["batch"].daily_at == "05:20"
    assert jobs["batch"].delay_s() > 0, "daily_at 要换算成到下一个 05:20 的秒数"


def test_audit_job_is_skipped_without_credentials(monkeypatch, tmp_path):
    """没凭据就别启动：拿空口令去打环控平台只是白挨一次 401。"""
    monkeypatch.setenv("AUDIT_INTERVAL", "600")
    path = str(tmp_path / "nocreds.db")
    assert default_jobs(connect(path), path) == []


def test_job_needs_exactly_one_period():
    with pytest.raises(ValueError):
        IngestJob("x", lambda: None)
    with pytest.raises(ValueError):
        IngestJob("x", lambda: None, interval_s=1, daily_at="05:20")


def test_lifespan_runs_the_job_once_then_stops(tmp_path):
    first_run = threading.Event()
    calls: list[int] = []

    def run() -> None:
        calls.append(1)
        first_run.set()

    def factory(conn, db_path):
        return [IngestJob("stub", run, interval_s=3600)]

    app = create_app(str(tmp_path / "life.db"), jobs_factory=factory)
    with TestClient(app) as c:
        assert c.get("/healthz").json() == {"ok": True}
        assert first_run.wait(5.0), "lifespan 启动后应当先跑一轮（不是等一个周期）"

    after_shutdown = len(calls)
    time.sleep(0.2)
    assert len(calls) == after_shutdown, "退出后不该还有作业在跑"


def test_db_path_defaults_to_analysis_db_env(monkeypatch, tmp_path):
    """容器里靠 `ANALYSIS_DB` 给路径，不靠工作目录（票 12 §2）。"""
    target = tmp_path / "from-env.db"
    monkeypatch.setenv("ANALYSIS_DB", str(target))
    with TestClient(create_app()) as c:
        assert c.get("/healthz").json() == {"ok": True}
    assert target.exists()
