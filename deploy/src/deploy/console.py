"""巡检台（console）的只读面：现场看"现在在哪、跑没跑、能不能跑、以前拍过什么"。

## 一条硬约束决定了它的形状

FMC4030 是**单会话**控制器，而且巡检守护在整轮期间一直占着它。所以：

> **守护在跑的时候，console 绝不去连控制器。**

一个只读页面如果每秒去连一次控制器，轻则被拒、重则把守护那一轮踢掉——那会毁掉 60 张图。
因此本模块的状态**主要从取证推**（`runs/*.jsonl` 的逐站心跳、outbox 的 round 汇总），
只有明确空闲时才允许（可选的）控制器读位置。推不出位置时就诚实报 `pos_source: "station"`
（`stations.yaml` 里的目标坐标），而不是编一个"当前位置"。

## 为什么状态接口永不 500

页面每 1 秒轮询一次。任何一个依赖坏掉（控制器没接、room.yaml 写坏、analysis 不通）
都不该让整页变成错误——那样操作者看到的是"系统挂了"，而真相往往只是某个文件没写好。
所以每个字段各自 fail-soft，坏掉的部分带 `error` 文本，其余照常返回。
"""

from __future__ import annotations

import json
import os
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI, Query
from fastapi.responses import JSONResponse
from patrol.room import RoomStateError, load_room_state
from patrol.stations import GRID_ANGLES, grid_geometry, load_stations
from patrol.store import JsonlStore
from pydantic import BaseModel

from deploy.manual import SESSION_TTL_S, ManualChannel
from deploy.patrol_trigger import TriggerStore

#: 心跳超过这么久没更新 ⇒ 不认为"正在巡检"（一轮约 11 分钟，逐站心跳是秒级）
HEARTBEAT_STALE_S = 180.0
#: 最近事件环形缓冲的条数（前端底栏日志）
EVENT_BUFFER = 50


class CmdBody(BaseModel):
    """手动指令请求体。`kind` 走白名单校验，未知指令在执行层被拒（400）。"""

    kind: str
    args: dict | None = None        # 默认 None 而不是 {}：可变默认值是个陷阱
    by: str = "operator"


@dataclass
class ConsoleDeps:
    """console 需要的一切外部东西（都能在测试里替换，不碰硬件）。"""

    room_path: str = "/app/configs/room.yaml"
    stations_path: str = "/app/configs/stations.yaml"
    outbox_path: str = "/app/data/outbox.jsonl"
    runs_dir: str = "/app/data/runs"
    analysis_url: str = "http://172.17.0.1:8000"
    capture_host: str = "172.17.0.1:7003"
    enclosures: dict = field(default_factory=dict)
    # 传输：patrol.links.Transport 形状（部署侧注入；测试里给假的）
    transport: object | None = None
    # 触发请求的落盘目录（跑一轮的入口；见 deploy/patrol_trigger.py）
    trigger_dir: str = "/app/data/trigger"
    # 手动指令的落盘目录（console 只写，执行方 patrol-serve 领走执行）
    cmd_dir: str = "/app/data/cmd"
    now: object = datetime.now

    def envelope(self) -> dict:
        g = grid_geometry()
        return {"y": [g["y_min"], g["y_max"]], "z": [g["z_min"], g["z_max"]]}


def _read_jsonl_tail(path: Path, limit: int = 400) -> list[dict]:
    """读 JSONL 尾部若干行（坏行跳过——取证文件的最后一行可能正被写）。"""
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()[-limit:]
    except OSError:
        return []
    out = []
    for ln in lines:
        ln = ln.strip()
        if not ln:
            continue
        try:
            out.append(json.loads(ln))
        except json.JSONDecodeError:
            continue
    return out


def latest_run(runs_dir: str) -> tuple[dict | None, list[dict]]:
    """最新一轮的取证：返回 `(汇总, 事件列表)`。

    "最新"按文件 mtime 取，不按文件名——文件名里是启动时刻，而一轮可能在跨时刻运行。
    """
    d = Path(runs_dir)
    if not d.is_dir():
        return None, []
    files = sorted(d.glob("*.jsonl"), key=lambda p: p.stat().st_mtime)
    if not files:
        return None, []
    newest = files[-1]
    events = _read_jsonl_tail(newest)
    summary = {
        "file": newest.name,
        "mtime": datetime.fromtimestamp(newest.stat().st_mtime).isoformat(timespec="seconds"),
        "age_s": round(time.time() - newest.stat().st_mtime, 1),
    }
    return summary, events


def patrol_state(runs_dir: str) -> dict:
    """从取证推"现在在不在巡检"，推不出来就说不知道。

    判据是**最后一轮有没有 end 事件** + 心跳是否新鲜：
    有 end ⇒ 结束（带结果）；没 end 且心跳新鲜 ⇒ 正在跑；没 end 但心跳很旧 ⇒
    进程可能死了，这跟"正在跑"是两件事，必须分开报（否则页面会一直显示"巡检中"）。
    """
    summary, events = latest_run(runs_dir)
    if summary is None:
        return {"active": False, "known": False, "reason": "还没有任何一轮取证（runs/ 为空）"}

    end = next((e for e in reversed(events) if e.get("event") == "end"), None)
    stations = [e for e in events if e.get("event") == "station"]
    last_hb = stations[-1] if stations else None
    fresh = summary["age_s"] < HEARTBEAT_STALE_S

    if end is not None:
        return {
            "active": False, "known": True, "file": summary["file"],
            "started_at": _first(events, "start", "ts"),
            "ended_at": end.get("ts") or summary["mtime"],
            "status": end.get("status"),
            "n_results": end.get("n_results"), "n_failures": end.get("n_failures"),
            "aborted": end.get("aborted"),
            "n_stations_seen": len(stations),
            "reason": f"上一轮已结束（{end.get('status')}）",
        }
    if fresh:
        total = last_hb.get("total") if last_hb else None
        return {
            "active": True, "known": True, "file": summary["file"],
            "started_at": _first(events, "start", "ts"),
            "current_station": (last_hb or {}).get("station_id"),
            "station_index": (last_hb or {}).get("index"),
            "station_total": total,
            "n_stations_seen": len(stations),
            "last_heartbeat_age_s": summary["age_s"],
            "reason": "正在巡检（守护占着控制器）",
        }
    return {
        "active": False, "known": True, "file": summary["file"],
        "started_at": _first(events, "start", "ts"),
        "n_stations_seen": len(stations),
        "last_heartbeat_age_s": summary["age_s"],
        "reason": (f"最新一轮没有 end 事件，且心跳已 {summary['age_s']:.0f} 秒没更新 —— "
                   "可能是被中断或进程已死，请看 journal 末行"),
    }


def _first(events: list[dict], kind: str, key: str):
    for e in events:
        if e.get("event") == kind:
            return e.get(key)
    return None


def room_state(room_path: str, *, now=None) -> dict:
    """准入门禁：能不能动、第几天、为什么不动。门禁读不到就明说（这是现场第一疑问）。"""
    now = now or datetime.now()
    try:
        state = load_room_state(room_path)
    except RoomStateError as e:
        return {"ok": False, "allowed": False, "error": str(e),
                "text": "读不到入库日期 ⇒ 不会移动机构（fail-closed）"}
    allowed, reason = state.verdict(now.date())
    return {
        "ok": True, "allowed": allowed, "room_id": state.room_id,
        "entry_date": state.entry_date.isoformat(), "batch_no": state.batch_no,
        "day": state.day_on(now.date()), "min_day": state.min_day, "max_day": state.max_day,
        "text": reason,
    }


def stations_state(stations_path: str) -> dict:
    """站位表 + 网格几何（页面左栏平面图与列表的数据源）。"""
    try:
        stations = load_stations(stations_path)
    except (OSError, ValueError) as e:
        return {"ok": False, "error": f"读取站位表失败：{e}", "stations": [], "grid": None}
    return {
        "ok": True,
        "grid": grid_geometry(),
        "angles": list(GRID_ANGLES),
        "stations": [
            {"id": s.id, "box_id": s.box_id, "y": s.y, "z": s.z, "layer": s.layer,
             "col": s.col, "angle_profile": s.angle_profile, "camera_ip": s.camera_ip,
             "trim_y": s.trim_y, "trim_z": s.trim_z, "trim_note": s.trim_note,
             "target_y": s.target[0], "target_z": s.target[1]}
            for s in stations
        ],
    }


def local_index(outbox_path: str) -> list[dict]:
    """还没同步出去的图像索引（历史模式的"本地待同步"那一半）。"""
    rows = JsonlStore(outbox_path).pending()
    return [r for r in rows if r.get("kind") == "image_index"]


class Console:
    """把上面那些读函数组装成一个可测对象（HTTP 层只做转发与合并）。"""

    def __init__(self, deps: ConsoleDeps):
        self.deps = deps
        self.events: list[dict] = []

    # ---------- 组装 ----------

    def note(self, text: str, level: str = "info") -> None:
        self.events.append({"ts": self.deps.now().isoformat(timespec="seconds"),
                            "level": level, "text": text})
        del self.events[:-EVENT_BUFFER]

    def status(self) -> dict:
        patrol = patrol_state(self.deps.runs_dir)
        room = room_state(self.deps.room_path)
        st = stations_state(self.deps.stations_path)
        current = patrol.get("current_station")
        pos, source = None, "none"
        if current and st.get("ok"):
            hit = next((s for s in st["stations"] if s["id"] == current), None)
            if hit:
                pos, source = [hit["target_y"], hit["target_z"]], "station"

        if patrol.get("active"):
            state = "PATROLLING"
        elif not room.get("ok"):
            state = "UNKNOWN"
        else:
            state = "IDLE"
        return {
            "ts": self.deps.now().isoformat(timespec="seconds"),
            "machine": {
                # connected 只表示"控制器能不能读"，**不代表**它现在空闲：
                # 正在巡检时我们刻意不去连它（单会话设备，第二个连接会踢掉守护）。
                "state": state,
                "connected": source == "controller",
                "real_pos": pos,
                "pos_source": source,
                "probe_suppressed": bool(patrol.get("active")),
                "host": self.deps.capture_host,
            },
            "patrol": patrol,
            "room": room,
            "envelope": self.deps.envelope(),
            "events": self.events[-EVENT_BUFFER:],
        }

    def images(self, *, station_id: str | None = None, limit: int = 200) -> dict:
        """历史图像：合并"本地待同步"与"prod 已同步"两份，每行带 source。"""
        rows = []
        for r in local_index(self.deps.outbox_path):
            if station_id and r.get("station_id") != station_id:
                continue
            rows.append({**r, "source": "local"})
        prod_error = None
        if self.deps.transport is not None:
            q = f"/images?limit={limit}"
            if station_id:
                q += f"&station_id={station_id}"
            try:
                got = self.deps.transport(f"{self.deps.analysis_url}{q}")
                for r in (got or {}).get("rows", []) if isinstance(got, dict) else (got or []):
                    rows.append({**r, "source": "prod"})
            except Exception as e:  # noqa: BLE001 - prod 不通不该让历史页整页打不开
                prod_error = str(e)
        rows.sort(key=lambda r: str(r.get("ts") or ""), reverse=True)
        return {"ok": prod_error is None, "prod_error": prod_error,
                "n_local": sum(1 for r in rows if r["source"] == "local"),
                "n_prod": sum(1 for r in rows if r["source"] == "prod"),
                "rows": rows[:limit]}


def deps_from_env() -> ConsoleDeps:
    """从环境变量装配（容器里的默认路径；现场改 compose 的 environment 即可）。

    `analysis` 的查询走 `HttpxTransport`：这与 patrol 侧"网络只出现在 deploy 包"的
    基线一致，也让 console 的测试不必真的发请求（测试直接注入假的 transport）。
    """
    from deploy.transport import HttpxTransport, host_port

    analysis_url = os.environ.get("PATROL_ANALYSIS", "http://172.17.0.1:8000")
    return ConsoleDeps(
        room_path=os.environ.get("PATROL_ROOM", "/app/configs/room.yaml"),
        stations_path=os.environ.get("PATROL_STATIONS", "/app/configs/stations.yaml"),
        outbox_path=os.environ.get("PATROL_OUTBOX", "/app/data/outbox.jsonl"),
        runs_dir=os.environ.get("PATROL_RUNS", "/app/data/runs"),
        analysis_url=analysis_url,
        capture_host=os.environ.get("PATROL_CAPTURE_HOST", "172.17.0.1:7003"),
        transport=HttpxTransport(allowed_hosts={host_port(analysis_url)}),
        trigger_dir=os.environ.get("PATROL_TRIGGER_DIR", "/app/data/trigger"),
        cmd_dir=os.environ.get("PATROL_CMD_DIR", "/app/data/cmd"),
    )


def create_app(deps: ConsoleDeps | None = None) -> FastAPI:
    deps = deps if deps is not None else deps_from_env()
    console = Console(deps)
    app = FastAPI(title="mushroom-patrol-console")

    @app.get("/healthz")
    def healthz() -> dict:
        return {"ok": True}

    @app.get("/api/status")
    def api_status() -> dict:
        return console.status()

    @app.get("/api/room")
    def api_room() -> dict:
        return room_state(deps.room_path)

    @app.get("/api/stations")
    def api_stations() -> dict:
        return stations_state(deps.stations_path)

    @app.get("/api/grid")
    def api_grid() -> dict:
        return grid_geometry()

    @app.get("/api/images")
    def api_images(station_id: str = "", limit: int = Query(200, ge=1, le=2000)) -> dict:
        return console.images(station_id=station_id or None, limit=limit)

    @app.post("/api/patrol/run", status_code=202)
    def api_patrol_run(reason: str = "", by: str = "scheduler") -> JSONResponse:
        """投一个"跑一轮"的请求：**立刻返回**，绝不等巡检跑完。

        为什么必须快进快出：调用方是算法工程的 APScheduler（`max_instances=1`、
        `misfire_grace_time=300s`），而我们一轮 11 分钟——job 里等结果会让下一次触发
        被判 misfire 丢掉。同一时刻只允许一个待处理请求，重复触发回 409 + 现有请求，
        **不排队**（排队会让两次挤在一起连跑 22 分钟）。
        """
        store = TriggerStore(dir_path=deps.trigger_dir, now=deps.now)
        req, created = store.request(by=by, reason=reason)
        body = {"accepted": created, "job": {**asdict(req), "pending": req.pending},
                "pending_age_s": store.age_s(req)}
        if not created:
            console.note(f"重复触发被拒（{req.id} 已在待处理）", level="warn")
            return JSONResponse(body, status_code=409)
        console.note(f"收到巡检请求 {req.id}（来自 {by}{'：' + reason if reason else ''}）")
        return JSONResponse(body, status_code=202)

    @app.get("/api/patrol/run")
    def api_patrol_run_state() -> dict:
        """这个请求现在什么状态（调度器与被触发的执行方都看它）。"""
        store = TriggerStore(dir_path=deps.trigger_dir, now=deps.now)
        req = store.current()
        return {"job": {**asdict(req), "pending": req.pending} if req else None,
                "pending": bool(req and req.pending),
                "age_s": store.age_s(req),
                "stale": store.is_stale(req)}

    # ---------- 手动控制：会话 / 指令 / 急停（ADR-0013 的语义） ----------

    def channel() -> ManualChannel:
        return ManualChannel(deps.cmd_dir, now=deps.now)

    @app.post("/api/session", status_code=201)
    def api_session_open() -> dict:
        """接管：开一个会话。巡检期间也能开，但**指令会被执行方按 ADR-0013 拒绝**——
        轮内不动机构，轮间随时可用（约 94% 的时间）。"""
        s = channel().open_session(uuid.uuid4().hex[:12])
        console.note(f"操作者接管（会话 {s.token}，{SESSION_TTL_S}s）")
        return {"session": asdict(s), "ttl_s": SESSION_TTL_S}

    @app.delete("/api/session")
    def api_session_close() -> dict:
        """放开会话。**真正的"先回零再放开"由执行方完成**（它才碰得到控制器）：
        这里只撤销授权，并把回零排成一条指令。"""
        ch = channel()
        ch.close_session()
        console.note("操作者已放开会话")
        return {"closed": True}

    @app.post("/api/stop")
    def api_stop(reason: str = "") -> dict:
        """急停：独立标志文件，**不排队、不需要会话、不受任何规则限制**。

        延迟上限约等于执行方的急停检查间隔（1 秒），不是实时回路——这一点写在
        deploy/manual.py 的模块注释里，别当它是安全回路。
        """
        channel().raise_estop(by="operator", reason=reason)
        console.note(f"急停已置位（{reason or '操作者按下'}）", level="warn")
        return {"estop": True}

    @app.delete("/api/stop")
    def api_stop_clear() -> dict:
        """复位急停。刻意做成独立动作：急停不该被"下一条指令"顺带清掉。"""
        channel().clear_estop()
        console.note("急停已复位")
        return {"estop": False}

    @app.get("/api/cmd")
    def api_cmd_state() -> dict:
        """当前指令与最近一次结果（页面按这个轮询）。"""
        ch = channel()
        inflight = ch.inflight()
        result = ch.result()
        return {"inflight": asdict(inflight) if inflight else None,
                "result": asdict(result) if result else None,
                "estop": ch.raised(),
                "session_active": ch.active_session() is not None}

    @app.post("/api/cmd", status_code=202)
    def api_cmd(body: CmdBody) -> JSONResponse:
        """提交一条手动指令（goto/jog/abs/home/lamp/capture）。

        **异步**：这里只把指令写进共享目录就返回 202，执行方（持有控制器的那个进程）
        领走后写回结果。之所以不是同步的：控制器是单会话设备，console 一旦自己去连，
        就可能把正在跑的那一轮巡检踢掉。页面按 `GET /api/cmd` 轮询结果。
        """
        ch = channel()
        active = ch.active_session()
        try:
            cmd, why = ch.submit(body.kind, args=body.args or {}, by=body.by or "operator",
                                 session=active.token if active else "")
        except PermissionError as e:
            return JSONResponse({"error": str(e), "need_session": True}, status_code=403)
        except ValueError as e:
            return JSONResponse({"error": str(e)}, status_code=400)
        if cmd.kind != body.kind:
            return JSONResponse({"error": why, "busy_with": asdict(cmd)}, status_code=409)
        console.note(f"指令 {cmd.id} {cmd.kind} {cmd.args or ''}", level="info")
        return JSONResponse({"command": asdict(cmd), "detail": why}, status_code=202)

    @app.get("/api/events")
    def api_events() -> JSONResponse:
        return JSONResponse({"events": console.events[-EVENT_BUFFER:]})

    return app
