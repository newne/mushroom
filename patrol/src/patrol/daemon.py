"""7×24 常驻巡检守护（票 06）：时刻表触发、断线重连、结果落 outbox、同步 prod。

**失败的可观测性**（2026-09-13 上机教训）：`run_cycle` 过去只在 `PatrolRound.run()`
正常返回后才写第一条记录，于是"整轮被中断"表现为**零记录、零文件、零痕迹**——
现场只知道它死了。现在每一轮都开一份 `RoundJournal`（本地取证，见 `patrol.journal`），
开始先落一行、逐站心跳、结束再落一行（含异常堆栈）；outbox 仍然是"要同步到 prod 的
数据"的通道，两者分工不混。
"""

from __future__ import annotations

import os
import time
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Protocol

from patrol.capture_client import CaptureClient
from patrol.fmc import Fmc4030, FmcError, MotionTimeoutError
from patrol.framing import clamp_trim
from patrol.journal import RoundJournal
from patrol.orchestrator import FramingHook
from patrol.room import RoomState, RoomStateError, load_room_state
from patrol.round import PatrolRound
from patrol.stations import Station
from patrol.store import JsonlStore
from patrol.sync import SyncClient

RUNS_DIR = "runs"  # 取证目录名（相对 outbox 所在目录）


class _Disabled:
    """``journal_dir`` 的显式"关闭"哨兵。

    用哨兵而不是 ``None``：``None`` 在别处一律表示"用默认值"，若它同时表示"关闭"，
    两种意图就会撞车，而默认值恰恰是这里最重要的行为（失败取证不能靠调用方记得开）。
    """

    def __repr__(self) -> str:  # pragma: no cover - 只为日志可读
        return "JOURNAL_DISABLED"


JOURNAL_DISABLED = _Disabled()


class ToRow(Protocol):
    """measure 侧产物协议：MeasurementRecord（measure.record）满足它。

    patrol 不 import measure（依赖方向约束）；部署侧在 measure_fn 里做
    BoxStats -> MeasurementRecord 的转换，daemon 只调 to_row()。
    """

    def to_row(self) -> dict: ...


def journal_dir_for(outbox_path: str | Path) -> Path:
    """取证目录默认落在 outbox **旁边**的 ``runs/``。

    跟 outbox 同处一个可写目录（现场是 ``/opt/mushroom-patrol/``），不引入第二个
    配置项；运维知道 outbox 在哪，就知道取证在哪。
    """
    return Path(outbox_path).expanduser().resolve().parent / RUNS_DIR


class PatrolDaemon:
    def __init__(
        self,
        *,
        open_fmc: Callable[[], Fmc4030],
        stations: list[Station],
        capture_client: CaptureClient,
        store: JsonlStore,
        sync: SyncClient,
        reconnect_attempts: int = 3,
        backoff_s: float = 5.0,
        measure_fn: Callable[[object], list[ToRow]] | None = None,
        journal_dir: str | Path | _Disabled | None = None,
        room_state: RoomState | None = None,
        room_state_path: str | Path | None = None,
        apply_trim: Callable[[dict[str, tuple[float, float]]], None] | None = None,
        framing: FramingHook | None = None,
        now: Callable[[], datetime] = datetime.now,
        sleep: Callable[[float], None] = time.sleep,
        log: Callable[[str], None] = print,
    ) -> None:
        self.open_fmc = open_fmc
        self.stations = stations
        self.capture_client = capture_client
        self.store = store
        self.sync = sync
        self.reconnect_attempts = reconnect_attempts
        self.backoff_s = backoff_s
        self.measure_fn = measure_fn
        # 巡检准入门禁（patrol.room）：None = 未接入库状态 ⇒ 按"不动"处理。
        # 刻意不给默认放行：拿不到准入依据时不动，是这台会自己走 4.5 米导轨的机器的
        # 唯一安全默认（见 room.py 的 fail-closed 说明）。
        self.room_state = room_state
        # 给了文件路径就**每轮重读**：准入依据会随时间变（新批次入库是常态，
        # `deploy-fetch-room` 换掉 room.yaml 后不该要求人工重启进程）。传对象等于
        # 把判定依据在进程启动那一刻冻结住——常驻进程里那就是个陷阱。
        self.room_state_path = Path(room_state_path) if room_state_path is not None else None
        self._room_load_error: str | None = None
        # 图像微调学到的偏移怎么落盘由调用方决定（见 _learn_trims）——
        # patrol 不知道站位表放在哪，也不该知道。
        self.apply_trim = apply_trim
        # 图像微调（第二步定位）：None = 只走推导坐标，行为与从前一致。
        self.framing = framing
        # 默认：outbox 旁边的 runs/；要关掉必须显式传 JOURNAL_DISABLED
        if journal_dir is JOURNAL_DISABLED:
            self.journal_dir: Path | None = None
        elif journal_dir is None:
            self.journal_dir = journal_dir_for(store.path)
        else:
            self.journal_dir = Path(journal_dir)
        self.now = now
        self._sleep = sleep
        self.log = log
        self.rounds_started = 0

    # ---------- 主流程 ----------

    def run_cycle(self) -> str:
        """执行一轮：**准入检查** → 连接 → PatrolRound（回零/巡检/回原位）→ 记录 → 同步。

        返回 "ok"/"partial"/"failed"/"skipped"。

        **准入检查在任何连接动作之前**（`patrol.room`）：第 0–1 天、第 26 天起、或读不到
        入库日期时返回 "skipped"——不连控制器、不建取证文件、更不移动机构。
        每轮重新判定，所以天数走进窗口后无需重启就会自动开始巡检。

        **任何**中断（运动失败、Ctrl+C、运维按下 Ctrl+C 后被转成异常的退出）都先急停
        再返回——绝不让轴停在运动中无人看管。急停自身的成败只打进日志，不掩盖原始异常。

        事务性：**只有整轮跑完才有"结果"**，中途死掉的那一轮在 outbox 里不留半截数据
        （那正是 2026-09-13 看到的"零记录"）；它的痕迹在 `RoundJournal` 里。
        """
        allowed, reason = self._admission()
        if not allowed:
            self.log(f"本轮不巡检：{reason}")
            return "skipped"

        fmc = self._connect()
        if fmc is None:
            self.log("连接失败，本轮未开始")
            return "failed"
        journal = self._open_journal()
        try:
            return self._run_cycle_body(fmc, journal)
        finally:
            if journal is not None:
                journal.close()
            try:
                fmc.close()
            except Exception as e:  # noqa: BLE001 - 关闭失败不阻断下轮
                self.log(f"关闭连接异常: {e}")

    def _run_cycle_body(self, fmc, journal: RoundJournal | None) -> str:
        """真正跑一轮。异常在这里被归类、记进 journal，并转成 "failed"。"""

        def note(msg: str) -> None:
            self.log(msg)

        def heartbeat(index: int, total: int, **fields) -> None:
            if journal is not None:
                journal.station(index, total, **fields)

        try:
            if journal is not None:
                journal.start(stations=len(self.stations), extra=self._room_fields())
            report = PatrolRound(
                fmc, self.capture_client, self.stations, log=note, on_station=heartbeat,
                framing=self.framing,
            ).run()
        except BaseException as e:  # noqa: BLE001 - 分类后转 failed，绝不静默
            stopped = self._emergency_stop(fmc)
            if journal:
                # 急停结果先记，再以 end 收尾——end 是这一轮的**终止符**，
                # 追加在它后面的行会让人误以为"记完 end 又出事了"。
                journal.append({"event": "emergency_stop", "result": stopped})
                journal.exception(e, where="round")
            if isinstance(e, KeyboardInterrupt):
                self.log(f"收到中断，本轮作废；急停{stopped}")
                return "failed"
            if isinstance(e, (FmcError, MotionTimeoutError)):
                self.log(f"本轮中止（回零/运动失败）: {e}；急停{stopped}")
                return "failed"
            self.log(f"本轮异常（{type(e).__name__}: {e}）；急停{stopped}")
            return "failed"

        if journal:
            journal.end(
                status="ok" if report.ok else "partial",
                n_results=len(report.results),
                n_failures=len(report.failures),
                aborted=report.aborted,
            )

        self.store.append({
            "kind": "round",
            "ts": self.now().isoformat(timespec="seconds"),
            "ok": report.ok,
            "n_results": len(report.results),
            "n_failures": len(report.failures),
            "aborted": report.aborted,
            **self._room_fields(),
        })
        # ADR-0005：图像索引**一帧一行**，随 outbox → /ingest 同步到 prod。
        # 此前 StationCapture 返回的 object_name/cloud_url 在这里被直接丢弃，
        # 于是"图落在 MinIO 里，却没有任何记录能把它关联到站位与时间"。
        #
        # 每行都带 `_room_fields()`：**时间（ts）+ 库房（room_id）** 是之后回溯的钥匙，
        # 少了库房这一维，多库房部署时无法区分这些照片属于哪一间。
        room = self._room_fields()
        for meta in report.results:
            self.store.append({"kind": "image_index", **room, **meta})
        for fail in report.failures:
            self.store.append({
                "kind": "image_index",
                "ts": self.now().isoformat(timespec="seconds"),
                "ok": False,
                **room,
                **fail,
            })
        if self.measure_fn is not None:
            for rec in self.measure_fn(report):
                row = rec.to_row()
                row.setdefault("kind", "measurement")
                self.store.append(row)
        self._learn_trims(report)
        self._flush()
        return "ok" if report.ok else "partial"

    def _learn_trims(self, report) -> None:
        """把本轮微调学到的偏移交给调用方落盘（下一轮从好位置起步）。

        为什么在**轮末**统一写、而不是每站写完就落盘：写站位表是"改配置"，
        60 个站位各写一次等于把文件反复截断重写 60 次；一轮写完一次，代价可忽略，
        而且失败时（本轮没跑完）不会留下半套新 trim。
        """
        if self.apply_trim is None:
            return
        learned: dict[str, tuple[float, float]] = {}
        for meta in report.results:
            framing = meta.get("framing") or {}
            suggested = framing.get("suggested_trim")
            station_id = meta.get("station_id")
            if station_id and suggested:
                learned[station_id] = clamp_trim((float(suggested[0]), float(suggested[1])))
        if learned:
            try:
                self.apply_trim(learned)
                self.log(f"微调偏移已更新 {len(learned)} 个站位")
            except Exception as e:  # noqa: BLE001 - 学不到只是白跑一趟，不该毁掉这一轮
                self.log(f"微调偏移落盘失败（本轮数据已保留）: {e}")

    # ---------- 准入（巡检门禁） ----------

    def _room(self) -> RoomState | None:
        """当前库房状态。给了路径就现读（失败 ⇒ None = 不动）。

        每轮都读是有意的：`deploy-fetch-room` 换批次时只改文件，常驻进程要能自己
        跟上。读失败的原因记在 `_room_load_error` 里，`_admission()` 会原样报出来——
        现场最需要知道的就是"为什么今天不动"，而不是一句"未接入"。
        """
        if self.room_state_path is None:
            return self.room_state
        try:
            self._room_load_error = None
            return load_room_state(self.room_state_path)
        except RoomStateError as e:
            self._room_load_error = str(e)
            return None

    def _room_fields(self) -> dict:
        """写进每一条记录里的库房维度：**库房号 + 入库日期 + 批次**。

        为什么要冗余进每一行：`image_index` 是之后按站位/时间回溯照片的索引，
        而"这张照片属于哪间库房、哪批蘑菇"必须跟着行本身走——否则多库房/多批次
        部署时只能靠文件名或时间猜。入库日期同时是准入判定的依据，留档便于复盘
        "当时为什么允许/不允许拍"。
        """
        rs = self._room()
        if rs is None:
            return {"room_id": None, "entry_date": None, "batch_no": None}
        return {
            "room_id": rs.room_id,
            "entry_date": rs.entry_date.isoformat(),
            "batch_no": rs.batch_no,
        }

    def _admission(self) -> tuple[bool, str]:
        """这一轮能不能巡检。未接入库房状态 ⇒ 不动（fail-closed）。"""
        rs = self._room()
        if rs is None:
            if self._room_load_error:
                return False, f"库房状态不可用（{self._room_load_error}）——不移动机构"
            return False, ("未接入库房在库状态（room.yaml 缺失或未读入）——"
                           "拿不到入库日期就不移动机构")
        return rs.verdict(self.now().date())

    # ---------- 取证 ----------

    def _open_journal(self) -> RoundJournal | None:
        """开一份本轮取证文件。``journal_dir`` 为 None 时明确关闭（测试/极简部署用）。"""
        if self.journal_dir is None:
            return None
        self.rounds_started += 1
        journal = RoundJournal(self.journal_dir, now=self.now(), pid=os.getpid())
        self.log(f"本轮取证：{journal.path}")
        return journal

    # ---------- 连接与同步 ----------

    def _connect(self):
        for attempt in range(1, self.reconnect_attempts + 1):
            try:
                fmc = self.open_fmc()
            except Exception as e:  # noqa: BLE001 - 连接失败重试由循环控制
                self.log(f"连接失败（{attempt}/{self.reconnect_attempts}）: {e}")
                if attempt < self.reconnect_attempts:
                    self._sleep(self.backoff_s)
                continue
            # ADR-0008：控制器自带软限位（出厂 ±200mm），与行程不符时长行程会被**静默
            # 截断**——表现为"指令走了 4.4 米、实际停在 0.2 米"，而控制器不报错。
            # 原先这道校验只在 m1 启动时做一次，于是常驻进程里"启动时对了、之后被改错"
            # 就没人管；放到每次连接之后，等于每轮都验一遍。
            #
            # 校验不过**不重试**：软限位是配置错了，重试三次只是把同一个错误再说三遍，
            # 而每次重试都要连一次控制器。直接断开、本轮判失败（机构不动）。
            try:
                fmc.check_soft_limits()
            except FmcError as e:
                self.log(f"软限位未整定，拒绝本轮巡检: {e}")
                try:
                    fmc.close()
                except Exception as close_err:  # noqa: BLE001 - 关闭失败不掩盖原因
                    self.log(f"关闭连接异常: {close_err}")
                return None
            if attempt > 1:
                self.log(f"重连成功（第 {attempt} 次尝试）")
            return fmc
        self.log("连接失败次数超限，转人工处理")
        return None

    def _flush(self) -> None:
        try:
            n = self.sync.flush(self.store)
            if self.store.corrupt_lines:
                self.log(f"已隔离 {self.store.corrupt_lines} 行损坏的 outbox 记录（含于同步数据外）")
            if n:
                self.log(f"已同步 {n} 条记录到 prod")
        except Exception as e:  # noqa: BLE001 - 同步失败保留 outbox 待补传
            self.log(f"同步失败（数据保留本地 outbox，稍后补传）: {e}")

    def _emergency_stop(self, fmc) -> str:
        """尽力停住两轴，返回一句可读结果。

        急停**自身**失败不能再抛异常——那会把真正的原因（运动失败/中断）盖掉，
        现场看到的就是一个与故障无关的报错。失败只记日志，并把"未确认成功"的动作
        数报出来（此时正确的下一步是断电）。
        """
        try:
            failed = fmc.stop_everything()
        except Exception as e:  # noqa: BLE001 - 不能覆盖原始异常
            self.log(f"急停本身失败: {e}")
            return "失败（见日志，必要时断电）"
        if failed:
            self.log(f"急停有 {len(failed)} 项未确认成功：{'；'.join(failed)}")
            return "部分失败（见日志）"
        return "完成"
