"""测量质量标记词汇表（票 11 §3 的代码侧钉子）。

`measurements.quality` 是**枚举**，不是自由文本。多标记用 `+` 连接，例如
`cap_level_approx+low_confidence`；**只要不含 `verified` 就不进区间表与决策统计**
（行照旧入库，缺失要可审计）。

| 值 | 含义 | 进区间表？ |
| --- | --- | --- |
| `verified` | 检出可信，数值可用于分析 | 是 |
| `no_detection` | 本行无有效检出，长度列全 NULL | 否 |
| `baseline` | 基线/定标帧，不是生长测量 | 否 |
| `cap_level_approx` | 菌盖为平视近似值 | 否 |
| `stem_absent(level0)` | 菌柄缺失（level 0），长度无意义 | 否 |
| `low_confidence` | 置信度不足 | 否 |

本模块只多登记一个值：`no_angle_profile`——票 11 §9 要求"缺测原因登记为
无对应角度档"，而这个原因在单档平视下**每行都有**，落在 `quality` 里最不容易被
下游忽略（比埋在 `notes` 里强）。已在票 11 Comments 里登记待环境官确认。

铁律：无值一律 **NULL**（`n` 可为 0），**绝不写 0 冒充测量**——
`align.hourly_rates` 会在 0 与真值之间差分出一个陡降的假速率。
"""

from __future__ import annotations

#: 票 11 §3 的枚举 + `no_angle_profile` + `level_view`。
QUALITY_FLAGS = (
    "verified",              # 检出可信，可用于分析（当前无一行是它）
    "no_detection",          # 无有效检出，长度列全 NULL
    "baseline",              # 基线/定标帧，不是生长测量
    "cap_level_approx",      # 菌盖数值为平视近似
    "stem_absent(level0)",   # 菌柄缺失，长度无意义
    "low_confidence",        # 置信度不足（实例拆分未生效 / 无种子 / 前景无法分离）
    "no_angle_profile",      # 该量在当前角度档下不可测（原因：无对应角度档）
    "level_view",            # **平视口径**：该值是俯视投影量，不是真实几何尺寸
    "no_light",              # 整帧近黑（灯未亮/无信息量）⇒ 该帧不可用，不是降权
)

#: 出现其中任意一个 ⇒ 这一行没有可用测量。
#: `no_light` 同属"不可用"（Chief 2026-10-13：近黑帧没有信息量，降权会污染统计）。
NON_USABLE_FLAGS = ("no_detection", "low_confidence", "no_light", "blurry", "overexposed")

#: 多标记连接符（票 11 §3）。
JOINER = "+"


def parse_flags(quality: str | None) -> set[str]:
    if not quality:
        return set()
    return {f for f in quality.split(JOINER) if f}


def format_flags(flags) -> str:
    """标记集合 → 规范串（去重、按枚举顺序、未知标记排末尾）。"""
    known = {f: i for i, f in enumerate(QUALITY_FLAGS)}
    items = {f for f in flags if f}
    return JOINER.join(sorted(items, key=lambda f: (known.get(f, len(QUALITY_FLAGS)), f)))


def is_usable(quality: str | None) -> bool:
    """这一行有没有可用测量（下游门控的便捷函数）。"""
    return not (parse_flags(quality) & set(NON_USABLE_FLAGS))
