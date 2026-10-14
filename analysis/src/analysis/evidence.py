"""估算证据（票 20）：`estimated_fields` / `scale_source` / `mm_per_px` / `scale_ref_*`。

## 三件事必须分开（票 18 的两层 + 票 20 的落列）

| 层 | 字段 | 作用 |
| --- | --- | --- |
| 行级 | `quality` 含 `estimate` | fail-safe：这一行里**有**估算值；与 `verified` **正交** |
| 字段级 | `estimated_fields` | **哪些列是估算**（列名清单）——同一行可以"这列实测、那列估的" |
| 标尺证据 | `scale_source` + `mm_per_px` + `scale_ref_mm`/`scale_ref_px` | 这个 mm/px 是**怎么来的**，且能交叉复算 `mm_per_px ≈ scale_ref_mm / scale_ref_px` |

为什么字段级不能省（票 20 §2 的病根）：只用行级 `estimate`，下游只能**整行**取舍 ——
要么整行丢（把同一行里真有的 `n`/`station_id` 一起丢了），要么整行收（把估算的 mm 当实测
喂进区间表）。**两种都是错**。

## 连接符与过滤规则的唯一权威

`estimated_fields` 用 **`+`** 连接（与测量侧 `measure.contract.field_is_usable()` 的 split 一致，
票 11 §3 的同一个连接符）。**过滤规则的实现留在测量侧**，本模块只 re-export ——
两套实现迟早会漂，而"漂了以后谁的规则生效"没人能回答。

## 两个 flag 的语义（Chief 2026-10-14，测量侧已实现，本侧只登记）

- `no_theta`：**有标尺、只缺俯角 θ** ⇒ 竖直量写 NULL、**水平量照出**；
  **不进 `NON_USABLE_FLAGS`**（行仍可用）。它只说明"哪个修正项缺"，
  字段级取舍交给 `estimated_fields` —— 别让行级 flag 承担字段级语义（那是 213/218 帧
  有值数据被整行丢掉的原因）。
- `height_uncorrected`：**未做 cosθ 补偿的竖直投影跨度（mm）**，**不是长度**；
  与 `estimate` 并存，与 `no_theta` 同时出现；**不进 `verified`/区间表/建议层**，
  只能作相对比较（同站位、同档、同 `imaging_profile`）。
  **两个入口的默认值不同，读口径要认准是哪条路**（2026-10-14 核对）：
  `measure.estimate.estimate_cluster` 默认**关**（`allow_uncorrected_height=False`），
  而 **L3 / 生产路径**（`measure.l3.L3Config`、`measure.production.make_measure_fn`）默认**开**。
  ⇒ 生产行在 θ 未注入时会**带值 + 挂本标记**；若某天看到的是 NULL + 仅 `no_theta`，
  说明走的是 estimate 那条默认关的路，而不是"θ 已校"。

## `scale_source` 枚举（本侧定名，测量侧同步）

| 值 | 含义 | 备注 |
| --- | --- | --- |
| `bag_standard` | **菌包标准尺寸常数**（袋长 240 mm / 袋径 140 mm）当帧内标尺 | **当前口径**（用户 2026-10-14） |
| `calibration_target` | 每站位/每排的**现场标定值**（`--mm-per-px`，ADR-0024 的另一路） | 可审计、可复算 |
| `bag_detected` | **只有自动检出可用**（现场常数还没标定） | 仍是估算，**绝不进 `verified`** |
| `none` | **没有标尺** ⇒ mm 列 NULL + `no_scale` | 与"值小/值 0"无关 |

票 18 的第四态**标尺冲突**（`scale_conflict`）不在本表：它是**质量 flag**，
按"两个来源都在、差异超阈值 ⇒ 值 NULL + 标记"处理，等测量侧落 flag 时一起登记。
"""

from __future__ import annotations

#: `estimated_fields` 的连接符（票 11 §3：多标记/清单一律 `+`）。
ESTIMATED_FIELDS_JOINER = "+"

#: 标尺来源枚举（本侧定名；测量侧同步）。
SCALE_SOURCES = ("bag_standard", "calibration_target", "bag_detected", "none")

#: 过渡别名：测量侧旧代码发的是 `bag_diameter`（那会儿还没有"标准尺寸"这个口径）。
#: **不静默改**：命中别名时归一化并**打一行警告** —— 让"还有人发旧值"看得见。
#: 测量侧同步完 `record.py` 之后，这一条可以删。
SCALE_SOURCE_ALIASES: dict[str, str] = {"bag_diameter": "bag_standard"}


def parse_estimated_fields(value) -> list[str]:
    """`"a+b"` / `["a", "b"]` / `None` → 去重保序的列名列表（**唯一入口**）。

    读回是 **list**（票 20 的形状要求）：列名清单如果是个裸字符串，
    下游每处都要自己 split，split 规则一旦有一处不同就是静默错过滤。
    """
    if value is None or value == "":
        return []
    if isinstance(value, str):
        items = value.split(ESTIMATED_FIELDS_JOINER)
    elif isinstance(value, (list, tuple, set)):
        items = [str(v) for v in value]
    else:
        items = [str(value)]
    out: list[str] = []
    for item in items:
        text = item.strip()
        if text and text not in out:
            out.append(text)
    return out


def format_estimated_fields(value) -> str | None:
    """列表/字符串 → 入库用的 `+` 串；空 ⇒ **NULL**（不是空串，更不是 `[]`）。"""
    items = parse_estimated_fields(value)
    return ESTIMATED_FIELDS_JOINER.join(items) if items else None


def normalize_scale_source(value) -> str | None:
    """标尺来源归一化：枚举值原样、过渡别名归一 + 报警、未知值**保留原文 + 报警**。

    未知值**不丢**：丢掉一个来源标记就等于"这个 mm 从哪来的"永久不可查；
    但也**不静默**——每个未知值打一行，页面拿到的仍是原始字符串。
    """
    text = str(value or "").strip()
    if not text:
        return None
    if text in SCALE_SOURCES:
        return text
    alias = SCALE_SOURCE_ALIASES.get(text)
    if alias:
        print(f"[evidence] scale_source {text!r} 是旧值，按 {alias!r} 归一（测量侧待同步）")
        return alias
    print(
        f"[evidence] scale_source {text!r} 不在枚举 {SCALE_SOURCES} 里 —— "
        "保留原文（不丢证据），但请补枚举或改生产者"
    )
    return text


def is_known_scale_source(value) -> bool:
    """归一化之后是否是枚举内的值（页面/报表可据此决定要不要标"未知来源"）。"""
    text = normalize_scale_source(value)
    return bool(text) and text in SCALE_SOURCES


def mm_per_px_from_ref(scale_ref_mm, scale_ref_px) -> float | None:
    """`mm_per_px ≈ scale_ref_mm / scale_ref_px` —— 标尺证据的交叉复算（对不上就别当真）。"""
    try:
        mm, px = float(scale_ref_mm), float(scale_ref_px)
    except (TypeError, ValueError):
        return None
    if px <= 0 or mm <= 0:
        return None
    return mm / px


def field_is_usable(quality: str | None, estimated_fields, field: str) -> bool:
    """票 18 §2 的过滤规则 ⇔ `verified` ∈ `quality` **且** `field ∉ estimated_fields`。

    **实现只有一处**：测量侧 `measure.contract.field_is_usable`（本函数只是适配器，
    负责把 list/str 两种形状统一成那一边要的 `+` 串）。禁止在 analysis 里再写一份。
    """
    from measure.contract import field_is_usable as impl

    return impl(quality, format_estimated_fields(estimated_fields), field)


def is_usable(quality: str | None) -> bool:
    """行级可用性（实现同样只有测量侧一处）：不含 `NON_USABLE_FLAGS` 才算可用。

    `no_theta` / `height_uncorrected` / `estimate` **都不在**那份清单里 ⇒ 它们本身
    不让行变不可用；但它们也**不含 `verified`**，所以进不了区间表与建议层（两层判断各司其职）。
    """
    from measure.contract import is_usable as impl

    return impl(quality)
