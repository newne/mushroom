"""质量门阈值标定（票 13）：在真实巡检帧样本上重算分布，产出阈值候选与误杀率。

样本由发版官只读抽取（`.scratch/frame-quality-samples/`，230 张线上真机帧），
本脚本**只读**样本目录，不碰生产。用法：

::

    python measure/tools/calibrate_quality_thresholds.py [样本目录]

产出（stdout）：按 label / stratum / 站位分组的 lapvar / p99 / 亮度分位数，
以及在若干候选阈值下的**误杀率**（以 `label_auto=ok` 为正常人群）。

## 刻度约定（重要）

`lapvar` 在 **0–255 灰度**上算（`np.asarray(img).mean(axis=2)`）。在 0–1 上算会小
65025 倍，`DEFAULT_ABS_LAPVAR_FLOOR` 这类绝对阈值就失去意义。`p99_brightness` 内部
归一化到 0–1。
"""

from __future__ import annotations

import csv
import os
import sys

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from measure.quality import (  # noqa: E402
    laplacian_variance,
    overexposed_fraction,
    p99_brightness,
)
from measure.sample_hygiene import is_excluded  # noqa: E402

DEFAULT_DIR = r"D:\code\mushroom\.scratch\frame-quality-samples"


def gray_of(root: str, row: dict) -> np.ndarray:
    from PIL import Image

    path = os.path.join(root, row["file"].replace("/", os.sep))
    img = np.asarray(Image.open(path).convert("RGB")).astype(np.float32)
    return img.mean(axis=2)


def describe(name: str, values: list[float]) -> None:
    if not values:
        print(f"  {name:24s} n=0")
        return
    a = np.asarray(values, dtype=float)
    print("  %-24s n=%3d min=%8.2f p05=%8.2f p10=%8.2f p50=%8.2f p90=%8.2f max=%8.2f"
          % (name, len(a), a.min(), np.percentile(a, 5), np.percentile(a, 10),
             np.percentile(a, 50), np.percentile(a, 90), a.max()))


def main() -> int:
    root = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_DIR
    labels = os.path.join(root, "labels.csv")
    if not os.path.exists(labels):
        print(f"样本表不存在：{labels}")
        return 2
    with open(labels, encoding="utf-8") as fh:
        all_rows = list(csv.DictReader(fh))

    # 样本卫生：跨站重复帧 / 已知坏轮 / 内容校验没查成的轮，一律**不参与拟合**
    # （票 13-round-content-integrity；fail-closed 见 measure.sample_hygiene）
    rows, excluded = [], []
    for row in all_rows:
        why = is_excluded(row)
        (excluded if why else rows).append((row, why) if why else row)
    print(f"样本：{len(all_rows)} 行（{root}）")
    if excluded:
        print(f"按样本卫生排除 {len(excluded)} 行：")
        for row, why in excluded[:10]:
            print(f"  - {row.get('ts')} {row.get('station_id')}: {why}")
    print()

    by_label: dict[str, list[dict]] = {}
    by_stratum: dict[str, list[dict]] = {}
    for row in rows:
        by_label.setdefault(row.get("label_auto") or "?", []).append(row)
        by_stratum.setdefault(row.get("stratum") or "?", []).append(row)

    measured: dict[str, list[float]] = {k: [] for k in ("lapvar", "p99", "luma", "over")}
    for row in rows:
        gray = gray_of(root, row)
        measured["lapvar"].append(laplacian_variance(gray))
        measured["p99"].append(p99_brightness(gray))
        measured["luma"].append(float(gray.mean()) / 255.0)
        measured["over"].append(overexposed_fraction(gray))

    print("=== 全样本（自算，0–255 刻度 lapvar）===")
    for key, vals in measured.items():
        describe(key, vals)

    for label, rs in sorted(by_label.items()):
        print(f"\n=== label_auto = {label}（n={len(rs)}）===")
        idx = [rows.index(r) for r in rs]
        for key in ("lapvar", "p99", "luma", "over"):
            describe(key, [measured[key][i] for i in idx])

    print("\n=== stratum ===")
    for stratum, rs in sorted(by_stratum.items()):
        idx = [rows.index(r) for r in rs]
        describe(stratum, [measured["lapvar"][i] for i in idx])

    ok = [rows.index(r) for r in rows if r["label_auto"] == "ok"]
    if ok:
        laps = np.asarray([measured["lapvar"][i] for i in ok])
        p99s = np.asarray([measured["p99"][i] for i in ok])
        print(f"\n=== 阈值候选（正常人群 = label_auto ok，n={len(ok)}）===")
        for floor in (40, 45, 48, 50, 52.59, 55, 60, 100):
            bad = int((laps < floor).sum())
            print("  清晰度绝对下限 %6.2f ⇒ 误杀 %5.1f%% (%d/%d)"
                  % (floor, 100 * bad / len(laps), bad, len(laps)))
        for th in (0.08, 0.12, 0.157, 0.20, 0.30):
            bad = int((p99s < th).sum())
            print("  近黑 p99 < %.3f       ⇒ 误杀 %5.1f%% (%d/%d)"
                  % (th, 100 * bad / len(laps), bad, len(laps)))

    # 同站位跨轮次稳定性（相对判据的前提）：pair 层同站位两轮比较
    pairs: dict[str, list[float]] = {}
    for i, row in enumerate(rows):
        if row.get("stratum") == "pair":
            pairs.setdefault(row["station_id"], []).append(measured["lapvar"][i])
    if pairs:
        rel = [abs(v[0] - v[1]) / max(v[0], v[1]) for v in pairs.values() if len(v) >= 2]
        if rel:
            print(f"\n=== 同站位跨轮次相对差（n={len(rel)} 站位）===")
            print("  min=%.1f%% p50=%.1f%% max=%.1f%%"
                  % (100 * min(rel), 100 * float(np.median(rel)), 100 * max(rel)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
