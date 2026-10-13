"""prod 接收 API（票 06）：巡检结果入库 + 健康检查。

单 worker 部署（uvicorn --workers 1），与截图服务同样的串行约束。
运行::

    uvicorn analysis.api:create_app --factory --host 0.0.0.0 --port 8000
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from analysis.audit import summarize_operations
from analysis.batches import window_of
from analysis.db import (
    ROUND_FIELDS,
    connect,
    fetch_batches as db_fetch_batches,
    fetch_environment,
    fetch_operations,
    fetch_images,
    fetch_measurements,
    fetch_ranges,
    fetch_rounds,
    insert_images,
    insert_measurements,
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


def create_app(db_path: str = "mushrooms.db", *, raw_source_factory=None) -> FastAPI:
    app = FastAPI(title="mushroom-analysis")
    # 单 worker 部署，允许跨线程复用连接（FastAPI 线程池执行 sync 端点）
    conn = connect(db_path, check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL")

    @app.get("/healthz")
    def healthz() -> dict:
        return {"ok": True}

    @app.post("/ingest")
    def ingest(body: IngestBody) -> dict:
        """接收 outbox 批次：measurement→measurements，round→rounds，image_index→images。

        字段名契约由 measure.record.RECORD_FIELDS 单方定义（spec §6）；
        行缺字段时按 NULL 入库，未知 kind 忽略。
        """
        measurements, rounds, images = [], [], []
        for row in body.rows:
            kind = row.get("kind")
            if kind == "measurement" and row.get("ts") and row.get("box_id"):
                row.pop("kind", None)
                measurements.append(row)
            elif kind == "round" and row.get("ts"):
                rounds.append(row)
            elif kind == "image_index" and row.get("ts"):
                images.append(row)
        if measurements:
            insert_measurements(conn, measurements)
        if images:
            insert_images(conn, images)
        if rounds:
            cols = ", ".join(ROUND_FIELDS)
            marks = ", ".join(f":{f}" for f in ROUND_FIELDS)
            conn.executemany(
                f"INSERT OR REPLACE INTO rounds ({cols}) VALUES ({marks})",
                [{f: r.get(f) for f in ROUND_FIELDS} for r in rounds],
            )
            conn.commit()
        return {
            "inserted_measurements": len(measurements),
            "inserted_rounds": len(rounds),
            "inserted_images": len(images),
        }

    @app.get("/measurements")
    def measurements(box_id: str = "") -> list[dict]:
        rows = fetch_measurements(conn)
        return [r for r in rows if not box_id or r.get("box_id") == box_id]

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
        "按断档切段"那条路）。台账由 `analysis.batch_ingest` 从 `mogu.mo_gu_batch` 同步。
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
    def ranges() -> list[dict]:
        """最佳控制参数区间表（只读 ranges 表；由每日批处理 refresh_ranges 写入）。"""
        return fetch_ranges(conn)

    return app
