"""操作日志：环控平台的命令审计（运行经验的原始记录）。

2026-10-12 用户指出："环境分析中没有体现操作员的实际操作，这些数据是运行经验的
体现，值得具体分析展示"——并给出接口。现场实测：

* 登录：`POST /service/security/auth/login`，`{"accountCode","password"}`，
  **口令要 MD5**；回 `data.accessToken`；
* 查询：`POST /service/monitor/command/audit/log/page/list`，带 `X-AUTH-TOKEN`；
* 一行一次操作：`operator` / `operateTime` / `deviceName` / `pointName` /
  `pointValue` / `actionSource` / `actionContent`。

**这类记录不聚合**：一天几十条，事件本身就是最小有意义单位。聚合成"每天改了几次"
之后就答不出"那天 10:10 谁把右加湿器的开启设定改成了 5"——而那句话正是要学的经验。
与实测环境数据的分界在这里：实测是每分钟一条的连续量，只留小时聚合（ADR-0023）；
操作是稀疏事件，逐条留。
"""

from __future__ import annotations

import base64
import hashlib
import json
import urllib.request
from datetime import datetime, timedelta

#: 设备号前缀 → 库房号。`TD1_Q3MDHUM02` 里的 `Q3` 就是库房位置，
#: 与 `analysis.env.tdengine.ROOM_DEVICE_CODES` 同一套编码。
ROOM_BY_DEVICE_PREFIX: dict[str, str] = {
    "TD1_Q1": "611",
    "TD1_Q2": "612",
    "TD1_Q3": "607",
    "TD1_Q4": "608",
}

LOGIN_PATH = "/service/security/auth/login"
AUDIT_PATH = "/service/monitor/command/audit/log/page/list"
AUTH_FAILED_CODE = 400100


class AuthError(RuntimeError):
    """token 失效/未登录。调用方据此重新登录一次，而不是把空列表当成"这段时间没操作"。"""


def md5_hex(text: str) -> str:
    return hashlib.md5(text.encode("utf-8")).hexdigest()


def room_of(device_code: str) -> str | None:
    code = (device_code or "").strip()
    for prefix, room in ROOM_BY_DEVICE_PREFIX.items():
        if code.startswith(prefix):
            return room
    return None


def norm_ts(value: str) -> str:
    """`2026-10-12 10:10:00` → `2026-10-12T10:10:00`（与库里其它表同口径）。"""
    text = str(value or "").strip().replace(" ", "T")
    return text[:19]


def default_transport(url: str, body: dict, *, headers: dict | None = None, timeout_s: float = 20.0) -> dict:
    req = urllib.request.Request(
        url, data=json.dumps(body).encode("utf-8"), method="POST"
    )
    req.add_header("Content-Type", "application/json")
    for key, value in (headers or {}).items():
        req.add_header(key, value)
    with urllib.request.urlopen(req, timeout=timeout_s) as resp:  # noqa: S310 - 地址来自部署配置
        return json.loads(resp.read().decode("utf-8"))


def _call(transport, url: str, body: dict, headers: dict | None = None) -> dict:
    payload = transport(url, body, headers=headers) if transport is not None else default_transport(
        url, body, headers=headers
    )
    if not isinstance(payload, dict):
        raise ValueError(f"环控平台响应不是对象：{payload!r}")
    code = payload.get("code")
    if code == AUTH_FAILED_CODE:
        raise AuthError(str(payload.get("message") or "认证失败"))
    if code != 200:
        raise RuntimeError(f"环控平台查询失败 code={code} message={payload.get('message')!r}")
    return payload.get("data") or {}


def login(base_url: str, account: str, password_md5: str, *, transport=None) -> str:
    """取 accessToken。口令只接受 MD5（明文会回 400200 帐号或密码错误）。"""
    data = _call(
        transport,
        base_url.rstrip("/") + LOGIN_PATH,
        {"accountCode": account, "password": password_md5},
    )
    token = data.get("accessToken")
    if not token:
        raise AuthError("登录成功但没拿到 accessToken")
    return str(token)


def parse_operations(payload_data: dict) -> list[dict]:
    """把一页审计记录摊平成入库行。字段缺失一律留空，不猜。"""
    rows: list[dict] = []
    for item in payload_data.get("list") or []:
        device_code = str(item.get("deviceCode") or "")
        ts = norm_ts(item.get("operateTime"))
        if not ts or not device_code:
            continue
        rows.append({
            "ts": ts,
            "zone": room_of(device_code) or "",
            "device_code": device_code,
            "device_name": str(item.get("deviceName") or ""),
            "point_code": str(item.get("pointCode") or ""),
            "point_name": str(item.get("pointName") or ""),
            "value": "" if item.get("pointValue") is None else str(item.get("pointValue")),
            "operator": str(item.get("operator") or ""),
            "source": str(item.get("actionSource") or ""),
            "content": str(item.get("actionContent") or ""),
        })
    return rows


def fetch_operations(
    base_url: str,
    token: str,
    start: str,
    end: str,
    *,
    page_size: int = 200,
    max_pages: int = 50,
    transport=None,
) -> list[dict]:
    """按时间窗翻页拉操作日志。`start`/`end` 是 `YYYY-MM-DD HH:MM:SS`。"""
    url = base_url.rstrip("/") + AUDIT_PATH
    headers = {"X-AUTH-TOKEN": token, "X-AUTH-CLIENT": "web"}
    rows: list[dict] = []
    for page in range(1, max_pages + 1):
        data = _call(transport, url, {
            "operateTimeStart": start, "operateTimeEnd": end,
            "deviceName": "", "operator": "", "actionModel": "",
            "pageNum": page, "pageSize": page_size,
        }, headers)
        page_rows = parse_operations(data)
        rows.extend(page_rows)
        total = int(data.get("total") or 0)
        if not page_rows or len(rows) >= total:
            break
    rows.sort(key=lambda r: (r["ts"], r["device_code"], r["point_code"]))
    return rows


def summarize_operations(rows: list[dict]) -> dict:
    """按天 / 设备 / 点位 / 操作人汇总。

    "各生长阶段、长势背景下库房通常怎么调"就是从这几个计数里读出来的；
    但**原始条目不能因此丢掉**——汇总答不出"几点几分谁改成了几"。
    """
    from collections import Counter

    def tally(key: str) -> dict:
        counts = Counter(str(r.get(key) or "未知") for r in rows)
        return dict(counts.most_common())

    return {
        "n": len(rows),
        "first_ts": rows[0]["ts"] if rows else None,
        "last_ts": rows[-1]["ts"] if rows else None,
        "by_day": dict(sorted(Counter(str(r["ts"])[:10] for r in rows if r.get("ts")).items(),
                              reverse=True)),
        "by_device": tally("device_name"),
        "by_point": tally("point_name"),
        "by_operator": tally("operator"),
        "by_zone": tally("zone"),
    }


def window(lookback_minutes: int, *, now: datetime | None = None) -> tuple[str, str]:
    """审计接口要 `YYYY-MM-DD HH:MM:SS` 的本地时间。"""
    end = now or datetime.now()
    start = end - timedelta(minutes=lookback_minutes)
    fmt = "%Y-%m-%d %H:%M:%S"
    return start.strftime(fmt), end.strftime(fmt)
