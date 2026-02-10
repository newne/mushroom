"""
分布式训练工具
"""

from __future__ import annotations

import os
from typing import Tuple

import torch
import torch.distributed as dist


def init_distributed(backend: str) -> Tuple[int, int]:
    """初始化分布式训练并返回 rank 与 world_size。"""
    if dist.is_available() and not dist.is_initialized():
        dist.init_process_group(backend=backend)
    rank = dist.get_rank() if dist.is_initialized() else 0
    world_size = dist.get_world_size() if dist.is_initialized() else 1
    return rank, world_size


def is_main_process() -> bool:
    """判断当前进程是否为主进程。"""
    return not dist.is_initialized() or dist.get_rank() == 0


def barrier() -> None:
    """分布式同步屏障。"""
    if dist.is_initialized():
        dist.barrier()


def cleanup() -> None:
    """销毁分布式进程组。"""
    if dist.is_initialized():
        dist.destroy_process_group()


def get_device() -> torch.device:
    """获取当前设备。"""
    if torch.cuda.is_available():
        local_rank = int(os.environ.get("LOCAL_RANK", "0"))
        return torch.device(f"cuda:{local_rank}")
    return torch.device("cpu")
