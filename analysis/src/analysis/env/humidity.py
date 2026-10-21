"""加湿器启停状态的**只读**信号（票 14 / 票 22；给巡检避让用）。

数据源是 TDengine 里已有的加湿器表 `SUP_<前缀>MDHUM01/HUM02`（每个库房左右两台）。
`Running` 的权威语义（`docs/technical/reference/设备监控点参考.md` §3）：

* `0` 加湿自动运行、`1` 加湿手动运行 ⇒ **在加湿**；
* `2` 加湿停止、`3` 加湿关闭 ⇒ **不在加湿**。

2026-10-21 实测（611）：`Running` 在 0/2 之间按 8–15 分钟翻转，说明它是**实际运行状态**
而不是模式；这与 `Model`（0/1/2 模式）是两回事。⚠️ 仍不是"正在喷水"的物理证明——
自动模式下按湿度上下限间歇启停，`Running=0` 只表示控制器在跑这一路。

取不到/过期一律返回 `None`（未知），**不把未知当"没加湿"**：调用方自己决定 fail-open
还是 fail-closed（巡检选 fail-open，否则一个掉线的传感器会永久停掉巡检）。
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime

from analysis.env.tdengine import (
    ROOM_DEVICE_CODES,
    default_transport,
    parse_rest,
    to_local,
)

#: 两台加湿器的表后缀（左/右）。
HUM_SUFFIXES = ("HUM01", "HUM02")
#: 超过这个秒数没有新数据 ⇒ 该路 `unknown`（不参与判定）。
STALE_SECONDS = 600.0


def _age_seconds(ts_local: str, now: datetime) -> float:
    return (now - datetime.fromisoformat(ts_local)).total_seconds()


def humid_state(
    *,
    base_url: str,
    room: str,
    user: str = "root",
    password: str = "taosdata",
    transport: Callable[[str, str], dict] | None = None,
    timeout_s: float = 5.0,
    stale_s: float = STALE_SECONDS,
    now: datetime | None = None,
) -> dict:
    """读某库房两台加湿器的最近一条 `Running`，合成一个"当前是否在加湿"的信号。

    返回::

        {"room_id": "611",
         "humidifying": True | False | None,
         "source": "tdengine",
         "devices": [{"device": "SUP_TD1_Q1MDHUM01", "running": 0, "ts": "...",
                      "age_s": 12.3, "fresh": True, "error": None}, ...]}

    `humidifying` 的合成规则：**任一**新鲜的那一路在运行态（0/1）⇒ True；
    两路都新鲜且都不在运行态 ⇒ False；其余（全未知/全过期）⇒ None。
    """
    prefix = ROOM_DEVICE_CODES.get(str(room))
    if prefix is None:
        raise ValueError(f"未知库房 {room!r}（已知：{sorted(ROOM_DEVICE_CODES)}）")

    url = base_url.rstrip("/") + "/rest/sql"
    send = transport or (
        lambda u, sql: default_transport(u, sql, user=user, password=password, timeout_s=timeout_s)
    )
    now = now or datetime.now()
    devices: list[dict] = []
    for suffix in HUM_SUFFIXES:
        table = f"SUP_{prefix}MD{suffix}"
        sql = f"select `ts`, `Running` from data1.`{table}` order by `ts` desc limit 1"
        try:
            rows = parse_rest(send(url, sql))
        except Exception as exc:  # noqa: BLE001 - 单表失败不影响另一台
            devices.append({"device": table, "running": None, "ts": None, "age_s": None,
                            "fresh": False, "error": f"{type(exc).__name__}: {exc}"})
            continue
        if not rows:
            devices.append({"device": table, "running": None, "ts": None, "age_s": None,
                            "fresh": False, "error": "该表没有数据"})
            continue
        ts_utc, running = rows[0][0], rows[0][1]
        ts = to_local(str(ts_utc))
        age = _age_seconds(ts, now)
        devices.append({
            "device": table,
            "running": None if running is None else int(running),
            "ts": ts,
            "age_s": round(age, 1),
            "fresh": age <= stale_s,
            "error": None,
        })

    known = [d for d in devices if d["fresh"] and d["running"] is not None]
    if any(d["running"] in (0, 1) for d in known):
        humidifying: bool | None = True
    elif len(known) == len(devices):
        humidifying = False
    else:
        humidifying = None
    return {"room_id": str(room), "humidifying": humidifying, "source": "tdengine",
            "devices": devices}
