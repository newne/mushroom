"""prod 接收 API（票 06）：巡检结果入库 + 健康检查。

单 worker 部署（uvicorn --workers 1），与截图服务同样的串行约束。
运行::

    uvicorn analysis.api:create_app --factory --host 0.0.0.0 --port 8000
"""

from __future__ import annotations

from fastapi import FastAPI
from pydantic import BaseModel

from analysis.db import (
    connect,
    fetch_images,
    fetch_measurements,
    fetch_ranges,
    insert_images,
    insert_measurements,
)
from analysis.growth import latest_delta, room_summary, series


class IngestBody(BaseModel):
    """POST /ingest 请求体：outbox 记录批次。"""

    rows: list[dict]


#: rounds 表的列（含库房维度——patrol 的 round 行一直带着它们，
#: 表里没有的话会在这一跳被静默丢掉，见 db.SCHEMA 的注释）。
ROUND_FIELDS = ("ts", "ok", "n_results", "n_failures", "aborted",
                "room_id", "entry_date", "batch_no")


def create_app(db_path: str = "mushrooms.db") -> FastAPI:
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
    def rounds(limit: int = 0) -> list[dict]:
        sql = f"SELECT {', '.join(ROUND_FIELDS)} FROM rounds ORDER BY ts"
        if limit:
            sql += " LIMIT ?"
        cur = conn.execute(sql, (int(limit),) if limit else ())
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, r)) for r in cur.fetchall()]

    @app.get("/images")
    def images(station_id: str = "", box_id: str = "", room_id: str = "",
               limit: int = 0) -> list[dict]:
        """图像索引（ADR-0005）：按站位/框/库房倒序，供巡检台历史模式使用。

        每行带 `room_id`/`entry_date`/`batch_no`——回溯照片时"这属于哪间库房、
        哪批蘑菇"必须能从索引本身读出来（见 db.SCHEMA 里 images 的注释）。
        """
        return fetch_images(conn, station_id=station_id or None, box_id=box_id or None,
                            room_id=room_id or None, limit=limit or None)

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

    @app.get("/ranges")
    def ranges() -> list[dict]:
        """最佳控制参数区间表（只读 ranges 表；由每日批处理 refresh_ranges 写入）。"""
        return fetch_ranges(conn)

    return app
