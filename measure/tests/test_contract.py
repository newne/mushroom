"""`measure.contract` 的单元测试：quality 枚举是票 11 §3 的钉子，钉错下游就静默算错。"""

from measure import contract


def test_format_flags_uses_plus_joiner_in_enum_order():
    assert contract.format_flags({"low_confidence", "baseline"}) == "baseline+low_confidence"
    assert contract.format_flags(set()) == ""
    assert contract.format_flags({"nonsense"}) == "nonsense"


def test_format_flags_dedupes_and_keeps_unknown_last():
    got = contract.format_flags({"baseline", "baseline", "zzz", "low_confidence"})
    assert got == "baseline+low_confidence+zzz"


def test_parse_flags_roundtrip():
    s = contract.format_flags({"baseline", "no_angle_profile"})
    assert contract.parse_flags(s) == {"baseline", "no_angle_profile"}
    assert contract.parse_flags(None) == set()
    assert contract.parse_flags("") == set()


def test_is_usable_gates_on_no_detection():
    assert not contract.is_usable("no_detection")
    assert not contract.is_usable("baseline+no_detection")
    assert not contract.is_usable("low_confidence")
    assert contract.is_usable("baseline+no_angle_profile")
    assert contract.is_usable("verified")


def test_verified_and_level_view_are_registered():
    assert "verified" in contract.QUALITY_FLAGS
    # 平视口径必须是一个显式标记（Chief 2026-10-13：平视能算的量也要标清口径）
    assert "level_view" in contract.QUALITY_FLAGS


def test_level_view_does_not_block_usability():
    """`level_view` 是口径标记，不是"不可用"标记。"""
    assert contract.is_usable("baseline+level_view+no_angle_profile")
    assert not contract.is_usable("baseline+level_view+low_confidence")


def test_no_angle_profile_is_orthogonal_to_verified():
    """环境官 2026-10-13 硬规则：`no_angle_profile` 只标记盖径/杆长两列不可用，
    **不否定整行** —— `verified+no_angle_profile` 合法且照常进统计。

    若把它当"整行不可用"，平视单档下每行都带它 ⇒ `verified` 一行不剩，
    等于从"标注缺测"变成"删数据"。
    """
    assert "no_angle_profile" not in contract.NON_USABLE_FLAGS
    assert contract.is_usable("verified+no_angle_profile")
    assert contract.is_usable("verified+no_angle_profile+level_view")


def test_no_light_is_unusable_not_downgraded():
    """近黑帧没有信息量：处置是"不可用"，不是降权（Chief 2026-10-13）。"""
    assert "no_light" in contract.QUALITY_FLAGS
    assert not contract.is_usable("no_light")
    assert not contract.is_usable("baseline+no_light+no_angle_profile")
