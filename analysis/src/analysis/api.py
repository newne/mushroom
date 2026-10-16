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

from analysis import store
from analysis.audit import summarize_operations
from analysis.batches import dedupe_by_identity, window_of
from analysis.jobs import default_jobs, run_job
# 字段清单是**契约常量**（单一实现处是 `analysis.schema`），不是数据访问：
# 数据访问一律经 `analysis.store`。
from analysis.schema import IMAGE_FIELDS, MEASUREMENT_FIELDS, ROUND_FIELDS
from analysis.environment import (
    batches as env_batches,
    series as env_series,
    summary as env_summary,
)
from analysis.growth import latest_delta, room_summary, series
from analysis.rooms import canonical_room, canonical_rooms, display_room, resolve_rooms
from analysis.setpoints import SetpointError, daily_states_from_db
from analysis.timeline import axis_of, coverage_points, legacy_round_count, matrix_of

#: 台账物化缓存的陈旧阈值（小时）。刷一次是每天 05:20（票 12 的 `BATCH_AT`）⇒ 26h 留 2h 余量。
DEFAULT_LEDGER_STALE_AFTER_H = 26.0


def _stale_after_h() -> float:
    raw = (os.environ.get("BATCH_STALE_AFTER_H") or "").strip()
    try:
        value = float(raw)
    except ValueError:
        return DEFAULT_LEDGER_STALE_AFTER_H
    return value if value > 0 else DEFAULT_LEDGER_STALE_AFTER_H


def _ledger_is_stale(refreshed_at, *, now: datetime | None = None) -> bool:
    """**从没刷过 ⇒ 陈旧**；时间戳读不出来 ⇒ 也陈旧（fail-closed）。

    "没人刷"必须长得跟"刷过"不一样 —— 否则准入会拿一份永远陈旧的表当真值。
    """
    if not refreshed_at:
        return True
    try:
        stamp = datetime.fromisoformat(str(refreshed_at))
    except ValueError:
        return True
    return ((now or datetime.now()) - stamp).total_seconds() > _stale_after_h() * 3600


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
    *,
    store_dsn: str | None = None,
    store_schema: str | None = None,
    raw_source_factory=None,
    jobs_factory=default_jobs,
) -> FastAPI:
    """建应用。

    **存储只有一条路**（票 28 contract）：经 `analysis.store` 连 PG。连接串按
    `store_dsn` → `PGSQL_DSN_FILE`（文件优先）→ `PGSQL_DSN` 解析，schema 按
    `store_schema` → `ANALYSIS_PG_SCHEMA` → `analysis`；缺了连接串就报
    `StoreConfigError`。**没有"退回本地文件库"的分支**（用户 2026-10-15 裁定：
    严禁本地文件数据库），也没有可关闭的后端开关。

    **常驻作业在 lifespan 里跑**（票 12）：API 与 env/audit/batch 三个 ingest
    同一个容器、同一个进程，定时是进程内 asyncio 任务 —— 不靠容器重启，
    也不引 cron/supervisor。`jobs_factory` 只为测试注入：默认
    `analysis.jobs.default_jobs`，而不设周期环境变量时它返回空表 ⇒
    开发与测试里不会有后台网络请求。
    """
    # 单 worker 部署，一份连接同时给请求线程与 lifespan 里的作业线程用：
    # psycopg 连接本身线程安全（同一连接上的操作会串行化）。
    conn = store.connect(dsn=store_dsn, schema=store_schema)

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        try:
            jobs = list(jobs_factory(conn))
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
    # 这份连接是**谁建谁关**：挂到 `app.state` 上，调用方（生产是进程退出、测试是夹具收尾）
    # 才拿得到它。2026-10-16 实测：不挂出来时测试夹具只能等 GC 回收 —— 每条用例攒一份，
    # 跑到后面撞上开发库 `max_connections=100`（"sorry, too many clients already"）。
    app.state.store_conn = conn

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
            store.insert_measurements(conn, measurements)
        if images:
            store.insert_images(conn, images)
        if rounds:
            store.insert_rounds(conn, rounds)
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
        return store.fetch_measurements(
            conn, room_id=room_id or None, box_id=box_id or None
        )

    @app.get("/rounds")
    def rounds(
        limit: int = 0, room_id: str = "", entry_date: str = "", batch_no: str = ""
    ) -> list[dict]:
        return store.fetch_rounds(
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
        return store.fetch_images(
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
        rows = store.fetch_measurements(conn)
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
        rows = store.fetch_measurements(conn)
        if boxes:
            wanted = [b.strip() for b in boxes.split(",") if b.strip()]
        elif room_id:
            # 框清单从图像索引来（「拍了但没测量」的框也要出现）：走门面，不写裸 SQL ——
            # `?` 占位符与直写表名都是 SQLite 专有（票 28 第 2 步的挡路点 2）。
            wanted = store.fetch_room_box_ids(conn, room_id=room_id)
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
        rows = store.fetch_environment(
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

        **库房号跨域 join（Chief 2026-10-14）**：台账侧的旧编号（`7`/`8`）与
        `environment.zone`（`607`/`608`）不是一套编码。这里按**显式别名表**归一，
        解不出的行写 NULL + 计数可见（`room_alias`）、**不猜**；每批还带 `room_label`
        让页面能显示「`607`（原始 `7`）」，不许只给一个数字让人以为是同一编码。
        """
        if not (zone or "").strip():
            raise HTTPException(status_code=400, detail="批次对比要指定 zone（批次按库房算）")
        want = canonical_room(zone)
        if want is None:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"{zone!r} 不是规范库房号（{'/'.join(canonical_rooms())}），"
                    "也不是别名表里登记过的旧编号"
                ),
            )
        # 整份台账都要看：只按 zone 过滤会把"旧编号挂着的那几批"当成"没有台账"
        ledger, alias_report = resolve_rooms(store.fetch_batches(conn), label="/environment/batches")
        if not ledger:
            return {
                "ok": False,
                "error": "还没有批次台账：先跑一次 analysis.batch_ingest",
                "batches": [],
            }
        # 同一 (规范库房号, 入库日期) 出现两次时只留一行（换口径过渡期的重复卡）
        ledger, dupe_report = dedupe_by_identity(ledger)
        windows = window_of(
            ledger, room=want, limit=max(1, min(limit, 12)), key="room_id_canonical"
        )
        if not windows:
            # 解不出的编号要点名说出来：不能让"旧编号"看起来像"这个库房没有批次"
            unresolved = alias_report["unresolved_values"]
            hint = (
                "台账里存在未解出的库房编号 "
                + ", ".join(f"{k!r}×{v}" for k, v in sorted(unresolved.items()))
                + "（未登记别名 ⇒ 不猜）；"
                if unresolved
                else ""
            )
            return {
                "ok": False,
                "zone": zone,
                "error": f"{hint}台账里没有库房 {want} 的批次",
                "batches": [],
                "room_alias": alias_report,
            }
        rows = store.fetch_environment(conn, zone=want)
        kept = [r for r in rows if r.get("ts")]
        return {
            "ok": True,
            "zone": want,
            "zone_requested": zone,
            "batches": [
                {**b, "room_label": display_room(b.get("room_id"))}
                for b in env_batches(rows, windows, zone=want)
            ],
            "room_alias": alias_report,
            "duplicates": dupe_report,
            # 数据实际覆盖到哪天：更早的批次只有窗口没有数据，对比时要看"覆盖天数"
            "data_from": min((r["ts"] for r in kept), default=None),
            "data_to": max((r["ts"] for r in kept), default=None),
        }

    @app.get("/batches")
    def batches(room_id: str = "", limit: int = 24) -> dict:
        """**只读物化批次台账**（Chief 2026-10-14）：给巡检 `fetch_room` 的准入依据。

        为什么单开这个端点：`/environment/batches` 是"按批次窗口汇总环境"，要 `zone`、
        返回的是环境统计，**没有台账新鲜度**；准入要的是"这个库房有哪些批次 + 这份台账
        是不是新鲜的"。不新增 service/端口，复用同一个 API 进程。

        字段约定（**别自己拼**）：

        - `batches[]`：`entry_date`、`batch_id`（`<规范库房号>|<入库日期>`，ADR-0020）、
          `batch_no`（**派生显示名** `<规范库房号>_<YYYYMMDD>`，与 `batch_yield_service`
          的写法一致）、`display_name`（映射来的业务名 `mogu-*`，**缺映射 ⇒ NULL，不编**）、
          `until`（下一批入库日，开区间右端；末批 NULL）；
        - `ledger_refreshed_at`：物化缓存最近一次**成功替换**的时间（`batches_state`）；
        - `stale`：`now - refreshed_at > stale_after_h`（默认 26h，`BATCH_STALE_AFTER_H` 可调）
          —— **从没刷过 ⇒ `stale=true`**（不假装新鲜）；
        - `room_alias` / `duplicates`：解不出的编号点名 + 计数、重复身份去重计数。

        **消费方注意**：`stale=true` 时这份台账**不该拿来判准入**（要判也得显式记下来）。
        """
        if not (room_id or "").strip():
            raise HTTPException(status_code=400, detail="要指定 room_id（台账按库房给）")
        want = canonical_room(room_id)
        if want is None:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"{room_id!r} 不是规范库房号（{'/'.join(canonical_rooms())}），"
                    "也不是别名表里登记过的旧编号"
                ),
            )
        ledger, alias_report = resolve_rooms(store.fetch_batches(conn), label="/batches")
        ledger, dupe_report = dedupe_by_identity(ledger)
        mine = sorted(
            (b for b in ledger if b.get("room_id_canonical") == want),
            key=lambda b: str(b.get("entry_date") or ""),
        )
        out: list[dict] = []
        for i, batch in enumerate(mine):
            nxt = mine[i + 1].get("entry_date") if i + 1 < len(mine) else None
            out.append({
                "room_id": want,
                "room_id_raw": batch.get("room_id"),
                "room_label": display_room(batch.get("room_id")),
                "entry_date": batch.get("entry_date"),
                "batch_id": batch.get("batch_id"),
                "batch_no": f"{want}_{str(batch.get('entry_date') or '').replace('-', '')}",
                "display_name": batch.get("batch_no"),
                "until": nxt,
            })
        out.reverse()                                  # 最新在前
        state = store.fetch_batches_state(conn)
        stale = _ledger_is_stale(state.get("refreshed_at"))
        if stale:
            print(
                f"[batches] 台账不新鲜：最后刷新 {state.get('refreshed_at') or '从未'}，"
                f"阈值 {_stale_after_h()}h —— 消费方不要拿它判准入"
            )
        return {
            "ok": True,
            "room_id": want,
            "room_id_requested": room_id,
            "room_label": display_room(room_id),
            "batches": out[: max(1, min(limit, 200))],
            "n_batches": len(out),
            "ledger_refreshed_at": state.get("refreshed_at"),
            "ledger_n_rows": state.get("n_rows"),
            "ledger_n_pruned": state.get("n_pruned"),
            "stale": stale,
            "stale_after_h": _stale_after_h(),
            "room_alias": alias_report,
            "duplicates": dupe_report,
        }


    @app.get("/timeline")
    def timeline(
        zone: str = "",
        station_id: str = "",
        metric: str = "coverage",
        batch_id: str = "",
        entry_date: str = "",
    ) -> dict:
        """本批「每轮 × 站位」的覆盖轨迹：**不给 `station_id` = 全部站位的矩阵**（票 03），
        给了就是那一个站位的明细（票 02）；metric 只认 `coverage`。

        现场要回答的是"这一批从时间维度看，每个节点哪几轮拍到了、哪几轮断了"，
        所以应答是**一格一轮**的轨迹，不是聚合数字：

        - `batch`：`room_id` / `entry_date` / `batch_no`（身份是 ADR-0020 的
          「库房号 + 入库日期」；`batch_no` 是派生显示名，缺台账行就如实给 null，**不编**）；
        - `axis`：`t0` / `t1` / `n_rounds`——网格首末轮的**真实时间戳**，页面按它定位 x；
        - `rounds[]`（**只有矩阵形态有**）：本批逐轮的**共享时间轴**（`ts` / `round_id` / `day`）；
        - `nodes[]`：站位一行（`node_id` / `layer` / `col` / `box_id` / `points[]`），
          按**与平面图同一套 col/layer 顺序**排（层升序、层内框升序）；
        - `points[]`：明细形态每格一个对象（`ts` / `round_id` / `day` / `has_frame` / `n_frames`）；
          矩阵形态**与 `rounds[i]` 一一对应**（`has_frame` / `n_frames`）——48 站 × 16 天 × 8 轮
          逐点重复 `ts`+`round_id` 会把单次响应抬到约 690 KB，而验收 4 要求 ≤500 KB；
        - `notes[]`：数据侧说明（空批、本批一张帧都没有、无法归属的旧轮次）。

        三条硬规则里，本端点负责把前两条**做成数据**（第三条"零前端依赖"在页面上）：

        1. 每个格点的 x 由**真实时间戳**定位：明细形态的格点自带 `ts`，矩阵形态的格点对齐
           `rounds[i].ts`（下标只是对齐键，**不是 x**）。`day`（生长天数，入库当天 = 0）由
           `entry_date` 派生；应答里**没有**任何"第几个点"的序号。
        2. `null ≠ 0`：这一轮这一站没有帧 ⇒ `has_frame=false` 且 **`n_frames` 为 null**
           （不是 0）。"没拍到"是缺口（页面断开、画灰），"拍到 0 张"这件事不存在。
        3. `layer` / `col` 由站位 id 的编码派生（`S{层}{框:02d}`，见 `analysis.timeline.cell_of`）；
           解析不出来就是 null（**不猜**），页面按"不知道在哪格"画。

        **批次口径**：`batch_id`（`<库房号>|<入库日期>`，ADR-0020）→ `entry_date` →
        台账（`/batches` 的当前批 = 这个库房最新的一批）。过滤直接用 `rounds.entry_date`，
        **不用**"今天减入库日"这类推算——批次身份在台账与轮次行上，不在时钟上。

        **纯读**：只 SELECT `rounds` / `images` / `batches`；不写任何表、不碰 TDengine
        （ADR-0026：只读源）。`metric=growth` 是 P1，等测量管线接通再开（现在给 400，
        不返回一条假的零增长线）。
        """
        if (metric or "").strip() != "coverage":
            raise HTTPException(
                status_code=400,
                detail=(f"metric={metric!r} 还不支持：P0 只认 coverage"
                        "（长势 growth 等测量管线接通再开，P1）"),
            )
        # 给了 station_id = 那一个站位的明细；不给 = **本批全部站位的矩阵**（票 03）。
        # 这是 spec 的规范调用式（`GET /timeline?zone=…&batch_id=…&metric=coverage`），
        # 页面因此**一次请求**就能拿到 48 站的逐轮格，不发 48 次。
        station = (station_id or "").strip()
        raw_zone = (zone or "").strip()
        if not raw_zone:
            raise HTTPException(status_code=400, detail="要指定 zone（轨迹按库房算）")
        want = canonical_room(raw_zone)
        if want is None:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"{raw_zone!r} 不是规范库房号（{'/'.join(canonical_rooms())}），"
                    "也不是别名表里登记过的旧编号"
                ),
            )
        rooms = [raw_zone]
        batch_no = None
        if (batch_id or "").strip():
            left, sep, right = batch_id.strip().partition("|")
            entry = (right if sep else left).strip()[:10]
            if sep and left.strip() != raw_zone and canonical_room(left) != want:
                raise HTTPException(
                    status_code=400,
                    detail=f"batch_id={batch_id!r} 的库房号与 zone={raw_zone!r} 不是同一间",
                )
            if not entry:
                raise HTTPException(
                    status_code=400,
                    detail="batch_id 里没有入库日期：写法是 `<库房号>|<YYYY-MM-DD>`（ADR-0020）",
                )
        elif (entry_date or "").strip():
            entry = entry_date.strip()[:10]
        else:
            # 当前批 = 台账里这个库房**最新**的一批（与 /batches 同一份台账、同一个口径）
            ledger, _alias = resolve_rooms(store.fetch_batches(conn), label="/timeline")
            ledger, _dupes = dedupe_by_identity(ledger)
            mine = [b for b in ledger
                    if b.get("room_id_canonical") == want and b.get("entry_date")]
            if not mine:
                raise HTTPException(
                    status_code=400,
                    detail=(f"台账里没有库房 {want} 的批次（先跑 analysis.batch_ingest），"
                            "也可以直接给 batch_id 或 entry_date"),
                )
            newest = max(mine, key=lambda b: str(b["entry_date"]))
            entry = str(newest["entry_date"])[:10]
            batch_no = newest.get("batch_no")
            # 台账可能挂旧编号（`7`/`8` 与 `607`/`608` 不是一套编码）：两套房间号都收，
            # 否则会"批次数得出来、轮次一条都查不到"。
            raw_batch_room = str(newest.get("room_id") or "").strip()
            if raw_batch_room and raw_batch_room not in rooms:
                rooms.append(raw_batch_room)

        rounds: list[dict] | None = None
        if station:
            rows = store.fetch_round_coverage(
                conn, room_ids=rooms, entry_date=entry, station_id=station
            )
            points = coverage_points(rows, entry_date=entry)
            nodes = [{
                "node_id": station,
                "box_id": next((r.get("box_id") for r in rows if r.get("box_id")), None),
                "points": points,
            }]
        else:
            rows = store.fetch_station_coverage_matrix(
                conn, room_ids=rooms, entry_date=entry
            )
            matrix = matrix_of(rows, entry_date=entry)
            rounds, nodes = matrix["rounds"], matrix["nodes"]
        # 旧轮次数按 ts 去重：矩阵是「轮 × 站」一行一轮，直接数行数会把一轮数成 48 轮
        n_excluded = legacy_round_count(rows)
        notes: list[str] = []
        if not rows:
            notes.append(
                f"本批（{want} {entry}）还没有巡检轮次：轨迹要等这一批有轮次之后才有内容"
            )
        elif rounds is not None and not nodes:
            notes.append(
                f"本批（{want} {entry}）一轮都没有帧：没有站位能排进矩阵——**不拿空站位凑数**"
            )
        if n_excluded:
            notes.append(
                f"{n_excluded} 轮没有 round_id（旧轮次）：帧挂不到它头上，未进网格——**不猜**"
            )
        if batch_no is None:
            # 没走台账（调用方直接点了批次/日期）时用轮次行上的派生名；一个都没有 ⇒ null
            batch_no = next((r.get("batch_no") for r in rows if r.get("batch_no")), None)
        out = {
            "ok": True,
            "metric": "coverage",
            "zone": want,
            "zone_requested": raw_zone,
            # 矩阵形态没有"某一个站位"：null = 本批全部站位（页面按 layer/col 排矩阵）
            "station_id": station or None,
            "batch": {"room_id": want, "entry_date": entry, "batch_no": batch_no},
            "axis": axis_of(rounds if rounds is not None else nodes[0]["points"]),
        }
        if rounds is not None:
            # 共享时间轴只给矩阵形态：明细形态的每个格点自带 ts（票 02 的契约一个字不动）
            out["rounds"] = rounds
        out["nodes"] = nodes
        out["notes"] = notes
        return out

    @app.get("/environment/summary")
    def environment_summary(zone: str = "", start: str = "", end: str = "") -> dict:
        """窗口内的极值/分位数、缺口清单与"整列无源"标记（都基于小时聚合）。"""
        rows = store.fetch_environment(
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
        rows = store.fetch_operations(
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

    @app.get("/setpoints/daily")
    def setpoints_daily(
        zone: str = "", start: str = "", end: str = "", entry_date: str = ""
    ) -> dict:
        """每日设定末态（票 09）：`(天 × 设备 × 点位) → 当天运行在什么设定上`。

        事件源是 **`operations`**（逐条，`GET /operations` 那条链）；`device_setpoint_changes`
        只作交叉验证（ADR-0022），不在这里读。

        三态**必须分得开**（票 09 验收原话）：

        - `changed`：当天有变更 ⇒ 末态 = 当天最后一条的 `value`；
        - `carried`：当天没改 ⇒ **沿用**更早那条（`since`/`since_day` 指出是哪天的）——
          这是"一直在用某个设定"，不是"不知道"；
        - `unknown`：从没见过该点位的变更 ⇒ `value` 写 **NULL**（**不写 0、不填默认**）。

        窗口**起点之前**的基线会一并取（否则第一天全是 `unknown`，"沿用"被错报成"未知"）。
        `entry_date` 给了批次入库日就并列两个生长天数：`growth_day`（入库当天 = 0）与
        `growth_day_prod`（入库当天 = `01`）+ `first_day`（票 19：三值并列、不平均、不合并；
        首日剔除只发生在统计/学习侧，**这里一条原始行都不删**）。
        """
        zone = (zone or "").strip()
        if not zone:
            raise HTTPException(status_code=400, detail="每日设定末态要指定 zone（按库房算）")
        if not (start and end):
            raise HTTPException(status_code=400, detail="要给 start 与 end（YYYY-MM-DD）")
        try:
            result = daily_states_from_db(
                conn, zone=zone, start=start, end=end, entry_date=entry_date or None
            )
        except SetpointError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {
            "ok": True,
            "zone": zone,
            "start": start,
            "end": end,
            "entry_date": entry_date or None,
            "days": result["days"],
            "rows": result["rows"],
            "summary": result["summary"],
        }

    @app.get("/ranges")
    def ranges() -> dict:
        """最优参数区间表（只读 `ranges` 表；由每日任务 `refresh_ranges` 写入）。

        **形状（票 05，2026-10-14）**：从裸列表改成信封。理由：裸列表表达不了空态 ——
        `[]` 分不清"从没重算过"、"没有测量"、"平视下没有可用长势量"、"样本不足"，
        而这四件事页面要说给用户听、且处置完全不同。所以与 `/operations` 一致给信封：

        - `rows`：区间行（每行带 `metric`/`unit`/`rate_per_h`，速率必须显示量纲；
          另带 `tier`（`verified`/`estimate`）、`unverified`、`n_verified`/`n_estimate`
          —— 同一分箱里两类行**计数分别可见**，统计只来自 `tier` 那一档）；
        - `state`：`ok` / `never_computed` / `no_measurements` / `all_excluded` /
          `no_environment` / `no_growth_metric` / `insufficient_samples` /
          **`estimate_only`（仅估算，票 26）**；
        - `state_text`：人话，页面直接显示；
        - `flags`：**整维标注**（票 26 / ADR-0025）。只有估算档时给 `["unverified"]`，
          页面照它显示「**估算（未验证）**」—— 标注进 flags，不另开状态词；
        - `detail`：计数与排除明细（`n_excluded`、`excluded_by_reason`、前 20 条原文、
          两档的 `tiers` 明细）—— **排除必须可见，不静默消失**；
        - `computed_at` / `metric` / `unit`：最近一次重算的戳与量纲。

        ⚠️ **建议层不许读这个响应**：它含估算行。建议层的入口是
        `analysis.align.advice_input` / `analysis.store.fetch_advice_ranges`（只给已验证档，带断言）。
        """
        state = store.fetch_ranges_state(conn)
        rows = store.fetch_ranges(conn)
        return {
            "ok": True,
            "rows": rows,
            "count": len(rows),
            "state": state["state"],
            "state_text": state["state_text"],
            "flags": state.get("flags") or [],
            "detail": state["detail"],
            "computed_at": state["computed_at"],
            "metric": state["metric"],
            "unit": state["unit"],
        }

    return app
