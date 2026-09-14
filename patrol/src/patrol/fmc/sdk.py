"""FMC4030 SDK 的 C 函数签名表（逐条抄自厂商 linux 版 ``FMC4030.h``）。

**为什么必须有这张表**：ctypes 在未声明 ``argtypes`` 时不会报错，而是按默认规则
"猜"每个实参的 C 类型——Python ``float`` 被当作 C ``double``（8 字节），而头文件里
是 ``float``（4 字节）；``int`` 一律当 ``c_int``。参数一旦错位，轻则抛
``ArgumentError``，重则**把另一个数静静发下去**（例如速度被读成 0 或极大值），
而函数返回值依然是"成功"——对运动控制来说这是最坏的一类故障。

本机第一次让轴真动就撞上了它：``FMC4030_Jog_Single_Axis`` 抛
``ArgumentError: argument 3``。此前所有成功调用（``Get_Machine_Status`` /
``Get_Device_Para``）只用到 ``int`` 与指针，恰好绕过了这条路径，所以长期没暴露。

``load_library()`` 在 ``CDLL`` 之后调用 :func:`bind_lib`；测试注入的假库不走这里，
仍是普通 Python 对象，不受影响。
"""

from __future__ import annotations

import ctypes as _c

# 函数名 -> (argtypes, restype)。指针参数一律用 c_void_p：它既能接受
# (c_ubyte*N)() 这类数组，也能接受 byref(struct) / 结构体实例，
# 比 POINTER(c_ubyte) 宽容（后者接不了 byref 的另一种结构体）。
SIGNATURES: dict[str, tuple[list, object]] = {
    # 设备打开与关闭
    "FMC4030_Open_Device": ([_c.c_int, _c.c_char_p, _c.c_int], _c.c_int),
    "FMC4030_Close_Device": ([_c.c_int], _c.c_int),
    # 单轴运动
    "FMC4030_Jog_Single_Axis": (
        [_c.c_int, _c.c_int, _c.c_float, _c.c_float, _c.c_float, _c.c_float, _c.c_int],
        _c.c_int,
    ),
    "FMC4030_Home_Single_Axis": (
        [_c.c_int, _c.c_int, _c.c_float, _c.c_float, _c.c_float, _c.c_int],
        _c.c_int,
    ),
    "FMC4030_Stop_Single_Axis": ([_c.c_int, _c.c_int, _c.c_int], _c.c_int),
    "FMC4030_Check_Axis_Is_Stop": ([_c.c_int, _c.c_int], _c.c_int),
    "FMC4030_Get_Axis_Current_Pos": ([_c.c_int, _c.c_int, _c.c_void_p], _c.c_int),
    "FMC4030_Get_Axis_Current_Speed": ([_c.c_int, _c.c_int, _c.c_void_p], _c.c_int),
    # IO
    "FMC4030_Set_Output": ([_c.c_int, _c.c_int, _c.c_int], _c.c_int),
    "FMC4030_Get_Input": ([_c.c_int, _c.c_int, _c.c_void_p], _c.c_int),
    # 插补
    "FMC4030_Line_2Axis": (
        [_c.c_int, _c.c_uint, _c.c_float, _c.c_float, _c.c_float, _c.c_float, _c.c_float],
        _c.c_int,
    ),
    "FMC4030_Line_3Axis": (
        [
            _c.c_int, _c.c_uint, _c.c_float, _c.c_float,
            _c.c_float, _c.c_float, _c.c_float, _c.c_float,
        ],
        _c.c_int,
    ),
    "FMC4030_Arc_2Axis": (
        [
            _c.c_int, _c.c_uint, _c.c_float, _c.c_float, _c.c_float,
            _c.c_float, _c.c_float, _c.c_float, _c.c_float, _c.c_int,
        ],
        _c.c_int,
    ),
    "FMC4030_Pause_Run": ([_c.c_int, _c.c_uint], _c.c_int),
    "FMC4030_Resume_Run": ([_c.c_int, _c.c_uint], _c.c_int),
    "FMC4030_Stop_Run": ([_c.c_int], _c.c_int),
    # 状态
    "FMC4030_Get_Machine_Status": ([_c.c_int, _c.c_void_p], _c.c_int),
    "FMC4030_Get_Device_Para": ([_c.c_int, _c.c_void_p], _c.c_int),
    "FMC4030_Set_Device_Para": ([_c.c_int, _c.c_void_p], _c.c_int),
    "FMC4030_Get_Version_Info": ([_c.c_int, _c.c_void_p], _c.c_int),
    # 文件与自动运行
    "FMC4030_Download_File": ([_c.c_int, _c.c_char_p, _c.c_int], _c.c_int),
    "FMC4030_Start_Auto_Run": ([_c.c_int, _c.c_char_p], _c.c_int),
    "FMC4030_Stop_Auto_Run": ([_c.c_int], _c.c_int),
    "FMC4030_Delete_Script_File": ([_c.c_int, _c.c_char_p], _c.c_int),
}


def bind_lib(lib) -> list[str]:
    """给已加载的 CDLL 声明 argtypes/restype，返回**实际绑定成功**的函数名。

    动态库里没有的符号跳过（本机 ``libFMC4030_2009_1.so`` 与头文件版本一致，
    但留出容错，便于换库时不整体炸掉）。返回的名单供测试核对覆盖率。
    """
    bound: list[str] = []
    for name, (argtypes, restype) in SIGNATURES.items():
        fn = getattr(lib, name, None)
        if fn is None:
            continue
        fn.argtypes = argtypes
        fn.restype = restype
        bound.append(name)
    return bound
