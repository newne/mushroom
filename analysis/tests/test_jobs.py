"""票 12：容器内常驻作业的装配与 lifespan 行为。

要钉住的三件事：
1. **不设周期环境变量就不跑**（开发/测试里不该有后台网络请求）；
2. 作业由 `analysis.api` 的 lifespan 在**同一个进程**里起停 —— 不靠容器重启当调度；
   作业与 API 共用**同一份连接**（票 28 第 2 步：不再按路径重连）；
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
    assert default_jobs(connect(path)) == []


def test_jobs_are_assembled_from_env(monkeypatch, tmp_path):
    monkeypatch.setenv("ENV_INGEST_INTERVAL", "300")
    monkeypatch.setenv("AUDIT_INTERVAL", "600")
    monkeypatch.setenv("EMS_ACCOUNT", "acct")
    monkeypatch.setenv("EMS_PASSWORD_MD5", "md5")
    monkeypatch.setenv("BATCH_AT", "05:20")
    monkeypatch.setenv("RANGES_AT", "05:40")
    path = str(tmp_path / "jobs.db")

    jobs = {job.name: job for job in default_jobs(connect(path))}
    assert set(jobs) == {"env", "audit", "batch", "ranges"}
    assert jobs["env"].interval_s == 300
    assert jobs["audit"].interval_s == 600
    assert jobs["batch"].daily_at == "05:20"
    assert jobs["batch"].base_delay_s() > 0, "daily_at 要换算成到下一个 05:20 的秒数"
    # 票 05：区间表重算接进同一套每日任务链（不再是"全仓库只有测试调用"）
    assert jobs["ranges"].daily_at == "05:40"


def test_failures_back_off_with_a_cap():
    """连续失败要退避（别把对方打穿），但要封顶（对方恢复后别等太久）。"""
    job = IngestJob("env", lambda: None, interval_s=300)
    assert job.next_delay_s(0) == 300
    assert job.next_delay_s(1) == 300          # 第一轮失败按正常周期重试
    assert job.next_delay_s(2) == 600
    assert job.next_delay_s(10) == 900         # 封顶，不是无限翻倍

    daily = IngestJob("batch", lambda: None, daily_at="05:20")
    assert daily.next_delay_s(1) <= daily.backoff_cap_s, "每天一次的作业失败后要更快重试（不是等明天）"


def test_audit_job_is_skipped_without_credentials(monkeypatch, tmp_path):
    """没凭据就别启动：拿空口令去打环控平台只是白挨一次 401。"""
    monkeypatch.setenv("AUDIT_INTERVAL", "600")
    path = str(tmp_path / "nocreds.db")
    assert default_jobs(connect(path)) == []


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

    def factory(conn):
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


# ---------- 异常隔离：作业炸了不许把 API 拖崩（Chief 2026-10-13 硬要求） ----------


def test_a_failing_job_never_takes_the_api_down(tmp_path):
    runs: list[int] = []

    def run() -> None:
        runs.append(1)
        raise RuntimeError("TDengine 抖了一下")

    def factory(conn):
        return [IngestJob("bad", run, interval_s=0.01, backoff_cap_s=0.02)]

    app = create_app(str(tmp_path / "bad.db"), jobs_factory=factory)
    with TestClient(app) as c:
        assert c.get("/healthz").json() == {"ok": True}
        time.sleep(0.3)
        assert c.get("/healthz").json() == {"ok": True}, "作业失败不许影响 API"
    assert len(runs) >= 2, "失败后要重试，不是就此停掉"


def test_broken_jobs_factory_still_serves_the_api(tmp_path):
    def factory(conn):
        raise RuntimeError("装配就炸了")

    with TestClient(create_app(str(tmp_path / "f.db"), jobs_factory=factory)) as c:
        assert c.get("/healthz").json() == {"ok": True}
