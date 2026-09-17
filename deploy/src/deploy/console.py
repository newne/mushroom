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
from collections.abc import AsyncIterator
from contextlib import AsyncExitStack, asynccontextmanager
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from urllib.parse import urlencode

from fastapi import FastAPI, Query
from fastapi.responses import JSONResponse, Response, StreamingResponse
from patrol.room import RoomStateError, load_room_state
from patrol.stations import GRID_ANGLES, grid_geometry, load_stations
from patrol.store import JsonlStore
from pydantic import BaseModel

from deploy.manual import ALL_KINDS, SESSION_TTL_S, ManualChannel, estop_allows
from deploy.patrol_trigger import TriggerStore

#: 心跳超过这么久没更新 ⇒ 不认为"正在巡检"（一轮约 11 分钟，逐站心跳是秒级）
HEARTBEAT_STALE_S = 180.0
#: 最近事件环形缓冲的条数（前端底栏日志）
EVENT_BUFFER = 50

#: 巡检进行中被拒的指令：**除急停以外的全部**（ADR-0013）。
#: 为什么连补光灯和抓拍也拒：轮内机构和相机链路都归那一轮——插一张抓拍会进历史、
#: 抢一次采图会话，而灯被人拨一下会打乱那一站的曝光窗口。
MANUAL_GATED_KINDS = frozenset(ALL_KINDS) - {"stop"}

#: 预览流开着时，每转发这么多块就回头看一眼"巡检开始了没"（ADR-0017 §4 的服务端兜底）
PREVIEW_RECHECK_CHUNKS = 8

#: 运动进度超过这么久没更新 ⇒ 执行方多半已经不在写它了（进程死了/动作卡死），
#: 页面再显示"实时位置"就是把残值当现在——宁可没有，不要假的。
PROGRESS_STALE_S = 5.0


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
    # 实时预览上游（ADR-0017：浏览器只连 console，相机地址与口令不进页面）
    preview_url: str = "http://mushroom_preview:8003"
    # 预览上游的打开器：`(path) -> async with 得 httpx 响应`（测试注入假的，不碰网络）
    preview_open: object | None = None
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


#: 一轮的典型时长（秒）。只用于"还要等多久"的**粗略**估计：进度可用时按已完成站数
#: 外推，拿不到进度时用它兜底。宁可说"大约"，也不要给一个精确到秒的假数。
ROUND_TYPICAL_S = 11 * 60


def round_eta_s(patrol: dict, *, now: datetime) -> int | None:
    """这一轮大概还要多少秒（拿不到开始时间就返回 None）。"""
    started = patrol.get("started_at")
    if not started:
        return None
    try:
        t0 = datetime.fromisoformat(started)
    except (TypeError, ValueError):
        return None
    elapsed = (now - t0).total_seconds()
    index, total = patrol.get("station_index"), patrol.get("station_total")
    if isinstance(index, int) and isinstance(total, int) and 0 < index < total and elapsed > 0:
        return max(0, int(elapsed / index * (total - index)))
    return max(0, int(ROUND_TYPICAL_S - elapsed))


def eta_text(patrol: dict, *, now: datetime) -> str:
    """给操作者的一句话："大约还要等多久"（ADR-0013：拒绝时必须说清楚，别只回"忙"）。"""
    eta = round_eta_s(patrol, now=now)
    if eta is None:
        return "预计几分钟后可用"
    return f"预计 {max(1, round(eta / 60))} 分钟后可用"


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
        # now 走**注入的时钟**：门禁的天数会随时钟变，测试与被测代码必须看同一个"今天"
        room = room_state(self.deps.room_path, now=self.deps.now())
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


def preview_block(deps: ConsoleDeps) -> dict | None:
    """巡检进行中**不给预览**（ADR-0017 §4）：返回拒绝体；允许预览则返回 None。

    为什么要拒：那一轮要用相机抓 60 张。实测这台 DVR 允许 ≥3 路并发 RTSP，但"预览 +
    抓图 + 老系统整点抓 6 台"三方同时拉没做过长时间验证，不去赌。轮间（约 94% 的时间）
    随时可看；急停**不**解锁预览——它停的是机构，本轮仍在跑。
    """
    patrol = patrol_state(deps.runs_dir)
    if not patrol.get("active"):
        return None
    return {
        "error": "巡检进行中：相机这一路归本轮，" + eta_text(patrol, now=deps.now()),
        "reason": "patrolling",
        "patrol": patrol,
        "retry_after_s": round_eta_s(patrol, now=deps.now()),
        "retry_hint": "本轮结束后预览自动可用；需要马上看画面可以等这一轮跑完",
    }


def _httpx_preview_opener(base_url: str):
    """默认的上游打开器：httpx 异步流。

    **读超时必须为 None**：MJPEG 在两帧之间会安静地等一整个帧间隔（5 fps 就是 200 ms），
    相机卡一下会更久。给一个"合理"的读超时，等于让页面每隔几秒黑一次——而且是从
    console 这一层断的，排障时看着像预览服务挂了。
    """

    @asynccontextmanager
    async def open_upstream(path: str):
        import httpx

        timeout = httpx.Timeout(connect=5.0, read=None, write=10.0, pool=5.0)
        async with (
            httpx.AsyncClient(timeout=timeout) as client,
            client.stream("GET", f"{base_url}{path}") as r,
        ):
            yield r

    return open_upstream


def _preview_opener(deps: ConsoleDeps):
    return deps.preview_open or _httpx_preview_opener(deps.preview_url)


async def _read_preview(deps: ConsoleDeps, path: str, limit: int = 65536):
    """把上游一个**有限**响应读完（`/healthz`、`/frame.jpg`）。流不走这里。"""
    opener = _preview_opener(deps)
    async with opener(path) as r:
        chunks, n = [], 0
        async for chunk in r.aiter_bytes(8192):
            chunks.append(chunk)
            n += len(chunk)
            if n >= limit:
                break
        return r.status_code, b"".join(chunks)


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
        preview_url=os.environ.get("PATROL_PREVIEW", "http://mushroom_preview:8003"),
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
        # 同样走注入时钟：这条接口的答案（第几天、今天动不动）**只**取决于"今天"，
        # 用真实时钟会让它在跨零点时悄悄变一个数（测试里就是这么炸的）。
        return room_state(deps.room_path, now=deps.now())

    @app.get("/api/stations")
    def api_stations() -> dict:
        return stations_state(deps.stations_path)

    @app.get("/api/grid")
    def api_grid() -> dict:
        return grid_geometry()

    @app.get("/api/images")
    def api_images(station_id: str = "", limit: int = Query(200, ge=1, le=2000)) -> dict:
        return console.images(station_id=station_id or None, limit=limit)

    @app.get("/api/growth")
    def api_growth(box_id: str = "", limit: int = Query(50, ge=1, le=500)) -> dict:
        """某框的逐点生长对比——**代理 prod 的 `/growth`**，页面不直连 prod。

        为什么要代理：页面在菇房内网里很可能到不了 prod（ADR-0003），而且直连就得把
        prod 的地址与凭据放进浏览器可见的配置里。

        prod 查不到时**如实报错**（`ok:false` + `error`），不返回一条空曲线顶替：
        "还没有测量值"与"问不到 prod"在现场是两件完全不同的事，页面必须分得开。
        """
        if not box_id.strip():
            return {"ok": False, "error": "需要 box_id（生长是按框比的）", "points": []}
        if deps.transport is None:
            return {"ok": False, "error": "未接入 prod 分析服务（transport 未配置）", "points": []}
        q = urlencode({"box_id": box_id.strip(), "limit": limit})
        try:
            got = deps.transport(f"{deps.analysis_url}/growth?{q}")
        except Exception as e:  # noqa: BLE001 - prod 不通不该让页面整页报错
            return {"ok": False, "error": f"prod 查询失败：{e}", "points": [], "box_id": box_id}
        points = got.get("points", []) if isinstance(got, dict) else []
        latest = got.get("latest") if isinstance(got, dict) else None
        return {"ok": True, "box_id": box_id, "points": points, "latest": latest}

    # ---------- 实时预览：从 console 反代到预览容器（ADR-0017） ----------
    #
    # 页面写 `<img src="/api/preview">`，不直连预览容器：相机地址与**口令**只存在于
    # 服务端（与 ADR-0003 的单一 origin 一致），浏览器拿到的永远只是 MJPEG 字节流。

    @app.get("/api/preview/status")
    async def api_preview_status() -> JSONResponse:
        """预览现在能不能看（页面每秒轮询，永不 500——与 /api/status 同样的规矩）。"""
        block = preview_block(deps)
        if block is not None:
            return JSONResponse({**block, "available": False, "upstream": None})
        try:
            status, body = await _read_preview(deps, "/healthz")
        except Exception as e:  # noqa: BLE001 - 预览没起不该让页面整页报错
            return JSONResponse({"available": False, "upstream": None,
                                 "error": f"预览服务连不上：{e}"})
        try:
            info = json.loads(body or b"{}")
        except json.JSONDecodeError:
            info = {"raw": body[:200].decode("utf-8", "replace")}
        ok = status == 200
        return JSONResponse({
            "available": ok,
            "upstream": info,
            # 上游 /healthz 用 503 表达"还没有帧"（ffmpeg 正在连相机）：照实说
            "error": None if ok else f"预览服务暂时没有画面（上游 HTTP {status}）",
        })

    @app.get("/api/preview/frame.jpg")
    async def api_preview_frame() -> Response:
        """单帧快照：流断了时页面可以退回"两秒一张"的降级显示，也方便排障。"""
        block = preview_block(deps)
        if block is not None:
            return JSONResponse(block, status_code=409)
        try:
            status, body = await _read_preview(deps, "/frame.jpg")
        except Exception as e:  # noqa: BLE001
            return JSONResponse({"error": f"预览服务连不上：{e}"}, status_code=503)
        if status != 200 or not body:
            return JSONResponse({"error": f"还取不到画面（上游 HTTP {status}）"}, status_code=503)
        return Response(content=body, media_type="image/jpeg",
                        headers={"Cache-Control": "no-store"})

    @app.get("/api/preview")
    async def api_preview() -> Response:
        """MJPEG 流（`multipart/x-mixed-replace`）：`<img>` 直接就能播，页面零播放器。

        必须先自己打开上游、看到它的状态码再返回：`StreamingResponse` 一旦返回，
        状态码就定死了——上游 503（正在连相机）会变成"200 + 空流"，页面只看到一张
        永远不出现的图，而错误理由全丢了。
        """
        block = preview_block(deps)
        if block is not None:
            return JSONResponse(block, status_code=409)
        stack = AsyncExitStack()
        try:
            r = await stack.enter_async_context(_preview_opener(deps)("/stream.mjpg"))
        except Exception as e:  # noqa: BLE001
            await stack.aclose()
            return JSONResponse({"error": f"预览服务连不上：{e}"}, status_code=503)
        if r.status_code != 200:
            await stack.aclose()
            return JSONResponse({"error": f"预览服务没给流（上游 HTTP {r.status_code}）"},
                                status_code=503)

        async def relay() -> AsyncIterator[bytes]:
            n = 0
            try:
                async for chunk in r.aiter_bytes(16384):
                    # 流开着的时候巡检可能开始了（调度器不看页面）。**服务端**兜底把它断掉，
                    # 而不是指望页面自觉——标签页卡住、网线掉了、页面是老版本，规则就没人执行了。
                    # 按块数而不是按秒判：5 fps 下 8 块约 1.5 秒，且不引入第二个时钟。
                    n += 1
                    if n % PREVIEW_RECHECK_CHUNKS == 0 and preview_block(deps) is not None:
                        break
                    yield chunk
            finally:
                # 浏览器关掉 <img>（放开接管、切页）时这里会被调用：上游 http 连接必须
                # 跟着断，否则预览容器会一直以为还有个观看者（广播队列白留一路）。
                await stack.aclose()

        return StreamingResponse(relay(), media_type="multipart/x-mixed-replace; boundary=frame",
                                 headers={"Cache-Control": "no-store"})

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
        """放开会话：**先排一条回零**，再撤销授权。

        为什么"放开"里含回零：手动点动之后，坐标系与实际位置的关系只有操作者心里有；
        而本机无编码器，两端硬限位是**唯一**的物理基准，回零是唯一能把两者重新对齐的
        动作。所以放开的语义是"回零 + 撤权"，不是单纯撤权。

        排不进（通道上还有指令在跑 / 巡检进行中）就**如实说**，绝不静默跳过——那会让
        人以为机器已经回零了。真正的回零由执行方做（只有它持有控制器）。
        """
        ch = channel()
        active = ch.active_session()
        patrol = patrol_state(deps.runs_dir)
        queued: dict | None = None
        pending = ch.inflight()
        if active is None:
            detail = "没有有效会话，无需回零"
        elif patrol.get("active"):
            detail = (f"巡检进行中（{eta_text(patrol, now=deps.now())}）——"
                      "本轮结束时它自己会回原位，未另排回零")
        elif pending is not None and pending.kind == "home":
            # 已经有一条回零在排队（多半是上一次放开时排的）：**别谎称"刚排的"**，
            # 但要让操作者知道"放开之后机器会回零"这件事仍然成立。
            queued = asdict(pending)
            detail = f"已有一条回零在排队（{pending.id}）：执行方领走后写回结果"
        else:
            cmd, why = ch.submit("home", by="operator", session=active.token)
            if cmd.kind == "home":
                queued = asdict(cmd)
                detail = "已排回零：执行方到位后写回结果（GET /api/cmd 看进度）"
            else:
                detail = f"回零没排上（{why}）——放开前请确认机器现在的位置"
        ch.close_session()
        console.note(f"操作者已放开会话（{detail}）")
        return {"closed": True, "home_command": queued, "detail": detail}

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
        """当前指令与最近一次结果（页面按这个轮询）。

        `progress` 是执行方在**运动过程中**写回的实时位置/速度（`progress.json`）：
        只有"属于当前在飞的那条指令"且"足够新鲜"才带出去——id 对不上或写回停了，
        页面拿到的就是残值，而残值冒充实时位置比"没有位置"更害人。
        """
        ch = channel()
        inflight = ch.inflight()
        result = ch.result()
        progress = None
        if inflight is not None:
            raw = ch.read_progress()
            if raw and raw.get("id") == inflight.id:
                try:
                    age = (deps.now() - datetime.fromisoformat(str(raw.get("ts") or ""))).total_seconds()
                except ValueError:
                    age = PROGRESS_STALE_S + 1
                if age <= PROGRESS_STALE_S:
                    progress = raw
        return {"inflight": asdict(inflight) if inflight else None,
                "result": asdict(result) if result else None,
                "progress": progress,
                "estop": ch.raised(),
                "session_active": ch.active_session() is not None}

    @app.post("/api/cmd", status_code=202)
    def api_cmd(body: CmdBody) -> JSONResponse:
        """提交一条手动指令（goto/jog/home/lamp/capture/stop）。

        **异步**：这里只把指令写进共享目录就返回 202，执行方（持有控制器的那个进程）
        领走后写回结果。之所以不是同步的：控制器是单会话设备，console 一旦自己去连，
        就可能把正在跑的那一轮巡检踢掉。页面按 `GET /api/cmd` 轮询结果。

        **巡检进行中拒绝会动机构的指令**（ADR-0013）：轮内机构归那一轮，等人家的 11
        分钟跑完再动。在这里当场回 409 是为了让页面立刻能说清"现在为什么不能动"——
        只靠执行方那边的过期判定，操作者要等到轮末才知道自己被拒了。
        """
        patrol = patrol_state(deps.runs_dir)
        if patrol.get("active") and body.kind in MANUAL_GATED_KINDS:
            return JSONResponse(
                {
                    "error": "巡检进行中：机构由这一轮占用，" + eta_text(patrol, now=deps.now()),
                    "patrol": patrol,
                    "retry_hint": "轮间（约 94% 的时间）随时可手动操作；急停永远可用",
                },
                status_code=409,
            )
        ch = channel()
        # 急停闩锁要**先于**"上一条还没结束"报出来：操作者最需要知道的是"现在还按着急停"，
        # 而"上一条没结束"会让人以为再等等就能动。判断与执行方共用 `estop_allows`。
        if ch.raised() and not estop_allows(body.kind, body.args):
            return JSONResponse(
                {"error": "急停已置位：先复位急停再操作（复位是独立动作，不随指令自动清掉）",
                 "estop": True},
                status_code=409,
            )
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
        # 每接受一条指令就续期（spec §5.2）：手动操作是"连着做几件事"，让会话在两次
        # 动作之间过期，操作者会在最不方便的时候被踢出去。续期只延长授权，不动机构。
        renewed = ch.renew_session() if active is not None else None
        console.note(f"指令 {cmd.id} {cmd.kind} {cmd.args or ''}", level="info")
        return JSONResponse({"command": asdict(cmd), "detail": why,
                             "session": asdict(renewed) if renewed else None},
                            status_code=202)

    @app.get("/api/events")
    def api_events() -> JSONResponse:
        return JSONResponse({"events": console.events[-EVENT_BUFFER:]})

    return app
