"""machine_device_para 结构体：布局、往返与软限位语义。

布局必须与厂商 linux 版 ``FMC4030.h`` 的 struct 逐字节一致，否则读回来的软限位是
错位的整数——这类错误不会报错，只会让整定判断失效，所以用显式 offset 断言钉住。
"""

from __future__ import annotations

import ctypes

import pytest
from patrol.fmc.device_para import (
    MAX_AXIS,
    DevicePara,
    DeviceParaStruct,
    build_device_para,
    parse_device_para,
)


def sample(**overrides) -> DevicePara:
    """本机整定后的样例：Y(轴1) 有效 0…4600、Z(轴2) 有效 -212…0。"""
    base = {
        "ip": "192.168.1.239",
        "port": 8088,
        "div": (1600, 1600, 1600),
        "lead": (40, 40, 20),
        "soft_limit_max": (200, 4600, 0),
        "soft_limit_min": (200, 0, 212),
        "home_time": (30000, 30000, 30000),
    }
    base.update(overrides)
    return DevicePara(**base)


def test_struct_offsets_match_the_vendor_header():
    """C 结构体：3×uint → char[15] → int(pad 1) → 5×int[3]。"""
    off = {name: getattr(DeviceParaStruct, name).offset for name, _ in DeviceParaStruct._fields_}
    assert off == {
        "id": 0, "bound232": 4, "bound485": 8,
        "ip": 12, "port": 28,
        "div": 32, "lead": 44, "softLimitMax": 56, "softLimitMin": 68, "homeTime": 80,
    }
    assert ctypes.sizeof(DeviceParaStruct) == 92


def test_round_trip_through_the_raw_buffer():
    raw = bytes(memoryview(build_device_para(sample())).cast("B"))
    assert parse_device_para(raw) == sample()


def test_ip_is_truncated_to_the_fixed_field():
    """char ip[15] 只有 15 字节，超长 IP 必须截断而不是越界写。"""
    para = sample(ip="255.255.255.255.example")
    assert parse_device_para(bytes(memoryview(build_device_para(para)).cast("B"))).ip \
        == "255.255.255.25"


def test_effective_limits_interpret_the_negative_field_as_a_magnitude():
    """「软限位负极限」按幅值存：原值 212 → 实际下限 -212。"""
    para = sample()
    assert para.raw_limits(2) == (212, 0)
    assert para.effective_limits(2) == (-212, 0)
    assert para.soft_limit_enabled(2) is True
    assert para.effective_limits(1) == (0, 4600)


def test_negative_raw_value_means_disabled():
    """说明书 §三.4：把任一软限位设成负数即取消软限位。"""
    para = sample(soft_limit_min=(200, -1, 212))
    assert para.effective_limits(1) is None
    assert para.soft_limit_enabled(1) is False
    assert para.effective_limits(2) == (-212, 0)  # 只取消出问题的那一根轴


def test_describe_marks_disabled_axes():
    text = sample(soft_limit_min=(200, -1, 212)).describe()
    assert "192.168.1.239:8088" in text
    assert "已取消" in text
    assert "轴2 细分 1600 导程 20 软限位 -212…0" in text


def test_axis_tuples_are_fixed_length():
    para = sample()
    for field in (para.div, para.lead, para.soft_limit_max, para.soft_limit_min, para.home_time):
        assert len(field) == MAX_AXIS == 3


@pytest.mark.parametrize("field", ["div", "lead", "soft_limit_max", "soft_limit_min", "home_time"])
def test_short_axis_tuple_is_rejected(field):
    with pytest.raises((TypeError, IndexError, ValueError)):
        build_device_para(sample(**{field: (1, 2)}))
