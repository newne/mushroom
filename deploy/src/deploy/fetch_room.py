"""把批次台账变成 ``room.yaml`` —— 补上"准入依据从哪来"这一步。

## 为什么需要它

``patrol.room`` 定义了准入**契约**（读不到入库日期就不动），但没人去读。现场于是出现
一个尴尬状态：要么手填日期（写错一次就是 48 张错照片进测量表），要么整份占位文件让
巡检永远不启动。

本模块只做两件事：**取数**与**落盘**，判定仍旧归 ``patrol.room``（同一份规则，不复制）。

## 数据源：算法侧的只读端点（不碰数据库）

用户 2026-10-14 裁定：**业务库禁止使用**（旧实现借别人的容器 + root 口令取数，那条路
已整条删除，见票 17）。现在取数走**算法侧的只读 HTTP 端点**：

    GET {base}/batches?room_id=611

``base`` 取 ``--analysis-url`` → 环境变量 ``PATROL_ANALYSIS`` → 默认 ``http://172.17.0.1:8000``
（与 console 访问算法侧同一套路：容器里走宿主网桥网关）。

**本模块不写 SQL、不解析连接串、不 import 任何数据库驱动**（连"第二条口径"的可能性一起掐掉）：
台账的成员与刷新由端点负责，端点还负责把 ``stale`` 一起报回来。

## 端点给的字段（三个名字别混，Chief 2026-10-14 裁定）

| 字段 | 含义 | 本模块怎么用 |
| --- | --- | --- |
| ``batch_id`` | ``<规范库房号>|<入库日期>``（ADR-0020） | 与 ``(room_id, entry_date)`` 推出的值**逐字核对**，不一致就报错 |
| ``batch_no`` | **派生名** ``<规范库房号>_<YYYYMMDD>`` | **直接用端点给的**，不自己拼 |
| ``display_name`` | 业务名 ``mogu-*``；缺映射 ⇒ NULL | 原样落进 ``room.yaml``，**缺就是 null，不编** |

## 准入规则（fail-closed）

* ``stale = true`` ⇒ **明确报"台账陈旧"、保留旧 ``room.yaml``**（阈值由端点给，当前 26h）：
  陈旧台账判不了准入，**绝不静默用旧值**；端点没给 ``stale`` 也拒绝继续（没法判断就不动）。
* 取不到 / ``ok≠true`` / 结构不对 / 未来日期 / 库房不存在 ⇒ 报错且**一个字节都不写**。
* ``entry_date`` 变化会**明确打印**旧值→新值。它会变（换批次），但绝不该悄悄变。
* 原子落盘（同目录临时文件 + ``os.replace``），避免 daemon 读到半截 YAML。
"""

from __future__ import annotations

import argparse
import os
import sys
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

from patrol.room import RoomState, RoomStateError, load_room_state

#: 与 console 的 `PATROL_ANALYSIS` 同名同义：容器里用宿主网桥网关访问算法侧。
ANALYSIS_URL_ENV = "PATROL_ANALYSIS"
DEFAULT_ANALYSIS_URL = "http://172.17.0.1:8000"
BATCHES_PATH = "/batches"
HTTP_TIMEOUT_S = 10.0


class FetchError(RuntimeError):
    """取数或解析失败——调用方据此**不写文件**（宁可留着旧值，也不要写半份）。"""


@dataclass(frozen=True)
class BatchRow:
    """端点 ``batches[]`` 里的一行（只保留准入用得上的字段）。"""

    room_id: str
    entry_date: date
    #: **派生名** `<room_id>_<YYYYMMDD>`——由端点给，本模块不自己拼。
    batch_no: str | None = None
    #: 业务名 `mogu-*`（端点字段 `display_name`）：缺映射 ⇒ None，**不编**。
    display_name: str | None = None
    #: 入库包数：台账里没有这个量 ⇒ 一律 None（**未知**，不是 0）。
    packages: int | None = None

    @property
    def batch_id(self) -> str:
        """批次身份（ADR-0020）：``<room_id>|<entry_date>``——与端点同式。"""
        return f"{self.room_id}|{self.entry_date.isoformat()}"


# --------------------------------------------------------------------------- 取数（HTTP，只读）

def analysis_base_url(url: str | None = None) -> str:
    """端点基址：``--analysis-url`` → ``PATROL_ANALYSIS`` → 默认宿主网关。"""
    for candidate in (url, os.environ.get(ANALYSIS_URL_ENV)):
        text = (candidate or "").strip()
        if text:
            return text.rstrip("/")
    return DEFAULT_ANALYSIS_URL


def _http_get_batches(base: str, room_id: str) -> dict:
    """真的发一次只读 GET；``httpx`` 是 deploy 既有依赖（不新增驱动）。"""
    try:
        import httpx
    except ImportError as exc:  # pragma: no cover - deploy 依赖里就有
        raise FetchError(f"环境里没有 httpx：{exc}") from exc
    url = f"{base}{BATCHES_PATH}"
    try:
        resp = httpx.get(url, params={"room_id": room_id}, timeout=HTTP_TIMEOUT_S)
    except Exception as exc:
        raise FetchError(f"读批次端点失败（{url}）：{exc}") from exc
    if resp.status_code != 200:
        raise FetchError(f"读批次端点失败（{url}）：HTTP {resp.status_code}")
    try:
        payload = resp.json()
    except Exception as exc:
        raise FetchError(f"批次端点返回的不是 JSON（{url}）：{exc}") from exc
    return payload


def fetch_ledger_payload(room_id: str, *, url: str | None = None, runner=None,
                         log=None) -> dict:
    """取端点载荷并做**结构级** fail-closed 校验；``runner`` 可注入（测试用）。"""
    base = analysis_base_url(url)
    payload = (runner or (lambda room: _http_get_batches(base, room)))(room_id)
    if not isinstance(payload, dict):
        raise FetchError(f"批次端点返回的不是对象：{payload!r}")
    if payload.get("ok") is not True:
        raise FetchError(f"批次端点 ok≠true（{payload.get('ok')!r}）：{payload.get('error') or ''}".strip())
    if "stale" not in payload:
        raise FetchError(
            "批次端点没给 stale：判断不了台账是否陈旧 ⇒ 拒绝用它判准入（宁可不动）"
        )
    if payload["stale"] is True:
        raise FetchError(
            "台账陈旧（stale=true）：拒绝用它判准入；保留旧 room.yaml，等台账刷新后再取"
        )
    batches = payload.get("batches")
    if not isinstance(batches, list):
        raise FetchError(f"批次端点没给 batches 列表：{batches!r}")
    _log_ledger_meta(payload, log=log)
    return payload


def _log_ledger_meta(payload: dict, *, log=None) -> None:
    """把端点的台账元数据记一行日志（**滞后/剪除/去重必须看得见**，不许静默）。

    缺字段时同样打一条告警：否则"没日志"会被读成"没有差异"。
    """
    if log is None:
        log = print
    refreshed = payload.get("ledger_refreshed_at")
    rows = payload.get("ledger_n_rows")
    pruned = payload.get("ledger_n_pruned")
    dups = payload.get("duplicates")
    if refreshed is None and rows is None:
        log("！批次端点没给台账元数据（ledger_refreshed_at/ledger_n_rows）：滞后就看不见了")
        return
    extra = f"，去重 {dups}" if dups else ""
    log(f"台账：刷新于 {refreshed or '(未给)'}，行数 {rows if rows is not None else '(未给)'}，"
        f"剪除 {pruned if pruned is not None else '(未给)'}{extra}，stale=False")


def rows_from_payload(payload: dict) -> list[BatchRow]:
    """端点载荷 → `BatchRow` 列表；三个名字按裁定逐条核对，错位即报错。"""
    out: list[BatchRow] = []
    for lineno, item in enumerate(payload.get("batches") or [], 1):
        if not isinstance(item, dict):
            raise FetchError(f"第 {lineno} 条批次不是对象：{item!r}")
        room = str(item.get("room_id") or "").strip()
        if not room:
            raise FetchError(f"第 {lineno} 条批次缺 room_id：{item!r}")
        entry = _as_date(item.get("entry_date"))
        batch_no = str(item.get("batch_no") or "").strip()
        if not batch_no:
            raise FetchError(
                f"第 {lineno} 条批次缺 batch_no（派生名）——**不许自己拼**：{item!r}"
            )
        expected_id = f"{room}|{entry.isoformat()}"
        got_id = str(item.get("batch_id") or "").strip()
        if got_id and got_id != expected_id:
            raise FetchError(
                f"第 {lineno} 条批次的 batch_id={got_id!r} 与 (room_id, entry_date) 推出的 "
                f"{expected_id!r} 不一致——命名口径对不上，拒绝继续"
            )
        display = item.get("display_name")
        out.append(BatchRow(
            room_id=room,
            entry_date=entry,
            batch_no=batch_no,
            display_name=(str(display).strip() or None) if display is not None else None,
        ))
    out.sort(key=lambda r: (r.room_id, r.entry_date))
    return out


def fetch_ledger_rows(room_id: str, *, url: str | None = None, runner=None,
                      log=None) -> list[BatchRow]:
    """取数一步到位：端点载荷 → `BatchRow` 列表。"""
    payload = fetch_ledger_payload(room_id, url=url, runner=runner, log=log)
    return rows_from_payload(payload)


def _as_date(value) -> date:
    """``date``/``datetime``/ISO 字符串 → ``date``；解析不了即报错（**不猜**）。"""
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value or "").strip()[:10]        # 端点给日期；带时分秒也接受
    try:
        return date.fromisoformat(text)
    except ValueError as exc:
        raise FetchError(f"入库日期不合法：{value!r}") from exc


# --------------------------------------------------------------------------- 离线 TSV（演练/无库环境）


def parse_tsv(text: str) -> list[BatchRow]:
    """解析**离线台账** TSV：每行两列 ``room_id<TAB>in_date``。

    这是给演练与无库环境用的旁路（``--from``），**不是**取数通道：它不连任何库、
    也不连端点。**离线旁路的 `batch_no` 在本机派生**（照端点同式），生产路径一律用端点给的。
    """
    rows: list[BatchRow] = []
    for lineno, line in enumerate(text.splitlines(), 1):
        if not line.strip():
            continue
        parts = line.split("\t")
        if len(parts) != 2:
            raise FetchError(f"第 {lineno} 行有 {len(parts)} 列，期望 2 列（room_id, in_date）：{line!r}")
        room = parts[0].strip()
        if room in ("", "NULL", r"\N"):
            raise FetchError(f"第 {lineno} 行缺 room_id：{line!r}")
        entry = _as_date(parts[1].strip())
        rows.append(BatchRow(room_id=room, entry_date=entry,
                             batch_no=f"{room}_{entry.strftime('%Y%m%d')}"))
    return rows


def read_tsv(source: str) -> str:
    """``-`` = stdin；其余按文件路径读。"""
    if source == "-":
        return sys.stdin.read()
    try:
        return Path(source).read_text(encoding="utf-8")
    except OSError as e:
        raise FetchError(f"读不到数据源 {source}：{e}") from e


# --------------------------------------------------------------------------- 判定与落盘


def select_batch(rows: list[BatchRow], room_id: str, *, today: date) -> BatchRow:
    """取该库房最新一条批次记录；没有、或在未来，都抛 ``FetchError``。

    「最新」按 ``entry_date`` 排（同一天多条时取 ``batch_no`` 最大者，保证结果稳定）。
    """
    mine = [r for r in rows if r.room_id == room_id]
    if not mine:
        known = sorted({r.room_id for r in rows})
        raise FetchError(
            f"台账里没有库房 {room_id!r} 的批次记录（现有库房：{known or '无'}）"
            "——读不到入库日期就不写文件"
        )
    row = max(mine, key=lambda r: (r.entry_date, r.batch_no or ""))
    if row.entry_date > today:
        raise FetchError(
            f"库房 {room_id} 的入库日期 {row.entry_date} 在今天（{today}）之后——"
            "数据可疑，拒绝写入（写进去会让机构按不存在的在库天数开跑）"
        )
    return row


def refresh_room_file(room_id: str, path: str | Path, *, url: str | None = None,
                      runner=None, log=None, today: date | None = None) -> dict:
    """**轮次前的就地刷新**：取台账 → 需要才写盘；**永不抛**（票 18）。

    调用方（`patrol-serve` 的轮次入口）按返回的 ``error`` 打告警 + 计数，
    **失败也继续跑这一轮**（停轮会丢掉连续影像；"批次刚换 + 端点恰好不可用"的窗口
    会被下一次成功轮询在 ≤1 个轮间隔内抓到）。

    返回：``{"changed", "entry_date", "previous_entry_date", "note", "error"}``

    行为（Chief 2026-10-14 裁定的硬边界）：
    * 批次**变了** ⇒ 原子更新 + 打印旧→新；
    * **未变** ⇒ **幂等不写**（不抖 mtime）；
    * 端点不可用 / `stale=true` / 未来日期 ⇒ **不写**，保留旧文件（沿用最后已知批次继续跑）；
    * 文件**缺失/坏** + 端点可用 ⇒ 写一份（首次接入）；+ 端点不可用 ⇒ 不写
      （此时 daemon 读不到 room.yaml，按既有契约不动机构——fail-closed）。
    """
    today = today or date.today()
    result: dict = {"changed": False, "entry_date": None, "previous_entry_date": None,
                    "note": "", "error": None}
    previous = _current_entry_date(path)
    result["previous_entry_date"] = previous
    try:
        rows = fetch_ledger_rows(room_id, url=url, runner=runner, log=log)
        row = select_batch(rows, room_id, today=today)
    except FetchError as e:
        result["error"] = str(e)
        result["entry_date"] = previous          # 沿用最后已知批次
        return result
    result["entry_date"] = row.entry_date.isoformat()
    if previous == result["entry_date"]:
        result["note"] = f"entry_date 未变（{previous}）——幂等，不写文件"
        return result
    try:
        result["note"] = write_room_yaml(path, render_room_yaml(row, today=today))
        result["changed"] = True
    except FetchError as e:                      # 生成的 YAML 过不了消费方校验
        result["error"] = str(e)
    return result


def _current_entry_date(path: str | Path) -> str | None:
    """旧文件里的入库日期；**缺失或坏 ⇒ None**（刷新按"要写一份新的"处理）。"""
    try:
        return load_room_state(path).entry_date.isoformat()
    except (RoomStateError, OSError):
        return None


def render_room_yaml(row: BatchRow, *, today: date, now: datetime | None = None) -> str:
    """生成 ``room.yaml``：只写 ``patrol.room`` 认得的字段，外加人读注释。"""
    now = now or datetime.now()
    state = RoomState(room_id=row.room_id, entry_date=row.entry_date)
    day = state.day_on(today)
    allowed, reason = state.verdict(today)
    gate = "准入" if allowed else "不巡检"
    source = (f"deploy-fetch-room {now:%Y-%m-%d %H:%M:%S} "
              f"(analysis /batches, 身份 {row.batch_id}, 在库第 {day} 天, {gate})")
    packages = row.packages if row.packages is not None else "null"
    display = f'"{row.display_name}"' if row.display_name else "null"
    return (
        "# 库房在库状态——巡检准入门禁（由 deploy-fetch-room 生成，可手改）。\n"
        "#\n"
        "# 数据来源：算法侧只读端点 GET /batches?room_id=…（本模块不碰数据库）\n"
        f"#   批次身份 = (room_id, entry_date) = ({row.room_id}, {row.entry_date.isoformat()})"
        "（ADR-0020）\n"
        f"#   batch_no 是**派生名**（端点给的）；display_name 是业务名 mogu-*，缺映射即 null，不编\n"
        "#   系统把入库当天记作第 01 天；本文件按「入库当天 = 第 0 天」计算"
        f"（本行对应系统口径第 {day + 1} 天）\n"
        "#   packages 台账里没有这个量 ⇒ null（未知，不是 0）\n"
        f"#   本次判定：{reason}\n"
        f"#   本文件由脚本生成于 {now:%Y-%m-%d %H:%M:%S}\n"
        "\n"
        f"room_id: {row.room_id}\n"
        f"entry_date: {row.entry_date.isoformat()}\n"
        f"packages: {packages}\n"
        f'batch_no: "{row.batch_no}"\n'
        f"display_name: {display}\n"
        f'source: "{source}"\n'
    )


def write_room_yaml(path: str | Path, text: str) -> str:
    """原子落盘并返回人读的变更说明（旧值 → 新值）。

    原子性是必须的：daemon 每轮都读这个文件，读到半截 YAML 会被判成
    ``RoomStateError`` ⇒ 当轮不巡检（安全但白跑一轮）。
    """
    p = Path(path)
    old = ""
    if p.exists():
        try:
            old = load_room_state(p).entry_date.isoformat()
        except RoomStateError as e:
            old = f"(旧文件不可用: {e})"
    # 先校验自己写的东西确实能被消费者接受，再落盘——不留"写进去但读不出来"的状态。
    try:
        parsed = load_room_state_text(text)
    except RoomStateError as e:      # pragma: no cover - 兜底，正常路径不会触发
        raise FetchError(f"生成的 YAML 通不过 patrol.room 校验，拒绝落盘：{e}") from e
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + f".tmp{os.getpid()}")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, p)
    new = parsed.entry_date.isoformat()
    if not old:
        return f"已写入 {p}（新建）：entry_date={new}"
    if old == new:
        return f"已写入 {p}：entry_date 未变（{new}）"
    return f"已写入 {p}：entry_date {old} → {new}（换批次了？确认无误）"


def load_room_state_text(text: str) -> RoomState:
    """从 YAML 文本构造 ``RoomState``（与 ``load_room_state`` 同一套校验）。"""
    import yaml
    from patrol.room import parse_room_state

    try:
        payload = yaml.safe_load(text)
    except yaml.YAMLError as e:      # pragma: no cover - 我们自己生成的文本
        raise RoomStateError(f"YAML 解析失败：{e}") from e
    return parse_room_state(payload)


# --------------------------------------------------------------------------- CLI


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="deploy-fetch-room",
        description="从算法侧只读端点取库房入库日期并写入 room.yaml（准入依据）",
    )
    ap.add_argument("--room", required=True, help="库房号（如 611）")
    ap.add_argument("--out", default="room.yaml", help="写到哪里（默认 ./room.yaml）")
    ap.add_argument("--analysis-url", default=None,
                    help=f"算法侧基址（默认读 {ANALYSIS_URL_ENV}，再默认 {DEFAULT_ANALYSIS_URL}）")
    ap.add_argument("--from", dest="source", default=None,
                    help="**离线**台账 TSV（每行 room_id<TAB>in_date；'-' = stdin）——"
                         "只用于演练/无库环境；默认走只读端点")
    ap.add_argument("--today", default=None, help="覆盖‘今天’（YYYY-MM-DD，仅用于演练）")
    ap.add_argument("--print", dest="print_only", action="store_true",
                    help="只打印，不写文件")
    return ap


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    today = date.fromisoformat(args.today) if args.today else date.today()
    try:
        if args.source:
            rows = parse_tsv(read_tsv(args.source))
        else:
            rows = fetch_ledger_rows(args.room, url=args.analysis_url)
        row = select_batch(rows, args.room, today=today)
        text = render_room_yaml(row, today=today)
    except FetchError as e:
        print(f"! {e}", file=sys.stderr)
        return 2

    state = load_room_state_text(text)
    allowed, reason = state.verdict(today)
    packages = row.packages if row.packages is not None else "—"
    display = row.display_name or "—"
    print(f"库房 {row.room_id}：入库 {row.entry_date}（批次 {row.batch_no}，"
          f"业务名 {display}，包数 {packages}）")
    print(f"准入判定：{reason} ⇒ {'本轮会巡检' if allowed else '本轮不巡检'}")

    if args.print_only:
        print(text)
        return 0
    try:
        print(write_room_yaml(args.out, text))
    except FetchError as e:
        print(f"! {e}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
