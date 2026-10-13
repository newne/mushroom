"""**样本卫生**（票 13-round-content-integrity，2026-10-13）。

阈值标定/评估用的样本必须是"可信的它自己那一份"。两类帧要排除：

1. **带内容完整性标记的帧**：`frame_dup`（跨站重复指纹）或 `suspect_duplicate`（该轮有
   跨站重复指纹）。所以两个函数都读，缺一不可——批处理只写轮级标记时，帧级过滤会落空。
2. **已知坏轮的兜底名单**：字段还没透下来之前的**显式排除表**（见下）。

`content_check_error`（该轮**没查成**）按 **fail-closed** 处理：**不当作"已确认无重复"**。
本模块的 `is_excluded()` 因此会在"没查成"时返回 True —— 宁可少用一帧，也不能把可疑帧
当成正常样本喂进阈值拟合。
"""

from __future__ import annotations

#: 已知坏轮（字段还没落盘前的兜底）。每项：``(起, 止, 依据)``。
#: 2026-10-09 18:22:05–18:27:16：35 帧字节完全相同（sha256 `41685c09…`、92364 B、
#: `p99`=2），同一张陈旧近黑帧写给了 ≥20 个站位而轮行仍写 `ok=1`。
#: 由巡检手票 `fmc4030-camera-scan/issues/13-round-content-integrity.md` 定案。
#: **当 `frame_dup`/`suspect_duplicate` 正常透下来后，本表可删**（保留只作双保险）。
KNOWN_BAD_ROUNDS: tuple[tuple[str, str, str], ...] = (
    ("2026-10-09T18:22:00", "2026-10-09T18:28:00",
     "票 13-round-content-integrity：35 帧同 sha256 41685c09…，轮行却 ok=1"),
)


def is_known_bad_round(ts: str) -> str | None:
    """时间戳落在已知坏轮里 ⇒ 返回依据串，否则 None。"""
    for start, end, why in KNOWN_BAD_ROUNDS:
        if start <= ts <= end:
            return why
    return None


def is_excluded(row: dict) -> str | None:
    """样本行是否要排除；返回原因（None = 可用）。

    传入的可以是采集行（含 `frame_dup`/`suspect_duplicate`/`content_check_error`）
    或纯度量行（只有 `ts`，此时靠 `KNOWN_BAD_ROUNDS` 兜底）。
    """
    ts = str(row.get("ts") or "")
    if row.get("frame_dup"):
        return f"frame_dup({row.get('dup_group') or '?'})"
    if row.get("suspect_duplicate"):
        return "suspect_duplicate_round"
    if row.get("content_check_error"):
        return "content_check_error（没查成 ⇒ 按未确认排除，fail-closed）"
    known = is_known_bad_round(ts)
    if known:
        return known
    return None
