"""库房号身份：跨域 join 的**唯一**过渡口径（Chief 2026-10-14 裁定）。

## 事实（发版官 2026-10-14 只读盘点）

`analysis/data/batch_no_map.json` 里 607/608 两间的批次键是 **`7|…` / `8|…`**，
而 `environment.zone`、`measurements.room_id`、`audit.ROOM_BY_DEVICE_PREFIX`、
`env.tdengine.ROOM_DEVICE_CODES` 用的是 **`607` / `608`**。
⇒ 「库房号 + 入库日期」这个批次身份**跨域 join 时会静默对不上**：
`/environment/batches?zone=607` 会说"还没有批次台账"，而台账里其实有 —— 只是挂着 `7`。
（试点 611/612 两侧编码一致，所以这个坑现在还没炸。）

## 四条口径（照 Chief 的裁定，逐条对应）

1. **别名表落仓库**：`analysis/data/room_alias.json`，带 `source` + `generated_at`，
   **只写确证过的对应**；未确证的**不许猜** ⇒ 代码只读 `map`，
   每条必须带 `evidence`，**没有 evidence 的条目一律跳过并报警**（"不许猜"由结构保证）。
2. **join 不上就 NULL + 计数 + 打印样例**（哪几条、原始值是什么）——
   不用 `0`、不用默认值糊过去。`resolve_rooms()` 是这条的实现。
3. **显示层不许洗白**：`display_room()` 给的是「`607`（原始 `7`）」或
   「`7`（原始编号，未登记别名）」——**不许只给一个数字让人以为是同一编码**。
4. **长期解 = 生产侧统一编码**（票 13 §3.3，待协调）。在那之前别名表是唯一过渡口径，
   **任何人不得在代码里再写一份映射** —— 所以本模块只有"读文件 + 比对"，没有内联表。

## 为什么 Canonical 是 `607`/`608` 而不是 `7`/`8`

测量、环境、操作日志、TDengine 表前缀四处都用 `607`/`608`（四个库房就这么定死了），
只有台账那一路留下旧编号；所以要改的是**一条**，不是**四条**。
规范集合直接取自 `env.tdengine.ROOM_DEVICE_CODES` 的值——不再抄第五份库房清单。
"""

from __future__ import annotations

import json
import os
from pathlib import Path

#: 仓库里的别名表（数据文件）。环境变量只给测试/临时覆盖用。
#: **路径按包位置解析，不按 cwd** —— 相对路径在别的 cwd 下会"文件不存在 ⇒ 一律解不出"，
#: 而那个失败长得跟"还没确证"一模一样（实测踩过：测试/CLI 换个工作目录就全解不出）。
DEFAULT_ALIAS_PATH = str(Path(__file__).resolve().parents[2] / "data" / "room_alias.json")
ALIAS_PATH_ENV = "ROOM_ALIAS_FILE"


def canonical_rooms() -> tuple[str, ...]:
    """规范库房号集合（`env.tdengine.ROOM_DEVICE_CODES` 的**键**就是库房号，值是设备前缀）。

    **不另抄一份库房清单** —— 抄第五份就是下一个"静默对不上"。
    """
    from analysis.env.tdengine import ROOM_DEVICE_CODES

    return tuple(sorted(ROOM_DEVICE_CODES))


def load_room_alias(path: str | None = None) -> dict[str, str]:
    """读 `原始编号 → 规范库房号` 别名表。

    - 文件缺失/读坏 ⇒ 返回 `{}` 并**打一行警告**：宁可一律解不出（NULL + 计数可见），
      也不许因为一个坏文件把某个旧编号"猜"成某个库房。
    - **每条必须有 `evidence`**；缺 evidence 的条目跳过并报警 —— 未确证的对应不许生效。
    - 映射目标必须是规范库房号，否则跳过并报警（挡住"再引入第三种编码"）。
    """
    target = Path(path or os.environ.get(ALIAS_PATH_ENV) or DEFAULT_ALIAS_PATH)
    if not target.exists():
        print(f"[rooms] 没有别名表 {target}：未登记的旧编号一律解不出（写 NULL，不猜）")
        return {}
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        print(f"[rooms] 别名表读不了（{exc}）：未登记的旧编号一律解不出（写 NULL，不猜）")
        return {}

    raw = data.get("map", {}) if isinstance(data, dict) else {}
    if not isinstance(raw, dict):
        print(f"[rooms] 别名表的 map 不是对象（{type(raw).__name__}）：整表作废")
        return {}

    known = set(canonical_rooms())
    out: dict[str, str] = {}
    for src, value in raw.items():
        to = value.get("to") if isinstance(value, dict) else None
        evidence = value.get("evidence") if isinstance(value, dict) else None
        if not to:
            print(f"[rooms] 别名条目 {src!r} 没有 to，跳过")
            continue
        if not evidence:
            print(f"[rooms] 别名条目 {src!r} 没有 evidence，跳过（只写确证过的对应）")
            continue
        if str(to) not in known:
            print(
                f"[rooms] 别名条目 {src!r} → {to!r} 不是规范库房号"
                f"（{'/'.join(sorted(known))}），跳过：不许再引入第三种编码"
            )
            continue
        out[str(src)] = str(to)
    return out


def canonical_room(raw, *, alias: dict[str, str] | None = None) -> str | None:
    """原始编号 → 规范库房号；**解不出就是 `None`**（不是原文、更不是默认值）。

    三种命中：别名表里有 ⇒ 用别名；本来就是规范库房号 ⇒ 原样；其余 ⇒ `None`。
    返回 `None` 的调用方必须**写 NULL + 计数 + 打样例**（口径 2），不许糊。
    """
    text = str(raw or "").strip()
    if not text:
        return None
    table = load_room_alias() if alias is None else alias
    mapped = table.get(text)
    if mapped in set(canonical_rooms()):
        return mapped
    return text if text in set(canonical_rooms()) else None


def alias_codes_for(canonical, *, alias: dict[str, str] | None = None) -> tuple[str, ...]:
    """**逆向**：哪些原始编号会被归一到这个规范库房号（升序）。

    为什么需要逆向：映射/台账里的键可能是**任一**编码。只知道"7 → 607"不够 ——
    拿到 `607` 时还要能想起"它的旧编号是 `7`"，否则按旧编码写的映射表永远查不中，
    `batch_no` 会静默全空（2026-10-14 实测踩到）。
    """
    text = str(canonical or "").strip()
    if not text:
        return ()
    table = load_room_alias() if alias is None else alias
    return tuple(sorted(src for src, to in table.items() if to == text))


def display_room(raw, *, alias: dict[str, str] | None = None) -> str:
    """给页面/报表用的**不洗白**标签（口径 3）。

    - 原始值已是规范号：`611`
    - 别名命中且与原始值不同：`607（原始 7）`
    - 解不出：`7（原始编号，未登记别名）`
    """
    text = str(raw or "").strip()
    if not text:
        return ""
    resolved = canonical_room(text, alias=alias)
    if resolved is None:
        return f"{text}（原始编号，未登记别名）"
    if resolved == text:
        return text
    return f"{resolved}（原始 {text}）"


def resolve_rooms(
    rows: list[dict],
    *,
    key: str = "room_id",
    alias: dict[str, str] | None = None,
    label: str = "",
    sample_limit: int = 5,
) -> tuple[list[dict], dict]:
    """给每行加 `room_id_canonical`（解不出即 **None**），并返回可见报告。

    **绝不改 `key` 那一列**：原始编号要留着（口径 3，显示层要能标"原始 7/8"）。
    报告字段：`n_total` / `n_resolved` / `n_unresolved` / `unresolved_values`（原始值→条数）
    / `unresolved_samples`（前 `sample_limit` 条：行号 + 原始值）。
    解不出时**打印一行**——"静默 NULL"和"静默默认值"一样坏。
    """
    table = load_room_alias() if alias is None else alias
    out: list[dict] = []
    unresolved_values: dict[str, int] = {}
    samples: list[dict] = []
    resolved_count = 0
    for index, row in enumerate(rows):
        raw = row.get(key)
        resolved = canonical_room(raw, alias=table)
        if resolved is None:
            text = str(raw or "")
            unresolved_values[text] = unresolved_values.get(text, 0) + 1
            if len(samples) < sample_limit:
                samples.append({"index": index, "raw": raw})
        else:
            resolved_count += 1
        out.append({**row, "room_id_canonical": resolved})

    report = {
        "n_total": len(rows),
        "n_resolved": resolved_count,
        "n_unresolved": len(rows) - resolved_count,
        "unresolved_values": unresolved_values,
        "unresolved_samples": samples,
    }
    if report["n_unresolved"]:
        shown = ", ".join(f"{k!r}×{v}" for k, v in sorted(unresolved_values.items()))
        where = f"[{label}] " if label else ""
        print(
            f"[rooms] {where}库房号解不出 {report['n_unresolved']}/{report['n_total']} 条"
            f"（原始值 {shown}）；样例 "
            + ", ".join(
                f"第{s['index']}行={s['raw']!r}" for s in samples
            )
            + " —— 写 NULL + 计数，不猜别名"
        )
    return out, report
