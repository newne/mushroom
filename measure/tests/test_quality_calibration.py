"""质量门阈值标定的回归测试（票 13）。

**样本不在仓库里**（`.scratch/frame-quality-samples/`，约 137 MB，发版官只读抽取的
230 张线上真机帧）：样本不存在就 `skip`，CI/别人机器上不会红。

样本在时，这里钉住的是**标定结论本身**：
- 用 `measure.quality` 的自有函数在**原始帧**上重算，必须复现发版官的度量
  （防止"标定用的量"和"线上判据"悄悄分叉）；
- 218 张 `ok` 帧在**定标后的门**下必须全部可用（误杀 0）；
- 2 张真暗帧必须被判 `no_light`（不可用）；
- 刻度约定：`lapvar` 在 0–255 灰度上算，`DEFAULT_ABS_LAPVAR_FLOOR` 才有意义。

样本目录可用环境变量 `FRAME_SAMPLES_DIR` 覆盖。
"""

from __future__ import annotations

import csv
import os

import numpy as np
import pytest
from PIL import Image

from measure.quality import (
    DEFAULT_ABS_LAPVAR_FLOOR,
    is_dark,
    laplacian_variance,
    p99_brightness,
)

SAMPLES = os.environ.get("FRAME_SAMPLES_DIR",
                         r"D:\code\mushroom\.scratch\frame-quality-samples")
LABELS = os.path.join(SAMPLES, "labels.csv")

pytestmark = pytest.mark.skipif(
    not os.path.exists(LABELS), reason="线上帧样本不在本机（.scratch/frame-quality-samples）")


def load_rows() -> list[dict]:
    with open(LABELS, encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def gray_of(row: dict) -> np.ndarray:
    path = os.path.join(SAMPLES, row["file"].replace("/", os.sep))
    img = np.asarray(Image.open(path).convert("RGB")).astype(np.float32)
    return img.mean(axis=2)          # 0–255 刻度（与产者/标定一致）


def test_ok_frames_are_not_killed_by_calibrated_floor():
    """标定结论：218 张 ok 帧的 lapvar 都在绝对下限之上（误杀 0）。"""
    ok = [r for r in load_rows() if r["label_auto"] == "ok"]
    assert len(ok) >= 200, f"样本太少（{len(ok)}），不足以支撑定标"
    laps = np.asarray([laplacian_variance(gray_of(r)) for r in ok])
    assert laps.min() >= DEFAULT_ABS_LAPVAR_FLOOR, (
        f"最暗的一张 ok 帧 lapvar={laps.min():.1f} 跌破了绝对下限 "
        f"{DEFAULT_ABS_LAPVAR_FLOOR}——要么下限太高，要么样本里混进了坏帧")
    # 旧默认 100 会拒掉绝大多数正常帧，这条把它钉成事实
    assert (laps < 100.0).mean() > 0.5, "旧默认门拒帧比例与标定记录不符"


def test_dark_frames_are_rejected_by_p99():
    dark = [r for r in load_rows() if r["label_auto"] == "dark"]
    assert dark, "样本里没有 dark 帧"
    for row in dark:
        gray = gray_of(row)
        assert is_dark(gray), f"{row['file']} 没被判成近黑（p99={p99_brightness(gray):.3f}）"


def test_recomputed_metrics_match_the_sample_table():
    """自有函数必须复现样本表里的度量（同刻度、同定义）——抽 10 帧核对。"""
    rows = [r for r in load_rows() if r["label_auto"] == "ok"][:10]
    for row in rows:
        gray = gray_of(row)
        assert laplacian_variance(gray) == pytest.approx(float(row["lapvar"]), rel=1e-3)
        # p99 在 0–1 归一化口径下与表的 0–255 口径成比例
        assert p99_brightness(gray) * 255.0 == pytest.approx(float(row["p99"]), abs=1.5)
