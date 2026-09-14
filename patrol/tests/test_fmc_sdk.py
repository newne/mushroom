"""SDK 签名表的覆盖率与类型守护。

起因（2026-09-12 现场）：第一次让轴真动时 ``FMC4030_Jog_Single_Axis`` 抛
``ArgumentError: argument 3``——``load_library`` 只是裸 ``CDLL()``，没声明
``argtypes``，ctypes 便把 Python ``float`` 当 C ``double`` 传，而头文件里是
``float``。此前成功的调用只用到 ``int`` 与指针，恰好绕过了这条路径。

所以这里守两件事：
1. **覆盖率**：代码里调用的每个 ``FMC4030_*`` 都必须在 ``SIGNATURES`` 里；
2. **类型**：带 ``float`` 形参的函数必须声明为 ``c_float``（不是 ``c_double``）。
"""

from __future__ import annotations

import ctypes
import re
from pathlib import Path

from patrol.fmc.sdk import SIGNATURES, bind_lib

_SRC = Path(__file__).resolve().parents[1] / "src" / "patrol"
# 只抓真正的 SDK 调用点：self._lib.FMC4030_xxx
_CALL = re.compile(r"self\._lib\.(FMC4030_[A-Za-z0-9_]+)")


def _called_functions() -> set[str]:
    names: set[str] = set()
    for path in _SRC.rglob("*.py"):
        names.update(_CALL.findall(path.read_text(encoding="utf-8")))
    return names


def test_every_called_sdk_function_is_declared():
    called = _called_functions()
    assert called, "没扫到任何 SDK 调用点，正则或目录结构变了"
    missing = sorted(called - set(SIGNATURES))
    assert not missing, f"这些函数被调用但没有签名声明，ctypes 会按默认规则猜错类型：{missing}"


def test_signature_table_uses_c_float_not_c_double():
    """C 头文件里运动参数是 float；声明成 c_double 会让参数整体错位。"""
    for name, (argtypes, _restype) in SIGNATURES.items():
        assert ctypes.c_double not in argtypes, f"{name} 把 float 形参声明成了 c_double"
    assert SIGNATURES["FMC4030_Jog_Single_Axis"][0] == [
        ctypes.c_int, ctypes.c_int, ctypes.c_float, ctypes.c_float,
        ctypes.c_float, ctypes.c_float, ctypes.c_int,
    ]
    assert SIGNATURES["FMC4030_Line_2Axis"][0][:2] == [ctypes.c_int, ctypes.c_uint]


def test_bind_lib_declares_argtypes_and_tolerates_missing_symbols():
    class Fake:
        def __init__(self) -> None:
            self.FMC4030_Stop_Run = lambda *a: 0  # 只有这一个符号

    fake = Fake()
    bound = bind_lib(fake)
    assert bound == ["FMC4030_Stop_Run"]
    assert fake.FMC4030_Stop_Run.argtypes == [ctypes.c_int]
    assert fake.FMC4030_Stop_Run.restype is ctypes.c_int
