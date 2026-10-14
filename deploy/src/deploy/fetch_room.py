"""把批次台账变成 ``room.yaml`` —— 补上"准入依据从哪来"这一步。

## 为什么需要它

``patrol.room`` 定义了准入**契约**（读不到入库日期就不动），但没人去读。现场于是出现
一个尴尬状态：要么手填日期（写错一次就是 48 张错照片进测量表），要么整份占位文件让
巡检永远不启动。

本模块只做两件事：**取数**与**落盘**，判定仍旧归 ``patrol.room``（同一份规则，不复制）。

## 数据源：复用权威源，不自写第二套口径

用户 2026-10-14 裁定：**业务库禁止使用**（旧实现借别人的容器 + root 口令取数，那条路
已整条删除，发票 17）。取数改为 **pgsql**（``mushroom_algorithm``），并且**复用**
``src/utils/batch_yield_service.get_batch_ranges`` —— 批次口径**只有一个来源**：
``(room_id, in_date)``。本模块**不自己写 SQL、不自己解析连接串**：那等于造第二套口径，
正是这次要消灭的东西。

代价（已报 Chief / 发版官评估）：巡检镜像要带 ``src/**`` 与 sqlalchemy。所以这里的 import
**全部惰性**，拿不到就报一条指名道姓的错，**且不退回任何自写取数**。

## 口径（哪些字段来自哪里）

* ``room_id`` / ``entry_date`` ← 权威源的 ``(room_id, in_date)``；
* ``batch_no`` = **派生**显示名 ``<room_id>_<YYYYMMDD>``（与权威源里已有的写法
  ``batch_yield_service.resolve_setpoint_batch_info`` 同一格式，如 ``611_20260930``）；
  旧值 ``mogu-<业务库主键>`` **不再使用**（那是被禁用的业务库主键）；
* ``packages``（入库包数）：pgsql 侧没有对应列、也无法可靠派生 ⇒ **写 NULL（未知）**，
  **绝不写 0**；
* 批次**身份** = ``room_id|entry_date``（ADR-0020），写进 ``source`` 注；
* 生产系统的"在库第 N 天"（入库当天 = 01）由日期派生（= 我们的天数 + 1）。

## 安全约束

* **未来日期一律拒绝**：入库日期比今天晚，说明查询打到了错库/错表或系统时间不对，
  写进去会让机构在"第 -N 天"这个不存在的状态下开跑。
* ``entry_date`` 变化会**明确打印**旧值→新值。它会变（换批次），但绝不该悄悄变。
* 原子落盘（同目录临时文件 + ``os.replace``），避免 daemon 读到半截 YAML。
* 取不到台账 ⇒ **一个字节都不写**（宁可留着旧值，也不要写半份）。
"""

from __future__ import annotations

import argparse
import os
import sys
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

from patrol.room import RoomState, RoomStateError, load_room_state


class FetchError(RuntimeError):
    """取数或解析失败——调用方据此**不写文件**（宁可留着旧值，也不要写半份）。"""


@dataclass(frozen=True)
class BatchRow:
    """台账里的一行（只保留准入用得上的字段）。"""

    room_id: str
    entry_date: date
    #: 入库包数。权威源里没有这个量 ⇒ 现存一律 None（**未知**，不是 0）。
    #: 字段保留是为了 `room.yaml` 的形状不随取数源变（消费方 `patrol.room` 只看 `entry_date`）。
    packages: int | None = None

    @property
    def batch_id(self) -> str:
        """批次身份（ADR-0020）：``<room_id>|<entry_date>``。"""
        return f"{self.room_id}|{self.entry_date.isoformat()}"

    @property
    def batch_no(self) -> str:
        """批次**显示名**：``<room_id>_<YYYYMMDD>``（派生，不是业务库主键）。

        格式取自权威源自己已经在用的写法（``batch_yield_service`` 里
        ``batch_id = f"{room_id}_{in_date:%Y%m%d}"``），不另造一套命名。
        """
        return f"{self.room_id}_{self.entry_date.strftime('%Y%m%d')}"


# --------------------------------------------------------------------------- 取数（权威源）

def ledger_rows_via_service(room_id: str, *, session=None) -> list[tuple]:
    """**唯一**取数实现：调 ``utils.batch_yield_service.get_batch_ranges``。

    返回 ``[(room_id, in_date), …]``。``session`` 可注入（测试用）；不注入时用权威源
    自己的 pgsql engine（``global_const.global_const.pgsql_engine``）——连接串口径也归它，
    本模块**不解析 DSN**。
    """
    if session is None:
        try:
            from global_const.global_const import pgsql_engine
            from sqlalchemy.orm import sessionmaker
        except ImportError as exc:
            raise FetchError(
                f"巡检环境缺少权威取数依赖（{exc}）：镜像要带 `src/**` 与 sqlalchemy（票 17 §2/§4）"
            ) from exc
        session = sessionmaker(bind=pgsql_engine)()
    try:
        from utils.batch_yield_service import get_batch_ranges
    except ImportError as exc:
        raise FetchError(
            "巡检环境没有 `utils.batch_yield_service`：取数口径只有这一处，"
            "镜像必须带 `src/**`（票 17 §2/§4）——**不退回自写 SQL**"
        ) from exc
    try:
        items = get_batch_ranges(session, room_ids=[room_id])
    except Exception as exc:      # 读不到就报错，绝不返回空表
        raise FetchError(f"读 pgsql 台账失败：{exc}") from exc
    return [(it["room_id"], it["in_date"]) for it in items]


def fetch_ledger_rows(room_id: str, *, runner=None) -> list[tuple]:
    """取台账原始行 ``(room_id, in_date)``。

    ``runner`` 可注入（测试用；也是将来若改走算法侧只读端点时的**唯一**替换点，票 17 §2）。
    默认 = 复用权威源，不自写 SQL。
    """
    run = runner if runner is not None else ledger_rows_via_service
    return list(run(room_id))


def _as_date(value) -> date:
    """``date``/``datetime``/ISO 字符串 → ``date``；解析不了即报错（**不猜**）。"""
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value or "").strip()[:10]        # 库里两种写法：带/不带时分秒，都只取日期
    try:
        return date.fromisoformat(text)
    except ValueError as exc:
        raise FetchError(f"入库日期不合法：{value!r}") from exc


def rows_to_batch_rows(rows: list) -> list[BatchRow]:
    """``(room_id, in_date)`` 行 → `BatchRow`；列数不对/缺库房/日期非法一律报错。

    列数不对时报错而不是跳过：列错位会静默把"入库日期"当成库房号，这类错误只能靠
    拒绝解析来暴露。
    """
    out: list[BatchRow] = []
    for lineno, raw in enumerate(rows, 1):
        try:
            parts = tuple(raw)
        except TypeError as exc:
            raise FetchError(f"第 {lineno} 行不是行式数据：{raw!r}") from exc
        if len(parts) != 2:
            raise FetchError(
                f"第 {lineno} 行有 {len(parts)} 列，期望 2 列（room_id, in_date）：{raw!r}"
            )
        room, in_date = parts
        if not room:
            raise FetchError(f"第 {lineno} 行缺 room_id：{raw!r}")
        out.append(BatchRow(room_id=str(room), entry_date=_as_date(in_date)))
    out.sort(key=lambda r: (r.room_id, r.entry_date))
    return out


# --------------------------------------------------------------------------- 离线 TSV（演练/无库环境）


def parse_tsv(text: str) -> list[BatchRow]:
    """解析**离线台账** TSV：每行两列 ``room_id<TAB>in_date``。

    这是给演练与无库环境用的旁路（`--from`），**不是**取数通道：它不连任何库，
    也不认识旧那套 7 列的业务库输出。
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
        rows.append(BatchRow(room_id=room, entry_date=_as_date(parts[1].strip())))
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
    row = max(mine, key=lambda r: (r.entry_date, r.batch_no))
    if row.entry_date > today:
        raise FetchError(
            f"库房 {room_id} 的入库日期 {row.entry_date} 在今天（{today}）之后——"
            "数据可疑，拒绝写入（写进去会让机构按不存在的在库天数开跑）"
        )
    return row


def render_room_yaml(row: BatchRow, *, today: date, now: datetime | None = None) -> str:
    """生成 ``room.yaml``：只写 ``patrol.room`` 认得的字段，外加人读注释。"""
    now = now or datetime.now()
    state = RoomState(room_id=row.room_id, entry_date=row.entry_date)
    day = state.day_on(today)
    allowed, reason = state.verdict(today)
    gate = "准入" if allowed else "不巡检"
    source = (f"deploy-fetch-room {now:%Y-%m-%d %H:%M:%S} "
              f"(pgsql mushroom_algorithm / batch_yield_service, 身份 {row.batch_id}, "
              f"在库第 {day} 天, {gate})")
    packages = row.packages if row.packages is not None else "null"
    return (
        "# 库房在库状态——巡检准入门禁（由 deploy-fetch-room 生成，可手改）。\n"
        "#\n"
        "# 数据来源：pgsql mushroom_algorithm 的批次台账（复用 batch_yield_service.get_batch_ranges）\n"
        f"#   批次身份 = (room_id, entry_date) = ({row.room_id}, {row.entry_date.isoformat()})；"
        "batch_no 是**派生**显示名（业务库已禁用，2026-10-14 裁定）\n"
        "#   系统把入库当天记作第 01 天；本文件按「入库当天 = 第 0 天」计算"
        f"（本行对应系统口径第 {day + 1} 天）\n"
        "#   packages 在 pgsql 台账里没有对应列、也无法可靠派生 ⇒ null（未知，不是 0）\n"
        f"#   本次判定：{reason}\n"
        f"#   本文件由脚本生成于 {now:%Y-%m-%d %H:%M:%S}\n"
        "\n"
        f"room_id: {row.room_id}\n"
        f"entry_date: {row.entry_date.isoformat()}\n"
        f"packages: {packages}\n"
        f'batch_no: "{row.batch_no}"\n'
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
        description="从批次台账（pgsql，复用权威源）取库房入库日期并写入 room.yaml（准入依据）",
    )
    ap.add_argument("--room", required=True, help="库房号（如 611）")
    ap.add_argument("--out", default="room.yaml", help="写到哪里（默认 ./room.yaml）")
    ap.add_argument("--from", dest="source", default=None,
                    help="**离线**台账 TSV（每行 room_id<TAB>in_date；'-' = stdin）——"
                         "只用于演练/无库环境；默认走权威取数源")
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
            rows = rows_to_batch_rows(fetch_ledger_rows(args.room))
        row = select_batch(rows, args.room, today=today)
        text = render_room_yaml(row, today=today)
    except FetchError as e:
        print(f"! {e}", file=sys.stderr)
        return 2

    state = load_room_state_text(text)
    allowed, reason = state.verdict(today)
    packages = row.packages if row.packages is not None else "—"
    print(f"库房 {row.room_id}：入库 {row.entry_date}（批次 {row.batch_no}，包数 {packages}）")
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
