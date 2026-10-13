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


def test_verified_is_registered():
    assert "verified" in contract.QUALITY_FLAGS
