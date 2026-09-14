"""加载厂商 libFMC4030_2009_1.so。

默认在工作目录/系统库路径中查找；可用环境变量 FMC4030_LIB_PATH 指定绝对路径。
"""

from __future__ import annotations

import ctypes
import os

DEFAULT_LIB_NAME = "libFMC4030_2009_1.so"


def load_library(path: str | None = None):
    """加载并返回 ctypes 库对象；测试可注入假库，不走本函数。

    加载后立刻用 :func:`patrol.fmc.sdk.bind_lib` 声明全部函数的 ``argtypes`` /
    ``restype``——**不声明会出事**：C 侧运动参数是 ``float``（4 字节），而 ctypes
    未声明时把 Python ``float`` 当 ``double``（8 字节）传，参数整体错位。
    """
    lib_path = path or os.environ.get("FMC4030_LIB_PATH") or DEFAULT_LIB_NAME
    try:
        lib = ctypes.CDLL(lib_path)
    except OSError as e:  # pragma: no cover - 环境相关
        raise RuntimeError(
            f"无法加载 FMC4030 动态库: {lib_path}（可用环境变量 FMC4030_LIB_PATH 指定），原因: {e}"
        ) from e
    from patrol.fmc.sdk import bind_lib

    bind_lib(lib)
    return lib
