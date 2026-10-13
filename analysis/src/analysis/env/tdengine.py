"""TDengine 环境数据源（票 01，口径见 ADR-0021）。

现场实测（2026-10-12，prod 10.77.77.39）：

* 库 `data1`，REST 在 `:6041`（容器 `tools_taos`），SQL 走 `POST /rest/sql`；
* 每个库房两张表：`TD1_Q<n>MDTM01`（温湿度 CO2）与 `TD1_Q<n>MDLIGHT01`（补光灯状态），
  两者都是**每分钟一条**；
* 表名与列名**大小写敏感**，必须用反引号包起来——不加会被折成小写，
  报 `Invalid column name`（现场踩过）；
* REST 返回的 `ts` 带 `Z`（UTC），落库统一转成北京时间的本地 ISO8601，
  与 `rounds` / `images` 的 `ts` 口径一致。

补光灯那一列是**设备状态**，不是"灯亮没亮"（`设备监控点参考.md` §4 的 `Running`）::

    0=自动   1=手动   2=停止   3=关闭

所以它落到 `environment.lamp_state`，是个分类量。**不要拿它当 0/1 的点亮信号去积分**：
"点亮分钟"只能由设定周期（`OnMSet`/`OffMSet`）推导，那是设定值不是实测值。
今天 611 库的实测就是 `Running=2/3` 且 `OnMSet=0`、`OffMSet=120`——这批菇当前不补光。
"""

from __future__ import annotations

import base64
import json
import urllib.request
from bisect import bisect_right
from datetime import datetime, timedelta, timezone

from analysis.env.adapters import EnvRow

#: 库房号 → TDengine 表前缀。与 `configs/static_config.json` 的登记一致。
ROOM_DEVICE_CODES: dict[str, str] = {
    "611": "TD1_Q1",
    "612": "TD1_Q2",
    "607": "TD1_Q3",
    "608": "TD1_Q4",
}

#: 北京时间偏移。中国无夏令时，固定 +8；TB 口径与 `rounds.ts` 对齐。
LOCAL_TZ = timezone(timedelta(hours=8))

ENV_COLUMNS = ("Tatmosp", "Hatmosp", "Co2")
#: 补光灯：状态 + 循环参数（开 `OnSSet` 秒 / 停 `OffSSet` 秒）
LIGHT_COLUMNS = ("Running", "OnSSet", "OffSSet", "OnMSet", "OffMSet")


def _quote(name: str) -> str:
    """TDengine 标识符大小写敏感，反引号是语法的一部分，不是装饰。"""
    return "`" + name + "`"


def to_local(ts_utc: str) -> str:
    """`2026-10-12T08:19:54.266Z` → `2026-10-12T16:19:54`（本地，秒级）。"""
    text = ts_utc.strip().replace("Z", "+00:00")
    dt = datetime.fromisoformat(text)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(LOCAL_TZ).strftime("%Y-%m-%dT%H:%M:%S")


def to_utc(ts_local: str) -> str:
    """本地 ISO8601 → TDengine 查询用的 UTC 字面量。"""
    dt = datetime.fromisoformat(ts_local.strip())
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=LOCAL_TZ)
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def default_transport(url: str, sql: str, *, user: str, password: str, timeout_s: float = 15.0) -> dict:
    """TDengine REST：POST SQL 正文，Basic 认证。"""
    token = base64.b64encode(f"{user}:{password}".encode()).decode()
    req = urllib.request.Request(url, data=sql.encode("utf-8"), method="POST")
    req.add_header("Authorization", f"Basic {token}")
    req.add_header("Content-Type", "text/plain")
    with urllib.request.urlopen(req, timeout=timeout_s) as resp:  # noqa: S310 - 地址来自部署配置
        return json.loads(resp.read().decode("utf-8"))


def parse_rest(payload: dict) -> list[tuple]:
    """TDengine REST 信封 → 数据行；`code != 0` 一律抛错，不静默当空。"""
    if not isinstance(payload, dict):
        raise ValueError(f"TDengine 响应不是对象：{payload!r}")
    code = payload.get("code")
    if code != 0:
        raise RuntimeError(f"TDengine 查询失败 code={code} desc={payload.get('desc')!r}")
    return [tuple(row) for row in payload.get("data") or []]


class TdengineEnvSource:
    """按库房拉 TM01（温湿度 CO2）与 LIGHT01（补光状态），对齐成一条环境序列。

    两表采样都是每分钟一条但时刻略有偏差（如 :54 与 :55），所以补光状态**前向填充**
    到环境采样点上：每个环境采样点的 `lamp_state` 是"该时刻之前最近一次上报的状态"。
    """

    def __init__(
        self,
        base_url: str,
        *,
        database: str = "data1",
        rooms: list[str] | None = None,
        transport=None,
        user: str = "root",
        password: str = "taosdata",
        lookback_minutes: int = 120,
        timeout_s: float = 15.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.database = database
        self.rooms = [r for r in (rooms or list(ROOM_DEVICE_CODES)) if r in ROOM_DEVICE_CODES]
        self._transport = transport
        self._user = user
        self._password = password
        self.lookback_minutes = lookback_minutes
        self.timeout_s = timeout_s

    # --- 取数 -------------------------------------------------------------
    def _sql_url(self) -> str:
        return self.base_url + "/rest/sql"

    def _query(self, sql: str) -> list[tuple]:
        if self._transport is None:
            payload = default_transport(
                self._sql_url(), sql, user=self._user, password=self._password, timeout_s=self.timeout_s
            )
        else:
            payload = self._transport(self._sql_url(), sql)
        return parse_rest(payload)

    def _since_utc(self, since_ts: str | None) -> str:
        if since_ts:
            return to_utc(since_ts)
        start = datetime.now(timezone.utc) - timedelta(minutes=self.lookback_minutes)
        return start.strftime("%Y-%m-%dT%H:%M:%S.000Z")

    def _select(self, table: str, columns: tuple[str, ...], since_utc: str) -> list[tuple]:
        cols = ", ".join(_quote(c) for c in columns)
        # 起点**闭区间**：ingest 每轮都从整点重算当前小时，用 `>` 会把整点那一秒漏掉
        sql = (
            f"select ts, {cols} from {self.database}.{_quote(table)} "
            f"where ts >= '{since_utc}' order by ts"
        )
        return self._query(sql)

    def fetch_since(self, since_ts: str | None) -> list[EnvRow]:
        since_utc = self._since_utc(since_ts)
        rows: list[EnvRow] = []
        for room in self.rooms:
            prefix = ROOM_DEVICE_CODES[room]
            env = self._select(prefix + "MDTM01", ENV_COLUMNS, since_utc)
            light = self._select(prefix + "MDLIGHT01", LIGHT_COLUMNS, since_utc)
            rows.extend(merge_room(room, env, light))
        rows.sort(key=lambda r: (r.ts, r.zone))
        return rows


def merge_room(room: str, env_rows: list[tuple], light_rows: list[tuple]) -> list[EnvRow]:
    """把补光状态按**分钟**对齐到环境采样点上。

    `env_rows` 每行 `(ts, Tatmosp, Hatmosp, Co2)`，`light_rows` 每行 `(ts, Running)`。

    两表都是每分钟一条，但上报时刻相差一秒上下（实测环境 `:54.266`、补光 `:55.536`），
    按秒比较会把同一分钟的两条数据判成"补光还没有数据"。所以按分钟取齐：每个环境采样点
    用**该分钟或之前最近一分钟**的补光状态；补光表在该分钟之前没有数据时为 None——
    未知就是未知，不填 0。
    """
    light_by_minute: dict[str, tuple] = {}
    for raw in light_rows:
        # raw[0] 是时间戳，五个值从 raw[1] 起：Running, OnSSet, OffSSet, OnMSet, OffMSet
        light_by_minute[to_local(str(raw[0]))[:16]] = tuple(
            _num(raw[i]) if len(raw) > i else None for i in range(1, 6)
        )
    minutes = sorted(light_by_minute)

    out: list[EnvRow] = []
    for raw in env_rows:
        ts = to_local(str(raw[0]))
        idx = bisect_right(minutes, ts[:16]) - 1
        state, on_s, off_s, on_m, off_m = (
            light_by_minute[minutes[idx]] if idx >= 0 else (None,) * 5
        )
        out.append(
            EnvRow(
                ts=ts,
                zone=room,
                temp_c=_num(raw[1]),
                rh_pct=_num(raw[2]),
                co2_ppm=_num(raw[3]),
                lamp_state=state,
                lamp_on_s=on_s,
                lamp_off_s=off_s,
                lamp_on_m=on_m,
                lamp_off_m=off_m,
            )
        )
    return out


def _num(value) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
