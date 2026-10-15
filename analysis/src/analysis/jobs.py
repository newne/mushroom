"""容器内常驻作业（票 12）：环境入库 / 操作日志入库 / 批次台账同步。

**最终形态（Chief 2026-10-13）：** API 与三个作业在**同一个容器**里，定时落在
`analysis.api` 的 lifespan —— 进程内 asyncio 任务；不引 cron/supercronic，
entrypoint 也不加角色（净新增只有 1 个 compose service）。

三条口径：

1. **不许拿 restart 当调度**。每个作业是进程内循环：先跑一次、再按周期睡。
   线上 `mushroom-audit-ingest.service` 的 `Type=simple` + `--once` +
   `Restart=always` 把设计的 10 分钟退化成 60 秒一次，还持续打环控平台的登录接口
   —— 这次迁移就是要把这个毛病去掉。
2. **不设环境变量就不跑**：开发与测试默认安静。生产由 compose 设
   `ENV_INGEST_INTERVAL=300` / `AUDIT_INTERVAL=600` / `BATCH_AT=05:20`。
3. **单轮失败只打印，不退出循环**。循环体吞掉 `Exception`；只有 OOM/被杀才会停，
   那时整个容器一起停（主进程就是 API），交给 compose 的 restart 策略兜底。

游标语义（票 12 §3）：环境入库的游标是**进程内变量**，每轮退到整点重算当前小时；
重启后无游标 ⇒ 按 `lookback_minutes` 回溯重算，覆盖靠 `(ts, zone)` 主键幂等。
"""

from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass
from typing import Callable

from analysis.batch_ingest import seconds_until

#: 与 `analysis.api.DEFAULT_TDENGINE_URL` 同一个值；放这里避免 jobs ↔ api 互相 import。
DEFAULT_TDENGINE_URL = "http://127.0.0.1:6041"


@dataclass
class IngestJob:
    """一个常驻作业：`run` 跑一轮，`interval_s` 或 `daily_at` 决定下一轮什么时候。

    `backoff_cap_s` 是**连续失败**时的退避上限：退避只影响这个作业，
    同进程的其它任务与 API 不受影响；上限保证"对方恢复后最多 15 分钟就恢复拉取"。
    """

    name: str
    run: Callable[[], object]
    interval_s: float | None = None
    daily_at: str | None = None
    backoff_cap_s: float = 900.0

    def __post_init__(self) -> None:
        if (self.interval_s is None) == (self.daily_at is None):
            raise ValueError("作业要给且只给一种周期：interval_s 或 daily_at")

    def base_delay_s(self) -> float:
        if self.interval_s is not None:
            return float(self.interval_s)
        return seconds_until(self.daily_at)  # type: ignore[arg-type]

    def next_delay_s(self, failures: int = 0) -> float:
        """正常周期；连续失败时指数退避（×2，最多 4 次方），上限 `backoff_cap_s`。

        对"每天一次"的作业，退避反而让失败**更早**重试（24 h → 15 min），
        这正是我们要的：当天失败了不该等明天。
        """
        base = self.base_delay_s()
        if failures <= 0:
            return base
        return min(base * (2 ** min(failures - 1, 4)), float(self.backoff_cap_s))


async def run_job(job: IngestJob) -> None:
    """先跑一轮，再按周期睡。任何异常都只打印 —— **运行在独立线程里，API 不会跟着崩**。"""
    failures = 0
    while True:
        try:
            await asyncio.to_thread(job.run)
            if failures:
                print(f"[{job.name}] 恢复正常（此前连续失败 {failures} 次）")
            failures = 0
        except Exception as e:  # noqa: BLE001 - 网络/接口抖动不中断循环，也不许冒泡
            failures += 1
            print(f"[{job.name}] 第 {failures} 次失败，{job.next_delay_s(failures):.0f}s 后重试: {e}")
        await asyncio.sleep(job.next_delay_s(failures))


def default_jobs(conn) -> list[IngestJob]:
    """按环境变量装配作业，**共用 API 那一份连接**。没设周期变量的作业不会出现（开发/测试默认安静）。

    | 环境变量 | 作业 | 默认 |
    | --- | --- | --- |
    | `ENV_INGEST_INTERVAL` | 环境入库 | 不跑 |
    | `AUDIT_INTERVAL` | 操作日志入库 | 不跑 |
    | `BATCH_AT` | 批次台账同步 | 不跑（迁移过渡期暂停，见票 13） |
    | `RANGES_AT` | 区间表重算（票 05） | 不跑 |
    """
    jobs: list[IngestJob] = []

    every = (os.environ.get("ENV_INGEST_INTERVAL") or "").strip()
    if every:
        from analysis.env.ingest import run_once as env_run_once
        from analysis.env.tdengine import TdengineEnvSource

        rooms = [r.strip() for r in (os.environ.get("ENV_ROOMS") or "").split(",") if r.strip()]
        source = TdengineEnvSource(
            os.environ.get("TDENGINE_URL", DEFAULT_TDENGINE_URL),
            database=os.environ.get("TDENGINE_DATABASE", "data1"),
            rooms=rooms or None,
            user=os.environ.get("TDENGINE_USER", "root"),
            password=os.environ.get("TDENGINE_PASSWORD", "taosdata"),
            lookback_minutes=float(os.environ.get("ENV_LOOKBACK_MIN", "120")),
        )
        cursor = {"since": None}

        def run_env() -> None:
            cursor["since"] = env_run_once(conn, source, cursor["since"])
            print(f"[env_ingest] 完成，游标: {cursor['since']}")

        jobs.append(IngestJob("env", run_env, interval_s=float(every)))

    audit_every = (os.environ.get("AUDIT_INTERVAL") or "").strip()
    if audit_every:
        from analysis.audit_ingest import DEFAULT_URL
        from analysis.audit_ingest import run_once as audit_run_once

        account = os.environ.get("EMS_ACCOUNT", "")
        password_md5 = os.environ.get("EMS_PASSWORD_MD5", "")
        if not account or not password_md5:
            # 凭据缺失就**不启动**这个作业：拿空口令去打环控平台只是白挨一次 401。
            print("[audit_ingest] 未配置 EMS_ACCOUNT/EMS_PASSWORD_MD5，跳过该作业")
        else:
            base_url = os.environ.get("EMS_URL", DEFAULT_URL)
            lookback = float(os.environ.get("AUDIT_LOOKBACK_MIN", "90"))

            def run_audit() -> None:
                n = audit_run_once(
                    conn, base_url=base_url, account=account,
                    password_md5=password_md5, lookback_minutes=lookback,
                )
                print(f"[audit_ingest] {n} 条")

            jobs.append(IngestJob("audit", run_audit, interval_s=float(audit_every)))

    batch_at = (os.environ.get("BATCH_AT") or "").strip()
    if batch_at:
        from analysis.batch_ingest import run_once as batch_run_once

        def run_batch() -> None:
            n = batch_run_once(conn)
            print(f"[batch_ingest] {n} 个批次")

        jobs.append(IngestJob("batch", run_batch, daily_at=batch_at))

    ranges_at = (os.environ.get("RANGES_AT") or "").strip()
    if ranges_at:
        from analysis.align import refresh_ranges

        def run_ranges() -> None:
            # 传连接而不是路径（票 28 第 2 步）：PG 下没有"路径"这回事，重连会丢 schema。
            n = refresh_ranges(conn=conn)
            print(f"[ranges] 重算完成，{n} 行区间")

        jobs.append(IngestJob("ranges", run_ranges, daily_at=ranges_at))

    return jobs
