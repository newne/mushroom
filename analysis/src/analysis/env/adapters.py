"""环境数据源适配器。

数据源为库房现有环控系统（spec §2.3）。接口形式（HTTP API / Modbus / 数据库直读）
待 P1 现场确认——确认后只需新增/调整对应 adapter，ingest 与分析层不动。
"""

from __future__ import annotations

import csv
import glob
import json
import os
from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class EnvRow:
    ts: str
    zone: str
    temp_c: float | None = None
    rh_pct: float | None = None
    co2_ppm: float | None = None
    light_lux: float | None = None


class EnvSource(Protocol):
    def fetch_since(self, since_ts: str | None) -> list[EnvRow]: ...


def _to_float(v) -> float | None:
    if v is None or v == "":
        return None
    return float(v)


class CsvEnvSource:
    """读取目录下的 CSV 文件（列：ts,zone,temp_c,rh_pct,co2_ppm,light_lux）。

    现场接口确认前的默认通道：环控系统若只能导出/落盘 CSV，用本适配器接入。
    """

    def __init__(self, dir_path: str, pattern: str = "*.csv") -> None:
        self.dir_path = dir_path
        self.pattern = pattern

    def fetch_since(self, since_ts: str | None) -> list[EnvRow]:
        rows: list[EnvRow] = []
        for path in sorted(glob.glob(os.path.join(self.dir_path, self.pattern))):
            with open(path, newline="", encoding="utf-8") as fh:
                for rec in csv.DictReader(fh):
                    ts = (rec.get("ts") or "").strip()
                    if not ts or (since_ts and ts <= since_ts):
                        continue
                    rows.append(
                        EnvRow(
                            ts=ts,
                            zone=(rec.get("zone") or "default").strip(),
                            temp_c=_to_float(rec.get("temp_c")),
                            rh_pct=_to_float(rec.get("rh_pct")),
                            co2_ppm=_to_float(rec.get("co2_ppm")),
                            light_lux=_to_float(rec.get("light_lux")),
                        )
                    )
        return rows


class HttpEnvSource:
    """HTTP 接口源：本模块只负责**解析**，不直接发起网络请求。

    现有环控系统的接口形式（URL/鉴权/字段）待 P1 现场确认（票 03 验收项）。
    确认后由部署侧注入 patrol.links.Transport 形状的传输可调用对象
    （transport(url, *, params, body) -> dict，内部做 scheme/host 白名单校验防
    SSRF）；本类只通过该调用约定取数并解析为 EnvRow，不 import patrol。
    """

    def __init__(self, base_url: str, transport=None):
        self.base_url = base_url.rstrip("/")
        self._transport = transport  # links.Transport 形状（鸭子类型）

    def _url_with(self, since_ts: str | None) -> str:
        if not since_ts:
            return self.base_url
        sep = "&" if "?" in self.base_url else "?"
        return f"{self.base_url}{sep}since={since_ts}"

    def fetch_since(self, since_ts: str | None) -> list[EnvRow]:
        if self._transport is None:
            raise NotImplementedError(
                "环控系统 HTTP 接口形式待现场确认（票 03）；确认后注入 links.Transport"
            )
        payload = self._transport(self._url_with(since_ts))
        return self._parse(payload)

    @staticmethod
    def _parse(payload) -> list[EnvRow]:
        items = payload if isinstance(payload, list) else payload.get("rows", [])
        return [
            EnvRow(
                ts=str(it["ts"]),
                zone=str(it.get("zone", "default")),
                temp_c=_to_float(it.get("temp_c")),
                rh_pct=_to_float(it.get("rh_pct")),
                co2_ppm=_to_float(it.get("co2_ppm")),
                light_lux=_to_float(it.get("light_lux")),
            )
            for it in items
        ]


def row_to_dict(row: EnvRow) -> dict:
    return json.loads(json.dumps(row.__dict__))
