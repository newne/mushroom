"""把生产系统的入库台账变成 ``room.yaml`` —— 补上"准入依据从哪来"这一步。

## 为什么需要它

``patrol.room`` 定义了准入**契约**（读不到入库日期就不动），但没人去读。现场于是出现
一个尴尬状态：要么手填日期（写错一次就是 60 张错照片进测量表），要么整份占位文件让
巡检永远不启动。

本模块只做两件事：**取数**与**落盘**，判定仍旧归 ``patrol.room``（同一份规则，不复制）。

## 数据源

``mogu.mo_gu_batch``（生产系统库房批次表，实测 2026-09-13）：

===================================  ==========================================
``code_num``                         库房号，与 ``device_register_ledger.installation_site``
                                     的「611库」前缀一致（611/612/7/8）
``in_time``                          **整库入库日期**——准入判定的唯一依据
``in_day_num``                       生产系统口径的在库天数，入库当天 = 01
``in_num``                           入库包数（9792）
``info_code``                        该库「育菇房信息」设备编号（611 ⇒ ``TD1_Q1MDINFO01``），
                                     库房与设备的稳定对应关系
===================================  ==========================================

口径差异要盯住：生产系统把入库当天记作第 **01** 天，我们内部记作第 **0** 天
（``patrol.room.RoomState.day_on``）。转换只发生在展示层，落盘的永远是 ``in_time``。

## 安全约束

* **未来日期一律拒绝**：入库日期比今天晚，说明查询打到了错库/错表或系统时间不对，
  写进去会让机构在"第 -N 天"这个不存在的状态下开跑。
* ``entry_date`` 变化会**明确打印**旧值→新值。它会变（换批次），但绝不该悄悄变。
* 原子落盘（同目录临时文件 + ``os.replace``），避免 daemon 读到半截 YAML。
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

from patrol.room import RoomState, RoomStateError, load_room_state

DEFAULT_CONTAINER = "tools_mysql"     # 现场 MySQL 跑在 docker 里（0.0.0.0:3306）
DEFAULT_DB = "mogu"

# 列顺序即契约：`parse_tsv` 按下标取值，改 SQL 必须同时改解析（两边写在一起就是为了这个）。
SQL = (
    "select id, code_num, in_time, in_day_num, in_num, info_code, update_time "
    "from mo_gu_batch where del_flag = 0 order by in_time desc"
)
COLUMNS = ("id", "code_num", "in_time", "in_day_num", "in_num", "info_code", "update_time")


class FetchError(RuntimeError):
    """取数或解析失败——调用方据此**不写文件**（宁可留着旧值，也不要写半份）。"""


@dataclass(frozen=True)
class BatchRow:
    """``mo_gu_batch`` 的一行（只保留准入用得上的字段）。"""

    batch_id: str
    room_id: str
    entry_date: date
    system_day_num: int | None = None    # 生产系统口径（入库当天 = 1）
    packages: int | None = None
    info_code: str = ""
    updated_at: str = ""

    @property
    def batch_no(self) -> str:
        """对账用批次号：能唯一回指生产系统那条记录。"""
        return f"mogu-{self.batch_id}"


def _cell(raw: str) -> str | None:
    """mysql ``-B`` 输出里 NULL 写作字面量 ``NULL``；其余为原始文本。"""
    text = raw.strip()
    return None if text in ("", "NULL", r"\N") else text


def _int(raw: str | None, *, field: str, row: str) -> int | None:
    if raw is None:
        return None
    try:
        return int(raw)
    except ValueError as e:
        raise FetchError(f"{field} 不是整数：{raw!r}（行：{row!r}）") from e


def parse_tsv(text: str) -> list[BatchRow]:
    """解析 ``mysql -N -B`` 的制表符输出（无表头，列序见 ``COLUMNS``）。

    字段数不对就报错而不是跳过：字段错位会静默把 ``in_day_num`` 当成 ``in_num``，
    这类错误只能靠拒绝解析来暴露。
    """
    rows: list[BatchRow] = []
    for lineno, line in enumerate(text.splitlines(), 1):
        if not line.strip():
            continue
        parts = line.split("\t")
        if len(parts) != len(COLUMNS):
            raise FetchError(
                f"第 {lineno} 行字段数 {len(parts)} ≠ {len(COLUMNS)}（{COLUMNS}）：{line!r}"
            )
        raw = dict(zip(COLUMNS, parts, strict=True))
        room = _cell(raw["code_num"])
        in_time = _cell(raw["in_time"])
        if room is None or in_time is None:
            raise FetchError(f"第 {lineno} 行缺 code_num/in_time：{line!r}")
        # in_time 在库里有 'YYYY-MM-DD' 与 'YYYY-MM-DD HH:MM:SS' 两种写法，都只取日期部分。
        text_date = in_time.split(" ", 1)[0]
        if len(text_date) != 10:
            raise FetchError(f"第 {lineno} 行 in_time 不是日期：{in_time!r}")
        try:
            entry = date.fromisoformat(text_date)
        except ValueError as e:
            raise FetchError(f"第 {lineno} 行 in_time 非法：{in_time!r}") from e
        rows.append(
            BatchRow(
                batch_id=_cell(raw["id"]) or "?",
                room_id=room,
                entry_date=entry,
                system_day_num=_int(_cell(raw["in_day_num"]), field="in_day_num", row=line),
                packages=_int(_cell(raw["in_num"]), field="in_num", row=line),
                info_code=_cell(raw["info_code"]) or "",
                updated_at=_cell(raw["update_time"]) or "",
            )
        )
    return rows


def select_batch(rows: list[BatchRow], room_id: str, *, today: date) -> BatchRow:
    """取该库房最新一条批次记录；没有、或在未来，都抛 ``FetchError``。

    「最新」按 ``entry_date`` 排（同一天多条时取 ``batch_id`` 最大者，保证结果稳定）。
    """
    mine = [r for r in rows if r.room_id == room_id]
    if not mine:
        known = sorted({r.room_id for r in rows})
        raise FetchError(
            f"生产系统里没有库房 {room_id!r} 的批次记录（现有库房：{known or '无'}）"
            "——读不到入库日期就不写文件"
        )
    row = max(mine, key=lambda r: (r.entry_date, int(r.batch_id) if r.batch_id.isdigit() else -1))
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
              f"(mo_gu_batch id={row.batch_id}, 在库第 {day} 天, {gate})")
    packages = row.packages if row.packages is not None else "null"
    sys_day = row.system_day_num if row.system_day_num is not None else "(空)"
    return (
        "# 库房在库状态——巡检准入门禁（由 deploy-fetch-room 生成，可手改）。\n"
        "#\n"
        "# 数据来源：生产系统库房批次表 mo_gu_batch\n"
        f"#   批次记录 id={row.batch_id}，info_code={row.info_code or '(空)'}，"
        f"系统记录 in_day_num={sys_day}\n"
        "#   系统把入库当天记作第 01 天；本文件按「入库当天 = 第 0 天」计算\n"
        f"#   本次判定：{reason}\n"
        f"#   本文件由脚本生成于 {now:%Y-%m-%d %H:%M:%S}\n"
        "\n"
        f"room_id: {row.room_id}\n"
        f"entry_date: {row.entry_date.isoformat()}\n"
        f"packages: {packages}\n"
        f"batch_no: {row.batch_no}\n"
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


# --------------------------------------------------------------------------- 取数


def read_tsv_from_docker(container: str, *, sql: str = SQL, db: str = DEFAULT_DB) -> str:
    """在 docker 里跑 mysql 客户端取数（口令只从容器环境读，不落命令行历史）。

    这是本模块**唯一**的不纯部分：``docker inspect`` + ``docker exec``。放在函数末尾，
    解析与判定都能脱离现场单测。
    """
    env = subprocess.run(
        ["docker", "inspect", container, "--format",
         "{{range .Config.Env}}{{println .}}{{end}}"],
        capture_output=True, text=True, errors="replace", check=False,
    )
    if env.returncode != 0:
        raise FetchError(f"读不到容器 {container} 的环境：{env.stderr.strip()}")
    pw = ""
    for line in env.stdout.splitlines():
        if line.startswith("MYSQL_ROOT_PASSWORD="):
            pw = line.split("=", 1)[1]
            break
    if not pw:
        raise FetchError(f"容器 {container} 里没有 MYSQL_ROOT_PASSWORD，无法连库")

    argv = ["docker", "exec", container, "mysql", "--default-character-set=utf8mb4",
            "-uroot", f"-p{pw}", db, "-N", "-B", "-e", sql]
    out = subprocess.run(argv, capture_output=True, text=True, errors="replace",
                         check=False)
    if out.returncode != 0:
        err = "\n".join(ln for ln in out.stderr.splitlines()
                        if "Using a password" not in ln)
        raise FetchError(f"查询失败（{container}）：{err.strip()}")
    return out.stdout


def read_tsv(source: str) -> str:
    """``-`` = stdin；``docker:<容器>`` = 现场库；其余按文件路径读。"""
    if source == "-":
        return sys.stdin.read()
    if source.startswith("docker:"):
        return read_tsv_from_docker(source.split(":", 1)[1])
    try:
        return Path(source).read_text(encoding="utf-8")
    except OSError as e:
        raise FetchError(f"读不到数据源 {source}：{e}") from e


# --------------------------------------------------------------------------- CLI


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="deploy-fetch-room",
        description="从生产系统台账取库房入库日期并写入 room.yaml（准入依据）",
    )
    ap.add_argument("--room", required=True, help="库房号（如 611）")
    ap.add_argument("--out", default="room.yaml", help="写到哪里（默认 ./room.yaml）")
    ap.add_argument("--from", dest="source", default=f"docker:{DEFAULT_CONTAINER}",
                    help="数据源：docker:<容器>（默认）、-（stdin）或 TSV 文件路径")
    ap.add_argument("--today", default=None, help="覆盖‘今天’（YYYY-MM-DD，仅用于演练）")
    ap.add_argument("--print", dest="print_only", action="store_true",
                    help="只打印，不写文件")
    return ap


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    today = date.fromisoformat(args.today) if args.today else date.today()
    try:
        rows = parse_tsv(read_tsv(args.source))
        row = select_batch(rows, args.room, today=today)
        text = render_room_yaml(row, today=today)
    except FetchError as e:
        print(f"! {e}", file=sys.stderr)
        return 2

    state = load_room_state_text(text)
    allowed, reason = state.verdict(today)
    print(f"库房 {row.room_id}：入库 {row.entry_date}（批次 {row.batch_no}，"
          f"{row.packages if row.packages is not None else '?'} 包）")
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
