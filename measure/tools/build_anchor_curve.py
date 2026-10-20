"""证据行 → 逐锚点曲线 CSV（票 20）。

输入是票 15 的 `anchor_results.jsonl`（只读），输出每个锚点一份 CSV：
`ts, value, n_bags_valid, n_bags_total, panel_coverage, segment_id, status, usable`。

**不插值、不补 0、不跨段**：缺测行照原样保留 `value` 为空，下游要画断点就画断点。

用法（仓库根）：

    uv run --no-project --offline --with numpy \
        python measure/tools/build_anchor_curve.py \
        --rows .scratch/profile-v6/anchor_results.jsonl --out .scratch/profile-v6/curves
"""

from __future__ import annotations

import argparse
import csv
import re
import sys
from collections import defaultdict
from pathlib import Path

_HERE = Path(__file__).resolve()
sys.path.insert(0, str(_HERE.parents[1] / "src"))  # measure/src

from measure.curve import anchor_series  # noqa: E402
from measure.profile_result import read_jsonl  # noqa: E402

COLUMNS = ("ts", "value", "n_bags_valid", "n_bags_total", "panel_coverage",
           "segment_id", "segmentation_status", "status", "usable", "reasons")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="锚点证据行 → 曲线 CSV")
    ap.add_argument("--rows", required=True, help="anchor_results.jsonl")
    ap.add_argument("--out", required=True, help="输出目录")
    ap.add_argument("--metric", default="h_anchor")
    args = ap.parse_args(argv)

    rows = read_jsonl(args.rows)
    grouped: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        grouped[str(row.get("anchor_id", ""))].append(row)

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    for anchor_id, items in sorted(grouped.items()):
        series = anchor_series(items, metric=args.metric)
        safe = re.sub(r"[^A-Za-z0-9_.-]", "_", anchor_id) or "unknown"
        path = out / f"{safe}.csv"
        with path.open("w", encoding="utf-8", newline="") as fh:
            writer = csv.DictWriter(fh, fieldnames=COLUMNS)
            writer.writeheader()
            for p in series:
                writer.writerow({
                    "ts": p.ts, "value": "" if p.value is None else f"{p.value:.6g}",
                    "n_bags_valid": p.n_bags_valid, "n_bags_total": p.n_bags_total,
                    "panel_coverage": f"{p.panel_coverage:.3f}",
                    "segment_id": p.segment_id,
                    "segmentation_status": p.segmentation_status,
                    "status": p.status, "usable": "true" if p.usable else "false",
                    "reasons": "|".join(p.reasons),
                })
        print(f"{anchor_id}: {len(series)} points → {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
