"""巡检避让闸门：蓝光/加湿期**起轮前延后、轮中原地暂停**（票 14、票 22）。

用户口径（2026-10-21，2026-10-21 下午修订）：

1. **补白光不影响**（巡检自己的白色补光），正常拍；
2. **补蓝光**：还没起轮 ⇒ 延后最多 30 min 再起；已经起轮 ⇒ 原地暂停，最多等 30 min
   等蓝光关掉再继续；
3. **加湿**：同上（未起轮等、已起轮原地停）；
4. **两个预算独立**：起轮前 30 min 与轮中 30 min 各自计时（首版曾"共用一份"，
   于是起轮前忍满后整轮等于没避让 —— 见票 23 §5-A）；
5. **连续 2 轮被拦 ⇒ 回原点跳过该轮**（轮内**兜底**）：上一轮已经等满预算仍未解除，
   这一轮不再干等 30 min，直接把机构退回 (0,0)、本轮什么都不拍（票 23 §5-A）。
   注意执行方现在**先探条件再起轮**（ADR-0029：每 60 s 探一次、条件一好立刻拍），
   正常情况走不到这条；留着它是为了"探针说能拍、起轮后条件又变"的那种情形。

两个信号从哪来：

* **加湿**：TDengine 的 `Running`（analysis 的 `GET /env/humid`，0/1=运行、2/3=停止）。
* **蓝光**：**台账里没有"灯现在亮没亮"的实测点**——`OnOff1..4` 是"启用/自动"使能位
  （2026-10-21 实测 2 小时不变），`Choose1..4` 只是白/蓝选择，`Model`/`Running` 是模式。
  所以蓝光只能用**图像本身**当传感器：蓝光下 `B/R` 实测 11–16，熄灯/白光下 ≈1.0
  （证据见 `docs/patrol/imaging-quality/issues/01-camera-imaging-quality.md`）。
  起轮前用一次**不动机构**的抓拍当探针，轮中用上一站刚拍的那张。

未知（取不到/太黑）一律**不拦**：传感器坏掉不该把巡检永久停掉；每次跳过都留一行日志。
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from io import BytesIO
from pathlib import Path

#: 蓝光判定：整幅 B/R 比值（720×404 抽样口径）。中性帧实测 0.94–1.00，蓝光帧 11–16。
BLUE_RATIO_THRESHOLD = 3.0
#: 太黑（熄灯且没有漫射光）时 B/R 没有意义 ⇒ 返回 None（未知，不拦）。
MIN_MEAN_LUMA = 15.0
#: 一轮/一次等待的总预算，用户口径 30 min。
DEFAULT_BUDGET_S = 1800.0
#: 轮询间隔：灯是 30/10 min 级周期、加湿是 8–15 min 级，1 min 足够。
DEFAULT_POLL_S = 60.0
#: 连续几轮"等满预算仍未解除"就放弃该轮（回原点，不拍）。用户口径 2026-10-21：2 轮。
DEFAULT_MAX_BLOCKED_ROUNDS = 2


def default_state_path(outbox: str | None) -> str:
    """连续被拦轮数的落盘点：`/app/data/outbox.jsonl` → `/app/data/outbox.gate.json`。

    必须落盘：`patrol-serve` 每轮都是**新的一次** `m1.main()`（还可能是独立进程），
    计数留在内存里等于每轮都从 0 开始，"连续 2 轮"永远攒不满。
    """
    if not outbox:
        return ""
    return str(Path(outbox).with_suffix(".gate.json"))


def load_blocked_rounds(path: str | Path | None) -> int:
    if not path:
        return 0
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        return max(0, int(data.get("blocked_rounds", 0)))
    except Exception:  # noqa: BLE001 - 状态文件坏了按 0 处理（宁可按 0 重新计数）
        return 0


def save_blocked_rounds(path: str | Path | None, value: int) -> str | None:
    """原子写；返回一句错误描述（None = 写成功或无需写）。"""
    if not path:
        return None
    target = Path(path)
    try:
        from datetime import datetime

        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_suffix(target.suffix + ".tmp")
        tmp.write_text(json.dumps({
            "blocked_rounds": int(value),
            "updated_at": datetime.now().isoformat(timespec="seconds"),
        }, ensure_ascii=False), encoding="utf-8")
        tmp.replace(target)
        return None
    except Exception as exc:  # noqa: BLE001 - 落盘失败不该影响巡检
        return f"{type(exc).__name__}: {exc}"


def blue_from_jpeg(blob: bytes, *, threshold: float = BLUE_RATIO_THRESHOLD,
                   min_mean_luma: float = MIN_MEAN_LUMA) -> bool | None:
    """这一帧是不是**蓝光下**拍的：`True` 蓝、`False` 不蓝、`None` 判不了（太黑/解不开）。"""
    try:
        import numpy as np
        from PIL import Image
    except ImportError:  # pragma: no cover - 镜像里两者都有（measure 的运行时依赖）
        return None
    try:
        img = Image.open(BytesIO(blob)).convert("RGB").resize((720, 404))
    except Exception:  # noqa: BLE001 - 坏字节 = 判不了
        return None
    arr = np.asarray(img, dtype="float32")
    mean = float(arr.mean())
    if mean < min_mean_luma:
        return None
    red = float(arr[..., 0].mean())
    blue = float(arr[..., 2].mean())
    return (blue / max(red, 1.0)) >= threshold


def humid_from_api(api_base: str, room_id: str, *, timeout_s: float = 5.0,
                   client=None) -> bool | None:
    """`GET /env/humid` → `True` 在加湿 / `False` 没加湿 / `None` 未知。"""
    url = api_base.rstrip("/") + "/env/humid"
    try:
        if client is not None:
            data = client(url, {"room_id": room_id})
        else:
            import httpx

            data = httpx.get(url, params={"room_id": room_id}, timeout=timeout_s).json()
    except Exception:  # noqa: BLE001 - 取不到就是未知，交给调用方 fail-open
        return None
    value = data.get("humidifying") if isinstance(data, dict) else None
    return None if value is None else bool(value)


class PatrolGate:
    """起轮前 / 逐站两个等待点，**各自**一套 30 min 预算（互不占用）。

    ``probe_image``：不动机构拍一张，返回 JPEG 字节（起轮前与暂停期间都用它）。
    ``fetch_bytes``：按 URL 取回已拍好的那一帧（轮中优先用它，省一次抓拍）。
    ``max_blocked_rounds``：连续几轮"等满预算仍未解除"就放弃该轮并回原点，默认 2。
    """

    def __init__(self, *, probe_image: Callable[[], bytes | None],
                 fetch_bytes: Callable[[str], bytes] | None,
                 humid_probe: Callable[[], bool | None],
                 budget_s: float = DEFAULT_BUDGET_S,
                 poll_s: float = DEFAULT_POLL_S,
                 max_blocked_rounds: int = DEFAULT_MAX_BLOCKED_ROUNDS,
                 state_path: str | None = None,
                 sleep: Callable[[float], None] = time.sleep,
                 clock: Callable[[], float] = time.monotonic,
                 abort: Callable[[], bool] | None = None,
                 log: Callable[[str], None] = print):
        self.probe_image = probe_image
        self.fetch_bytes = fetch_bytes
        self.humid_probe = humid_probe
        self.budget_s = float(budget_s)
        self.poll_s = float(poll_s)
        self.max_blocked_rounds = max(1, int(max_blocked_rounds))
        self.state_path = state_path or ""
        self._sleep = sleep
        self._clock = clock
        self.abort = abort
        self.log = log
        #: 连续"等满预算仍未解除"的轮数：到 ``max_blocked_rounds`` 就回原点跳过该轮。
        #: 跨轮/跨进程要靠 ``state_path`` 落盘，否则每轮都从 0 起（票 23 §5-A）。
        self.blocked_rounds = load_blocked_rounds(self.state_path)
        self._mid_deadline: float | None = None
        #: 本轮轮中预算已用完 ⇒ 余下各站不再判、不再取帧（票 23 §5-B）。
        self._mid_done = False
        self._humid_cache: bool | None = None
        self._humid_at: float | None = None

    def _humid(self) -> bool | None:
        """加湿信号按 `poll_s` 缓存：逐站调一次 analysis 会把 TDengine 打满。"""
        now = self._clock()
        if self._humid_at is not None and now - self._humid_at < max(self.poll_s - 5.0, 1.0):
            return self._humid_cache
        try:
            value = self.humid_probe()
        except Exception as exc:  # noqa: BLE001 - 信号坏了不拦巡检
            self.log(f"避让：加湿信号取不到（{exc}）——按不拦处理")
            value = None
        self._humid_cache, self._humid_at = value, now
        return value

    def _aborting(self) -> bool:
        if self.abort is not None and self.abort():
            self.log("避让：收到急停请求，停止等待（本轮由急停链收尾）")
            return True
        return False

    def _persist(self) -> None:
        """把 `blocked_rounds` 落盘（每轮是新的 `m1.main()`，内存里的计数活不过一轮）。"""
        error = save_blocked_rounds(self.state_path, self.blocked_rounds)
        if error:
            self.log(f"避让：连续被拦计数落盘失败（{error}）——本轮计数只在内存里")

    # ---- 判定 ----

    def _blue(self, blob: bytes | None) -> bool | None:
        if not blob:
            return None
        return blue_from_jpeg(blob)

    def blocking_reason(self, blob: bytes | None) -> str | None:
        """当前有没有要避让的：先加湿（便宜、直接），再蓝光（要一帧）。"""
        if self._humid():
            return "加湿中"
        try:
            sample = blob if blob is not None else self.probe_image()
        except Exception as exc:  # noqa: BLE001 - 探针抓拍失败不许把巡检带停
            self.log(f"避让：探针抓拍失败（{exc}）——按不拦处理")
            return None
        blue = self._blue(sample)
        if blue is None:
            return None
        return "蓝光" if blue else None

    def ready_now(self) -> tuple[bool, str | None]:
        """此刻能不能拍：**只探一次、不动机构、不连控制器**（执行方的外层轮询用）。

        为什么不复用 `wait_before_round` 去等：一轮里等会占住唯一执行方，手动指令全被
        堵在后面（ADR-0013）；而这里只是"看一眼"——没消耗触发、没让机构动一格。
        """
        reason = self.blocking_reason(None)
        return (reason is None, reason)

    # ---- 两个等待点 ----

    def wait_before_round(self) -> bool:
        """还没起轮：最多延后 ``budget_s``（每 60 s 探一次，预算**不与轮中共享**）。

        返回 ``True`` = 可以起轮；``False`` = 连续被拦的轮数到上限 ⇒ **别拍了，
        回原点跳过这一轮**（上一轮已经忍满 30 min 仍没解除，再等一遍只是白等）。
        """
        start = self._clock()
        while True:
            if self._aborting():
                return True                 # 急停链会收尾，这里不额外折腾机构
            reason = self.blocking_reason(None)
            if reason is None:
                if self.blocked_rounds:
                    self.log(f"避让：条件解除（连续 {self.blocked_rounds} 轮被拦的计数清零）")
                self.blocked_rounds = 0
                self._persist()
                return True
            # 上一轮已经等满预算仍未解除 ⇒ 这一轮直接放弃（"连续 2 轮"口径）
            if self.blocked_rounds + 1 >= self.max_blocked_rounds:
                self.blocked_rounds += 1
                self.log(f"避让：连续 {self.blocked_rounds} 轮遇{reason}"
                         f" —— 不再等，回原点、本轮不巡检")
                self._persist()
                return False
            left = self.budget_s - (self._clock() - start)
            if left <= 0:
                self.blocked_rounds += 1
                self.log(f"避让：起轮前遇{reason}，已延后 {self.budget_s / 60:.0f} min"
                         f" —— 按口径开始巡检（连续被拦第 {self.blocked_rounds} 轮）")
                self._persist()
                return True
            self.log(f"避让：起轮前遇{reason}，延后（剩余 {left / 60:.1f} min）")
            self._sleep(min(self.poll_s, left))

    def before_station(self, index: int, total: int, station, meta: dict | None) -> None:
        """已起轮：用**上一站刚拍的帧**判蓝光；要在就原地停（本轮预算 30 min，独立）。

        预算一旦用完就**短路**：不再取帧、不再判、不再每站记两行——首版每站都记
        「原地暂停」+「已暂停 30 min」，48 站 96 行，看着像一直在停，其实一秒没停
        （票 23 §5-B）。
        """
        if self._mid_deadline is None:
            self._mid_deadline = self._clock() + self.budget_s
        if self._mid_done:
            return
        blob = None
        url = (meta or {}).get("cloud_url")
        if url and self.fetch_bytes is not None:
            try:
                blob = self.fetch_bytes(url)
            except Exception:  # noqa: BLE001 - 取不回就现拍
                blob = None
        reason = self.blocking_reason(blob)
        if reason is None:
            return
        while True:
            if self._aborting():
                return
            left = self._mid_deadline - self._clock()
            if left <= 0:
                self._mid_done = True
                self.log(f"避让：轮中暂停预算已用完（{self.budget_s / 60:.0f} min，"
                         f"仍在{reason}）—— 余下各站不再判，按口径拍完")
                return
            self.log(f"避让：[{index}/{total}] {station.id} 原地暂停（{reason}）")
            self._sleep(min(self.poll_s, left))
            blob = self.probe_image()
            reason = self.blocking_reason(blob)
            if reason is None:
                self.log("避让：条件解除，继续巡检")
                return


def _api_base(ingest: str) -> str:
    """`http://host:8000/ingest` → `http://host:8000`（analysis 的根）。"""
    text = (ingest or "").strip().rstrip("/")
    return text.removesuffix("/ingest")


def build_gate(args, *, capture, room_id: str, abort: Callable[[], bool] | None = None,
               log: Callable[[str], None] = print,
               fetch: Callable[[str], bytes] | None = None) -> PatrolGate | None:
    """按命令行参数造闸门；`--gate off` / 缺前置 ⇒ None（行为与从前一致）。"""
    if getattr(args, "gate", "off") != "on":
        log("避让：关闭（--gate off）——蓝光/加湿期照常巡检")
        return None
    if not str(room_id or "").strip():
        log("避让：开启但拿不到 room_id —— 关闭（不猜库房）")
        return None
    api_base = (getattr(args, "gate_api", "") or "").strip() or _api_base(
        getattr(args, "ingest", ""))
    if not api_base:
        log("避让：开启但拿不到 analysis 端点（--gate-api / --ingest）—— 关闭")
        return None
    if capture is None:
        log("避让：开启但拿不到采图客户端 —— 关闭")
        return None

    from datetime import datetime

    from patrol.stations import CAMERA_IP

    from deploy.framing_wire import _fetch

    fetch_bytes = fetch or _fetch

    def probe_image() -> bytes | None:
        """**不动机构**拍一张当蓝光探针（相机在当前位置，灯由环境决定）。"""
        now = datetime.now()
        result = capture.capture(
            ip=CAMERA_IP, pwd="", filename=f"{now:%Y%m%d}/PROBE_{now:%H%M%S}.jpg")
        return fetch_bytes(result.cloud_url) if result.cloud_url else None

    def humid_probe() -> bool | None:
        return humid_from_api(api_base, str(room_id))

    budget = float(getattr(args, "gate_budget_s", DEFAULT_BUDGET_S) or DEFAULT_BUDGET_S)
    poll = float(getattr(args, "gate_poll_s", DEFAULT_POLL_S) or DEFAULT_POLL_S)
    max_rounds = int(getattr(args, "gate_max_blocked_rounds",
                            DEFAULT_MAX_BLOCKED_ROUNDS) or DEFAULT_MAX_BLOCKED_ROUNDS)
    state_path = ((getattr(args, "gate_state", "") or "").strip()
                  or default_state_path(getattr(args, "outbox", "")))
    gate = PatrolGate(
        probe_image=probe_image, fetch_bytes=fetch_bytes, humid_probe=humid_probe,
        budget_s=budget, poll_s=poll, max_blocked_rounds=max_rounds,
        state_path=state_path, abort=abort, log=log,
    )
    log(f"避让：开启（蓝光/加湿；起轮前延后 ≤{budget / 60:.0f} min、"
        f"轮中原地暂停 ≤{budget / 60:.0f} min（两个预算独立）；"
        f"连续 {gate.max_blocked_rounds} 轮未解除 ⇒ 回原点跳过该轮；"
        f"信号 {api_base}/env/humid + 抓拍；已拦 {gate.blocked_rounds} 轮"
        + (f"，计数 {state_path}" if state_path else "，计数不落盘") + "）")
    return gate
