"""流中断看门狗（票 07 第一版：**仪表化 + 观察模式**）。

## 这一版不做什么（硬约束）

* **不改任何控制流**：抓拍成功与否、是否重拍、是否中止轮次，一律照旧；
* `play_data_ready` 维持"只报不用"（票 06 起就是这样）；
* 三个字段**只出现在采图响应里**，不写轮次/帧元数据、不进 outbox——
  那要环境官先加列，属下一步。

## 信号从哪来

厂商 SDK 自己写日志（`XCloudSDK_SetLogTypeAndLevel`，`sdk.py:87`），宿主机把
`/app/log` 挂进了容器（entrypoint `mkdir -p /app/log`，compose 有
`…/log -> /app/log rw=true`）。发版官 2026-10-13 现场确认的两族：

* **心跳族** `mediacodec.log`（+ `.1/.2/.3`）：每条 `notifyPlayInfo` 是一次心跳，
  行里带绝对时间戳（毫秒）与 `relativeTime`，出处 `play_msg_pusher.cpp:95`；
* **连接族** `xcloudsdk.log`（+ `.1/.2/.3`）：`HeartBeat-Timeout` / `Login success` /
  `device disconnect`。**本版不用它**（看门狗的状态机只用心跳），留着做 v2：
  用 `Login success` 给 outage 收尾、用另两条做旁证。

两族都按大小轮转（约 10 MiB、每流 4 个文件、不压缩），容器内没有 logrotate。
`app_log.log` / `xvfb.log` 在容器重启时归零重建，**不是**同一族，不要续写。
日志时间戳是 CST（Asia/Shanghai），与服务时钟差 8 小时（发版官对拍实测）——
这个差值由 `tz_offset_s` 表达，**配错的后果是 age 算不出来，状态退化成 unknown，
不会假报 alive，也不会假报 dead**（见 `_stamp` 的将来守卫）。

## 为什么读日志而不是等回调

`sdk.py:334-342` 里 `EUIMSG_PLAY_INFO` 确实每帧都进 `_on_message`，但**只在有人等时
被转发**（没人等就丢），而现场实测**健康抓拍的 `wait_play_data` 也等满 5s 超时**——
稳态下应用层收不到稳定速率的心跳。在数到真实速率之前，回调不能当看门狗（票 07 §2.1）。
日志是厂商自己维护的、带绝对时间戳的一条线，先用它。

## fail-open（本模块最重要的性质）

读不到日志、文件缺失、解析不出时间戳、时区偏置对不上（时间戳看起来在将来）——
**一律 `unknown`**。`unknown` 是一等状态：不是错误，更不是 alive。
"""

from __future__ import annotations

import os
import re
import threading
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

#: 日志目录（容器内已挂好，不新增挂载）
DEFAULT_LOG_DIR = "/app/log"
#: 心跳文件名（轮转后是 `.1/.2/.3`）
DEFAULT_HEARTBEAT_FILE = "mediacodec.log"
#: 心跳行标记
DEFAULT_HEARTBEAT_PATTERN = "notifyPlayInfo"
#: 观察模式阈值：多久没有心跳算可疑（`suspect`）；两倍算 `dead`
DEFAULT_DEAD_AFTER_S = 30.0
#: 日志帧（CST）− 服务时钟，发版官 2026-10-13 实测正好 8 小时
DEFAULT_TZ_OFFSET_S = 28800.0
#: 启动时回看多长的尾巴（约 2.6 小时 @ ~1 行/s、~100 B/行）
DEFAULT_TAIL_BYTES = 1 << 20
#: 轮转文件后缀（本机是 size-based rename 轮转）
ROTATED_SUFFIXES = (".1", ".2", ".3")
#: 时钟抖动容忍（秒）：比"日志帧的现在"还新的心跳，超过这个幅度就认为时钟/时区对不上
FUTURE_TOLERANCE_S = 5.0

STATE_UNKNOWN = "unknown"
STATE_ALIVE = "alive"
STATE_SUSPECT = "suspect"
STATE_DEAD = "dead"

#: 行内时间戳：`HH:MM:SS` 可选 `.mmm` / `,mmm`。取**第一个**匹配——日志前缀通常在最前。
_TS_RE = re.compile(r"(?<!\d)(\d{1,2}):(\d{2}):(\d{2})(?:[.,](\d{1,3}))?(?!\d)")


def tail_lines(path: Path, n_bytes: int) -> list[str]:
    """读一个文件**最后** `n_bytes` 字节里的完整行（不改变任何跟随状态）。

    启动回看用：进程刚起（容器重启）而心跳一直在写，不回看就意味着第一次 observe
    手里没有任何心跳，只能报 unknown——观察模式里等于前几分钟瞎着。
    首行可能被截断，起点不在 0 时丢掉它。
    """
    try:
        with open(path, "rb") as fh:
            size = os.fstat(fh.fileno()).st_size
            fh.seek(max(0, size - n_bytes))
            data = fh.read()
    except OSError:
        return []
    lines = data.split(b"\n")
    if size > n_bytes:
        lines = lines[1:]                 # 首行多半是半行
    return [ln.decode("utf-8", "replace") for ln in lines if ln.strip()]


class _LogTail:
    """跟一个日志文件：按路径增量读 + 轮转处理。

    **不持宿命中的 fd**：每次读都 open → seek → read → close。代价是每 observe 一次
    open/close（巡检约 9 秒一次，可忽略）；换来的是 rename 轮转天然正确——持着 fd 时
    "路径还在、inode 变了"这种轮转会被我们手里的旧 fd 蒙混过去（而且某些平台上
    持开着的文件根本没法被 rename，连测试都写不了）。

    轮转有两种，都要接住：

    * **rename**（本机就是这种）：`.log → .log.1`，SDK 另开一个新的 `.log`。
      判据是**身份变化**：`(st_dev, st_ino)` 与上次读到的不同 ⇒ offset 归零；
    * **copy-truncate**：同一个 inode 被截断。判据是 **size 比 offset 还小** ⇒ 归零。
      （同一时刻 size 恰好等于 offset 的窗口理论上存在，那一次会漏读一段；
       本机是 rename 轮转，这条只是兜底。）

    文件暂时不在（轮转窗口、被删、没挂上）时返回空，**不抛**——调用方据此把状态退到
    unknown。
    """

    def __init__(self, path: Path):
        self.path = path
        self._ident: tuple[int, int] | None = None
        self._offset = 0
        self._pending = b""

    def _identify(self) -> tuple[int, int] | None:
        try:
            st = os.stat(self.path)
        except OSError:
            return None
        return (st.st_dev, st.st_ino)

    def read_new(self) -> list[str]:
        """自上次以来的新**完整**行（半行留在缓冲区，下次拼齐）。"""
        ident = self._identify()
        if ident is None:
            return []                       # 文件不在：轮转窗口/被删/没挂上
        if self._ident is not None and ident != self._ident:
            self._offset, self._pending = 0, b""      # rename 轮转
        elif ident == self._ident:
            try:
                size = os.stat(self.path).st_size
            except OSError:
                return []
            if size < self._offset:
                self._offset, self._pending = 0, b""  # copy-truncate 轮转

        try:
            with open(self.path, "rb") as fh:
                fh.seek(self._offset)
                chunk = fh.read()
        except OSError:
            return []
        if not chunk:
            return []

        self._ident = ident
        self._offset += len(chunk)
        buf = self._pending + chunk
        *lines, self._pending = buf.split(b"\n")
        return [ln.decode("utf-8", "replace") for ln in lines]


@dataclass
class StreamWatchdog:
    """心跳看门狗。线程安全；`observe()` 与 `fields()` 都只读日志、不动机构。"""

    log_dir: str = DEFAULT_LOG_DIR
    heartbeat_file: str = DEFAULT_HEARTBEAT_FILE
    heartbeat_pattern: str = DEFAULT_HEARTBEAT_PATTERN
    dead_after_s: float = DEFAULT_DEAD_AFTER_S
    tz_offset_s: float = DEFAULT_TZ_OFFSET_S
    tail_bytes: int = DEFAULT_TAIL_BYTES
    enabled: bool = True
    now: object = field(default=datetime.now)

    def __post_init__(self) -> None:
        self._lock = threading.Lock()
        self._tail = _LogTail(Path(self.log_dir) / self.heartbeat_file)
        self._bootstrapped = False
        self._last_heartbeat: datetime | None = None      # 日志帧（CST）
        self._outage: dict | None = None
        self._degraded = False                            # 信号源不可用/时钟可疑

    # ---------- 内部 ----------

    def _log_now(self) -> datetime:
        """"现在"换算到日志帧（CST）。"""
        return self.now() + timedelta(seconds=self.tz_offset_s)

    def _stamp(self, line: str) -> datetime | None:
        """从一行里取时间戳（日志帧）；取不到 ⇒ None。

        "看起来在将来"是时钟/时区对不上的信号：那说明我们连"日志里的现在"都没对上，
        此时任何 age 都是瞎算——**退回 None，让状态落成 unknown**。

        故意**不**做"跨零点回退一天"：那条规则会把"时区偏置配错 8 小时"洗成一条
        16 小时前的合法心跳，从而假报 `dead`——比 unknown 危险得多。代价是跨零点后
        读到昨晚的行会被丢弃（fail-open 成 unknown），下一条心跳到来即恢复。
        """
        match = _TS_RE.search(line)
        if match is None:
            return None
        hh, mm, ss = (int(match.group(i)) for i in (1, 2, 3))
        frac = match.group(4) or "0"
        micro = int(frac.ljust(6, "0")[:6])
        log_now = self._log_now()
        try:
            stamp = log_now.replace(hour=hh, minute=mm, second=ss, microsecond=micro)
        except ValueError:
            return None
        if stamp > log_now + timedelta(seconds=FUTURE_TOLERANCE_S):
            return None                          # 在将来 ⇒ 时钟/时区对不上
        return stamp

    def _note_heartbeat(self, when: datetime) -> None:
        """记一次心跳；若与上一次间隔超过阈值，就这一次间隔记成一次 outage。"""
        if self._last_heartbeat is not None:
            gap = (when - self._last_heartbeat).total_seconds()
            if gap >= self.dead_after_s:
                self._outage = {
                    "start": self._last_heartbeat.isoformat(timespec="milliseconds"),
                    "end": when.isoformat(timespec="milliseconds"),
                    "duration_s": round(gap, 3),
                }
        self._last_heartbeat = when

    def _bootstrap(self) -> None:
        """回看日志尾巴，建立"上次心跳"基线（见 `tail_lines` 的说明）。

        文件按从新到旧看：`.log` 有心跳就用它（轮转是顺序的，`.log` 一定比 `.1` 新）。
        """
        base = self._tail.path
        newest: datetime | None = None
        for path in [base] + [base.with_name(base.name + sfx) for sfx in ROTATED_SUFFIXES]:
            if newest is not None:
                break
            for line in tail_lines(path, self.tail_bytes):
                if self.heartbeat_pattern not in line:
                    continue
                when = self._stamp(line)
                if when is not None and (newest is None or when > newest):
                    newest = when
        if newest is not None:
            self._last_heartbeat = newest

    # ---------- 对外 ----------

    def observe(self) -> None:
        """读一次日志、更新内部状态。**不做任何控制动作**（第一版＝观察模式）。"""
        if not self.enabled:
            return
        with self._lock:
            if not self._bootstrapped:
                self._bootstrap()
                self._bootstrapped = True
            self._degraded = False
            for line in self._tail.read_new():
                if self.heartbeat_pattern not in line:
                    continue
                when = self._stamp(line)
                if when is None:
                    # 有心跳但读不出可信时间戳：**只丢这一行**，不毒化整个状态。
                    # 真正的 fail-open 由"一条都没读到"（_last_heartbeat is None）
                    # 和"文件不在"两条路负责。
                    continue
                self._note_heartbeat(when)
            if not self._tail.path.exists():
                self._degraded = True              # 文件不在：轮转窗口/被删/没挂上

    def fields(self) -> dict:
        """三个只读字段（直接并进抓拍响应）。

        `stream_last_heartbeat_age_s` 在状态是 `unknown` 时仍报**最后一次见到**的心跳
        年龄——它配合 `stream_state` 读："不知道 + 最后一次是 5 分钟前"是有用的排查信息。
        两者都没底（从没读到过心跳）时为 `None`。

        `stream_outage` 的时间戳是**日志帧（CST）**，不是服务时钟——和证据材料对齐。
        """
        with self._lock:
            age: float | None = None
            if self._last_heartbeat is not None:
                age = max(0.0, (self._log_now() - self._last_heartbeat).total_seconds())
            if not self.enabled or self._degraded or self._last_heartbeat is None:
                state = STATE_UNKNOWN
            elif age < self.dead_after_s:
                state = STATE_ALIVE
            elif age < 2 * self.dead_after_s:
                state = STATE_SUSPECT
            else:
                state = STATE_DEAD
            return {
                "stream_state": state,
                "stream_last_heartbeat_age_s": None if age is None else round(age, 3),
                "stream_outage": dict(self._outage) if self._outage else None,
            }
