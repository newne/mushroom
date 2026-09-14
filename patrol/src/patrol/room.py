"""库房在库状态与巡检准入（这批蘑菇能不能拍）。

## 业务规则（2026-09-13 用户定）

| 在库天数 | 是否巡检 | 原因 |
| --- | --- | --- |
| 第 0、1 天（入库当天与次日） | **不动** | 刚入库，菌包/覆土未稳定，拍了也没有可比性 |
| 第 2 … 25 天 | 巡检 | 生长期，正是要采数据的窗口 |
| 第 26 天及以后 | **不自动巡检** | 超出本轮生长周期（该采收了） |

"整库同一入库时间"——所以准入是**库房级**判断，不是逐框判断。

## 读不到就不动（fail-closed）

**没有入库日期 ⇒ 拒绝巡检**，而不是"没有限制条件就照跑"。这条是刻意的：
这台机器会自己动 4.5 米导轨、拍 60 张图。拿不到准入依据时，"不动"是唯一安全的默认——
误动一次会产出 60 张错误照片并写进测量表，而少拍一轮只是少一轮数据。

同理，日期解析失败、天数越界、配置缺失，一律拒绝并**说明原因**，不静默放行。

## 数据来源

入库信息属于**农场生产系统**（`mushroom_solution` 的「蘑菇台账接口」
`service/monitor/register/ledger/page/list`，见其 `configs/settings.toml`），
不在本仓库。本模块定义的是**契约**：谁去读、怎么读是部署侧的事，读到的结果落进
``room.yaml``（见 `patrol.deploy`/`deploy.m1` 的 `--room`）：
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

import yaml

ROOM_FILE = "room.yaml"

# 准入窗口（在库天数，含端点）。入库当天 = 第 0 天。
MIN_DAY = 2
MAX_DAY = 25


class RoomStateError(RuntimeError):
    """库房状态不可用（文件缺失/字段缺失/日期非法）——调用方必须据此**拒绝移动**。"""


@dataclass(frozen=True)
class RoomState:
    """一个库房的在库状态。``entry_date`` 是整库同一个入库日期。"""

    room_id: str
    entry_date: date
    packages: int | None = None          # 入库包数（生产系统台账里的量，供留档/报表）
    batch_no: str | None = None          # 批次号（台账主键之一，便于与生产系统对账）
    source: str = ""                     # 这条记录是谁/何时写下的（排障时最想知道的事）
    min_day: int = MIN_DAY
    max_day: int = MAX_DAY

    def day_on(self, today: date) -> int:
        """在库天数：**入库当天 = 第 0 天**。

        生产系统的 `mushroom_operating_record.in_day_num` 把入库当天记作 **01**
        （实测：611 库 in_time=2026-03-16，当天记录 in_day_num=01），即"从 1 开始数"。
        我们内部从 0 开始数，`verdict()` 输出里会**同时给出系统口径**，避免现场对不上号。
        """
        return (today - self.entry_date).days

    def verdict(self, today: date) -> tuple[bool, str]:
        """返回 ``(是否可巡检, 人读原因)``。原因文案要能直接贴进日志/告警。

        文案里同时给"第 N 天"与"系统口径第 N+1 天"，因为现场看到的是系统口径。
        """
        day = self.day_on(today)
        sys_day = day + 1                     # 生产系统 in_day_num 的口径
        if day < 0:
            return False, (f"入库日期 {self.entry_date} 在未来（今天 {today}）——"
                           f"数据有误，按不动处理")
        where = f"第 {day} 天（入库当天算第 0 天；生产系统口径第 {sys_day} 天，入库 {self.entry_date}）"
        if day < self.min_day:
            return False, f"{where}：前 {self.min_day} 天不动，等稳定后再拍"
        if day > self.max_day:
            return False, f"{where}：已超过第 {self.max_day} 天，本周期不再自动巡检"
        return True, f"{where}：在准入窗口 {self.min_day}–{self.max_day} 天内"


def parse_room_state(payload: dict) -> RoomState:
    """从已解析的 ``room.yaml`` 内容构造 RoomState；缺关键字段即抛 RoomStateError。"""
    if not isinstance(payload, dict):
        raise RoomStateError(f"room.yaml 顶层必须是映射，实际是 {type(payload).__name__}")

    raw_date = payload.get("entry_date")
    if raw_date in (None, ""):
        raise RoomStateError("room.yaml 缺少 entry_date（整库入库日期）——读不到就不巡检")
    if isinstance(raw_date, datetime):
        entry = raw_date.date()
    elif isinstance(raw_date, date):
        entry = raw_date
    else:
        text = str(raw_date).strip()
        # 只认 `YYYY-MM-DD`：Python 的 fromisoformat 连 `20260913`（basic 格式）也收，
        # 对一份**决定机构动不动**的配置来说太宽松——写错的人不会得到任何提示。
        # 宁可让人看到"格式不对"，也不要默默按一个他没想过的日期算天数。
        if len(text) != 10 or text[4] != "-" or text[7] != "-":
            raise RoomStateError(
                f"entry_date 必须是 YYYY-MM-DD（收到 {raw_date!r}）"
            )
        try:
            entry = date.fromisoformat(text)
        except ValueError as e:
            raise RoomStateError(
                f"entry_date 不是合法日期（YYYY-MM-DD）：{raw_date!r}"
            ) from e

    packages = payload.get("packages")
    if packages is not None:
        try:
            packages = int(packages)
        except (TypeError, ValueError) as e:
            raise RoomStateError(f"packages 不是整数：{packages!r}") from e

    min_day = int(payload.get("min_day", MIN_DAY))
    max_day = int(payload.get("max_day", MAX_DAY))
    if min_day > max_day:
        raise RoomStateError(f"min_day({min_day}) > max_day({max_day})，窗口为空")

    return RoomState(
        room_id=str(payload.get("room_id") or "unset"),
        entry_date=entry,
        packages=packages,
        batch_no=payload.get("batch_no"),
        source=str(payload.get("source") or ""),
        min_day=min_day,
        max_day=max_day,
    )


def load_room_state(path: str | Path) -> RoomState:
    """读取库房状态；任何问题都抛 RoomStateError（调用方据此拒绝移动）。"""
    p = Path(path)
    if not p.exists():
        raise RoomStateError(
            f"库房状态文件不存在：{p}（入库信息未接入 ⇒ 按不动处理，不会巡检）"
        )
    try:
        payload = yaml.safe_load(p.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as e:
        raise RoomStateError(f"读取库房状态失败：{p}（{e}）") from e
    return parse_room_state(payload)


def ensure_room_file(path: str | Path, *, room_id: str = "unset") -> Path:
    """写一份**占位** ``room.yaml``（entry_date 留空）——给现场一个明确的填写位置。

    刻意留空 entry_date 而不是填今天：填空 ⇒ `load_room_state` 抛错 ⇒ 拒绝巡检。
    若填今天，运维会以为"配好了"，而实际语义是"入库当天、不该动"，两者容易混淆。
    """
    p = Path(path)
    if p.exists():
        return p
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(
        "# 库房在库状态——巡检准入门禁。读不到 entry_date ⇒ 拒绝巡检（fail-closed）。\n"
        "#\n"
        "# 入库信息由农场生产系统（蘑菇台账接口）提供，整库同一个入库日期。\n"
        "# 现场/集成脚本把读到的值写到这里，或直接改这一行。\n"
        "#\n"
        "#   entry_date: 整库入库日期 YYYY-MM-DD（**必填**）\n"
        "#   packages:   入库包数（台账量，留档用）\n"
        "#   batch_no:   批次号（与生产系统对账用）\n"
        "#   source:     谁/何时写的（排障时最想知道的事）\n"
        f"#   min_day/max_day: 准入窗口，默认 {MIN_DAY}–{MAX_DAY}（第 0/1 天不动、第 26 天起不自动巡检）\n"
        "\n"
        f"room_id: {room_id}\n"
        'entry_date: ""        # ← 填这里；留空则巡检不会启动\n'
        "packages: null\n"
        "batch_no: null\n"
        'source: ""\n',
        encoding="utf-8",
    )
    return p
