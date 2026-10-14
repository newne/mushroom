"""prod 接收 API（票 06）：巡检结果入库 + 健康检查。

单 worker 部署（uvicorn --workers 1），与截图服务同样的串行约束。
运行::

    uvicorn analysis.api:create_app --factory --host 0.0.0.0 --port 8000
"""

from __future__ import annotations

import asyncio
import os
from contextlib import asynccontextmanager, suppress
from datetime import datetime, timedelta

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from analysis.audit import summarize_operations
from analysis.batches import window_of
from analysis.jobs import default_jobs, run_job
from analysis.db import (
    IMAGE_FIELDS,
    MEASUREMENT_FIELDS,
    ROUND_FIELDS,
    connect,
    fetch_batches as db_fetch_batches,
    fetch_environment,
    fetch_operations,
    fetch_images,
    fetch_measurements,
    fetch_ranges,
    fetch_ranges_state,
    fetch_rounds,
    insert_images,
    insert_measurements,
    insert_rounds,
)
from analysis.environment import (
    batches as env_batches,
    series as env_series,
    summary as env_summary,
)
from analysis.growth import latest_delta, room_summary, series


class IngestBody(BaseModel):
    """POST /ingest 请求体：outbox 记录批次。"""

    rows: list[dict]


#: 原始分钟序列不落库，按窗口现取；窗口上限给排障用，趋势图请用 hour。
RAW_WINDOW_MAX_HOURS = 6
DEFAULT_TDENGINE_URL = "http://127.0.0.1:6041"


def _span_hours(start: str, end: str | None) -> float:
    first = datetime.fromisoformat(start)
    last = datetime.fromisoformat(end) if end else datetime.now()
    return (last - first).total_seconds() / 3600.0


def create_app(
    db_path: str | None = None,
    *,
    raw_source_factory=None,
    jobs_factory=default_jobs,
) -> FastAPI:
    """建应用。`db_path` 缺省取环境变量 `ANALYSIS_DB`（容器里显式给路径，不靠工作目录）。

    **常驻作业在 lifespan 里跑**（票 12）：API 与 env/audit/batch 三个 ingest
    同一个容器、同一个进程，定时是进程内 asyncio 任务 —— 不靠容器重启，
    也不引 cron/supervisor。`jobs_factory` 只为测试注入：默认
    `analysis.jobs.default_jobs`，而不设周期环境变量时它返回空表 ⇒
    开发与测试里不会有后台网络请求。
    """
    resolved_db = db_path or os.environ.get("ANALYSIS_DB", "mushrooms.db")
    # 单 worker 部署，允许跨线程复用连接（FastAPI 线程池执行 sync 端点）
    conn = connect(resolved_db, check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL")

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        try:
            jobs = list(jobs_factory(conn, resolved_db))
        except Exception as e:  # noqa: BLE001 - 装配失败不许把 API 拖下水
            print(f"[ingest] 作业装配失败，API 照常启动: {e}")
            jobs = []
        tasks = [asyncio.create_task(run_job(job), name=f"ingest:{job.name}") for job in jobs]
        for job in jobs:
            period = (f"每 {job.interval_s}s" if job.interval_s is not None
                      else f"每天 {job.daily_at}")
            print(f"[ingest] 启动作业 {job.name}（{period}）")
        try:
            yield
        finally:
            for task in tasks:
                task.cancel()
            for task in tasks:
                with suppress(asyncio.CancelledError):
                    await task
            if tasks:
                print("[ingest] 作业已停止")

    app = FastAPI(title="mushroom-analysis", lifespan=lifespan)

    @app.get("/healthz")
    def healthz() -> dict:
        return {"ok": True}

    @app.post("/ingest")
    def ingest(body: IngestBody) -> dict:
        """接收 outbox 批次：measurement→measurements，round→rounds，image_index→images。

        字段名契约由 measure.record.RECORD_FIELDS 单方定义（spec §6）；
        行缺字段时按 NULL 入库，未知 kind 忽略。
        **measurement 行必须有 `room_id` 与 `box_id`**（票 11）：缺的行不入库
        （主键 `(ts, room_id, box_id)`，且缺库房的行会污染某个库房的区间表），
        但计入 `skipped_measurements` 返回给发送方，不静默丢。
        """
        measurements, rounds, images = [], [], []
        skipped_measurements = 0
        # 每种 kind 允许的键。**收到表外的键不能静默忽略**：那样发送方以为已经落库，
        # 实际库里没有这一列（巡检手票 13 §3.2 的四个字段就是这样被吞过一次）。
        known = {
            "measurement": set(MEASUREMENT_FIELDS),
            "round": set(ROUND_FIELDS),
            # `yz` 是 patrol 的坐标列表，由 `flatten_image` 摊平成 y/z，不是表列。
            "image_index": set(IMAGE_FIELDS) | {"yz"},
        }
        unknown: set[tuple[str, str]] = set()
        for row in body.rows:
            kind = row.get("kind")
            if kind in known:
                for key in set(row) - known[kind] - {"kind"}:
                    unknown.add((str(kind), str(key)))
            if kind == "measurement":
                # 票 11：测量行必须带库房与框。缺库房的行不入库（主键是
                # (ts, room_id, box_id)，且它会污染某个库房的区间表），
                # 但也不静默丢——计数返回给发送方。
                if row.get("ts") and row.get("room_id") and row.get("box_id"):
                    row.pop("kind", None)
                    measurements.append(row)
                else:
                    skipped_measurements += 1
            elif kind == "round" and row.get("ts"):
                rounds.append(row)
            elif kind == "image_index" and row.get("ts"):
                images.append(row)
        warnings = [
            f"{kind}.{key} 没有对应列，**会被忽略**（先加列再发字段）"
            for kind, key in sorted(unknown)
        ]
        for text in warnings:
            print(f"[ingest] 未知字段：{text}")
        if measurements:
            insert_measurements(conn, measurements)
        if images:
            insert_images(conn, images)
        if rounds:
            insert_rounds(conn, rounds)
        return {
            "inserted_measurements": len(measurements),
            "skipped_measurements": skipped_measurements,
            "inserted_rounds": len(rounds),
            "inserted_images": len(images),
            "warnings": warnings,
        }

    @app.get("/measurements")
    def measurements(room_id: str = "", box_id: str = "") -> list[dict]:
        """测量行（票 11）。可按库房/框过滤 —— 区间表按库房分组，别在内存里筛。"""
        return fetch_measurements(
            conn, room_id=room_id or None, box_id=box_id or None
        )

    @app.get("/rounds")
    def rounds(
        limit: int = 0, room_id: str = "", entry_date: str = "", batch_no: str = ""
    ) -> list[dict]:
        return fetch_rounds(
            conn,
            room_id=room_id or None,
            entry_date=entry_date or None,
            batch_no=batch_no or None,
            limit=limit or None,
        )

    @app.get("/images")
    def images(
        station_id: str = "",
        box_id: str = "",
        room_id: str = "",
        round_id: str = "",
        limit: int = 0,
    ) -> list[dict]:
        """图像索引（ADR-0005）：按站位/框/库房倒序，供巡检台历史模式使用。

        每行带 `room_id`/`entry_date`/`batch_no`——回溯照片时"这属于哪间库房、
        哪批蘑菇"必须能从索引本身读出来（见 db.SCHEMA 里 images 的注释）。
        """
        return fetch_images(
            conn,
            station_id=station_id or None,
            box_id=box_id or None,
            room_id=room_id or None,
            round_id=round_id or None,
            limit=limit or None,
        )

    @app.get("/growth")
    def growth(box_id: str, limit: int = 50) -> dict:
        """某框的逐点对比序列（每个点相对上一个点的判词）。

        判词与阈值口径见 `analysis.growth`：间隔太短/样本太少时**不给结论**，
        而不是硬算一个速率。
        """
        rows = fetch_measurements(conn)
        points = series(rows, box_id, limit=limit or None)
        return {"box_id": box_id, "points": [p.to_row() for p in points],
                "latest": (points[-1].to_row() if points else None)}

    @app.get("/growth/room")
    def growth_room(room_id: str = "", boxes: str = "") -> dict:
        """整间库房的生长判断：每框最新结论 + 判词计数。

        框清单优先取显式传入的 `boxes`（逗号分隔），否则从该库房的图像索引里取
        `distinct box_id` —— 这样"拍了但没测量"的框也能出现在列表里（判词为
        `no_prev`），而不是从页面上悄悄消失。
        """
        rows = fetch_measurements(conn)
        if boxes:
            wanted = [b.strip() for b in boxes.split(",") if b.strip()]
        elif room_id:
            cur = conn.execute(
                "SELECT DISTINCT box_id FROM images WHERE room_id = ? AND box_id IS NOT NULL "
                "ORDER BY box_id", (room_id,))
            wanted = [r[0] for r in cur.fetchall()]
        else:
            wanted = []
        summary = room_summary(rows, boxes=wanted or None)
        summary["room_id"] = room_id or None
        latest = latest_delta(rows, wanted[0]) if len(wanted) == 1 else None
        summary["latest"] = latest.to_row() if latest else None
        return summary

    def _raw_rows(zone: str, start: str, end: str) -> list[dict]:
        """原始分钟序列**不落库**，按窗口现取 TDengine（2026-10-12 定）。"""
        if not start:
            raise HTTPException(status_code=400, detail="raw 粒度必须给 start：原始序列不落库，按窗口现取")
        if _span_hours(start, end or None) > RAW_WINDOW_MAX_HOURS:
            raise HTTPException(
                status_code=400,
                detail=f"raw 窗口最多 {RAW_WINDOW_MAX_HOURS} 小时；趋势图请用 granularity=hour",
            )
        if raw_source_factory is not None:
            source = raw_source_factory(zone)
        else:
            from analysis.env.tdengine import TdengineEnvSource

            source = TdengineEnvSource(
                os.environ.get("TDENGINE_URL", DEFAULT_TDENGINE_URL),
                rooms=[zone] if zone else None,
                user=os.environ.get("TDENGINE_USER", "root"),
                password=os.environ.get("TDENGINE_PASSWORD", "taosdata"),
            )
        rows = [r.__dict__ for r in source.fetch_since(start)]
        if end:
            rows = [r for r in rows if r["ts"] <= end]
        return rows

    @app.get("/environment")
    def environment(
        zone: str = "", start: str = "", end: str = "", granularity: str = "hour"
    ) -> dict:
        """实测环境序列（票 01 入库、票 02 读）。

        `hour`/`day` 读库里的小时聚合；**`raw` 不落库**，按窗口现取 TDengine
        （最长 `RAW_WINDOW_MAX_HOURS` 小时）。补光的 `lamp_state` 是设备状态
        （0=自动 1=手动 2=停止 3=关闭），分类量，聚合取众数（ADR-0021）。
        """
        if granularity == "raw":
            return {
                "granularity": "raw",
                "source": "tdengine",
                "rows": _raw_rows(zone, start, end),
            }
        rows = fetch_environment(
            conn, zone=zone or None, start=start or None, end=end or None
        )
        return {
            "granularity": granularity,
            "source": "aggregate",
            "rows": env_series(
                rows,
                granularity=granularity,
                zone=zone or None,
                start=start or None,
                end=end or None,
            ),
        }

    @app.get("/environment/batches")
    def environment_batches(zone: str = "", limit: int = 3) -> dict:
        """按**生产台账的批次窗口**汇总环境参数，最新在前，供"当前 vs 前两批"对比。

        批次边界只能来自台账：环境是连续采集的，换批不会让传感器停（真机证否过
        "按断档切段"那条路）。台账由 `analysis.batch_ingest` 从 **pgsql** 同步
        （2026-10-14 起；业务库 MySQL 已禁用，旧的 `docker exec tools_mysql` 通路已下线）。
        """
        if not (zone or "").strip():
            raise HTTPException(status_code=400, detail="批次对比要指定 zone（批次按库房算）")
        ledger = db_fetch_batches(conn, room_id=zone)
        if not ledger:
            return {
                "ok": False,
                "error": "还没有批次台账：先跑一次 analysis.batch_ingest",
                "batches": [],
            }
        windows = window_of(ledger, room=zone, limit=max(1, min(limit, 12)))
        rows = fetch_environment(conn, zone=zone)
        kept = [r for r in rows if r.get("ts")]
        return {
            "ok": True,
            "zone": zone,
            "batches": env_batches(rows, windows, zone=zone),
            # 数据实际覆盖到哪天：更早的批次只有窗口没有数据，对比时要看"覆盖天数"
            "data_from": min((r["ts"] for r in kept), default=None),
            "data_to": max((r["ts"] for r in kept), default=None),
        }

    @app.get("/environment/summary")
    def environment_summary(zone: str = "", start: str = "", end: str = "") -> dict:
        """窗口内的极值/分位数、缺口清单与"整列无源"标记（都基于小时聚合）。"""
        rows = fetch_environment(
            conn, zone=zone or None, start=start or None, end=end or None
        )
        return env_summary(
            rows, zone=zone or None, start=start or None, end=end or None
        )

    @app.get("/operations")
    def operations(
        zone: str = "", start: str = "", end: str = "", limit: int = 200
    ) -> dict:
        """操作日志：谁、何时、把哪个点改成了什么。

        **逐条返回，不聚合**——"那天 10:10 谁把右加湿器的开启设定改成 5"
        是运行经验的最小单位，聚合掉就答不出来了。同时给一份按天/设备/点位的计数。
        """
        rows = fetch_operations(
            conn,
            zone=zone or None,
            start=start or None,
            end=end or None,
            limit=max(1, min(limit, 2000)),
        )
        return {
            "ok": True,
            "zone": zone or None,
            "rows": rows,
            "summary": summarize_operations(list(reversed(rows))),
        }

    @app.get("/ranges")
    def ranges() -> dict:
        """最优参数区间表（只读 `ranges` 表；由每日任务 `refresh_ranges` 写入）。

        **形状（票 05，2026-10-14）**：从裸列表改成信封。理由：裸列表表达不了空态 ——
        `[]` 分不清"从没重算过"、"没有测量"、"平视下没有可用长势量"、"样本不足"，
        而这四件事页面要说给用户听、且处置完全不同。所以与 `/operations` 一致给信封：

        - `rows`：区间行（每行带 `metric`/`unit`/`rate_per_h`，速率必须显示量纲）；
        - `state`：`ok` / `never_computed` / `no_measurements` / `all_excluded` /
          `no_environment` / `no_growth_metric` / `insufficient_samples`；
        - `state_text`：人话，页面直接显示；
        - `detail`：计数与排除明细（`n_excluded`、`excluded_by_reason`、前 20 条原文）
          —— **排除必须可见，不静默消失**；
        - `computed_at` / `metric` / `unit`：最近一次重算的戳与量纲。
        """
        state = fetch_ranges_state(conn)
        rows = fetch_ranges(conn)
        return {
            "ok": True,
            "rows": rows,
            "count": len(rows),
            "state": state["state"],
            "state_text": state["state_text"],
            "detail": state["detail"],
            "computed_at": state["computed_at"],
            "metric": state["metric"],
            "unit": state["unit"],
        }

    return app
