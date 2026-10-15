"""端到端批量运行（主线，2026-10-14）：现役帧 → 生产路径 → measurements 行。

**走的是生产那条路**：`make_measure_fn()`（默认检出器 = L1–L3）→ 每帧一行，
而不是评估脚本里另拼一套。用途：

1. 证明"接了线"在**整批**帧上成立（20 列、证据列、`height_uncorrected`、NULL 语义）；
2. 给出可入库的行文件（`rows.jsonl`），`--ingest` 时经 analysis 存储层灌进 PG
   验证 `/measurements` 非零行；
3. 记整批耗时与轮增量（性能口径）。

用法（仓库根）：

    python measure/tools/run_measure_batch.py --out .scratch/measure-batch
    python measure/tools/run_measure_batch.py --out .scratch/measure-batch \
        --ingest --ingest-schema analysis_test_local

`--mm-per-px` **默认不传**：一期口径是**菌包标准尺寸**当帧内标尺（`scale_source=bag_standard`）；
给了就是站位标定优先（ADR-0024，`calibration_target`）。
"""

from __future__ import annotations

import argparse
import csv
import json
import statistics as st
import sys
import time
from pathlib import Path

import numpy as np

_HERE = Path(__file__).resolve()
sys.path.insert(0, str(_HERE.parents[1] / "src"))  # measure/src

from measure.production import make_measure_fn  # noqa: E402
from measure.record import RECORD_FIELDS  # noqa: E402

DEFAULT_SAMPLES = Path(r"D:\code\mushroom\.scratch\frame-quality-samples")
EXCLUDED_WINDOWS = (("2026-10-09T18:20:00", "2026-10-09T18:28:00"),)
ROI_FRAC = 0.45


def _excluded(ts: str) -> bool:
    return any(a <= ts <= b for a, b in EXCLUDED_WINDOWS)


def load_entries(samples: Path, limit: int = 0) -> list[dict]:
    with (samples / "labels.csv").open(encoding="utf-8") as fh:
        rows = [r for r in csv.DictReader(fh)
                if r.get("label_auto") == "ok" and not _excluded(r.get("ts", ""))]
    return rows[:limit] if limit else rows


def main() -> int:
    ap = argparse.ArgumentParser(description="端到端批量运行（生产路径，离线）")
    ap.add_argument("--samples", default=str(DEFAULT_SAMPLES))
    ap.add_argument("--out", required=True)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--room", default="611")
    ap.add_argument("--mm-per-px", type=float, default=None)
    ap.add_argument("--emit-empty", action="store_true", default=True)
    ap.add_argument("--ingest", action="store_true",
                    help="把行经 analysis 存储层灌进 PG（用户 2026-10-15 裁定：严禁本地文件库）")
    ap.add_argument("--ingest-dsn", default=None,
                    help="（配 --ingest）显式连接串；缺省走 PGSQL_DSN_FILE / PGSQL_DSN")
    ap.add_argument("--ingest-schema", default=None,
                    help="（配 --ingest）PG schema；缺省 ANALYSIS_PG_SCHEMA，再缺省 analysis")
    args = ap.parse_args()

    samples, out = Path(args.samples), Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    entries = load_entries(samples, args.limit)
    print(f"帧：{len(entries)}（label_auto=ok，已排除 10-09 坏轮窗口）")

    # fetch/decode 打桩：url 即本地路径（与生产同一条 decode 入口，只是取字节换成读盘）
    holder: dict[str, str] = {}

    def fetch(url: str) -> bytes:
        holder["path"] = url
        return b""

    def decode(_data: bytes) -> np.ndarray:
        from PIL import Image

        return np.asarray(Image.open(holder["path"]).convert("RGB")).astype(np.float32).mean(axis=2)

    logs: list[str] = []
    fn = make_measure_fn(room_id=args.room, mm_per_px=args.mm_per_px,
                         fetch=fetch, decode=decode, emit_empty=args.emit_empty,
                         log=logs.append)
    metas = [{"ts": e.get("ts"), "box_id": f"{e.get('station_id', 'S?')}@{e.get('ts')}",
              "station_id": e.get("station_id"), "angle_profile": e.get("angle_profile"),
              "cloud_url": str(samples / e["file"])} for e in entries]

    t0 = time.monotonic()
    rows = fn(type("R", (), {"results": metas})())
    wall = time.monotonic() - t0

    with (out / "rows.jsonl").open("w", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")

    summary = _summarize(rows, len(entries), wall, logs)
    (out / "summary.md").write_text(summary, encoding="utf-8")
    print(summary)
    for line in logs:
        if "标尺散布" in line or "height_uncorrected" in line or "测量：" in line:
            print("LOG:", line)

    if args.ingest:
        _ingest(args.ingest_dsn, args.ingest_schema, rows)
    return 0


def _ingest(dsn: str | None, schema: str | None, rows: list[dict]) -> None:
    """经 analysis 存储层灌进 PG（票 28 contract 后**只有**这条路）：证明 `/measurements` 非零行。

    连接口径与生产一致：`--ingest-dsn` → `PGSQL_DSN_FILE`（文件优先）→ `PGSQL_DSN`；
    schema 走 `--ingest-schema` → `ANALYSIS_PG_SCHEMA` → `analysis`。
    """
    sys.path.insert(0, str(_HERE.parents[2] / "analysis" / "src"))
    from analysis import store
    from analysis.schema import MEASUREMENT_FIELDS

    conn = store.connect(dsn=dsn, schema=schema)
    try:
        store.insert_measurements(conn, rows)
        got = store.fetch_measurements(conn)
        print(f"\n入库：schema `{conn.schema}` ⇒ measurements **{len(got)} 行**"
              f"（列 {len(MEASUREMENT_FIELDS)}：{', '.join(MEASUREMENT_FIELDS)}）")
        for row in got[:2]:
            print("  样例行:", tuple(row.get(f) for f in (
                "ts", "room_id", "box_id", "n", "mean_cluster_w_mm", "mean_cluster_h_mm",
                "quality", "estimated_fields", "scale_source", "mm_per_px")))
    finally:
        conn.close()


def _quant(vals: list[float], q: float) -> float | None:
    if not vals:
        return None
    v = sorted(vals)
    return v[min(len(v) - 1, int(q * len(v)))]


def _summarize(rows: list[dict], frames: int, wall: float, logs: list[str]) -> str:
    def col(name):
        return [float(r[name]) for r in rows if r.get(name) is not None]

    w, h = col("mean_cluster_w_mm"), col("mean_cluster_h_mm")
    n_inst = col("n")
    q = lambda vals, p: "—" if not vals else f"{_quant(vals, p):.1f}"
    med = lambda vals: "—" if not vals else f"{st.median(vals):.1f}"
    flags: dict[str, int] = {}
    for r in rows:
        for f in (r.get("quality") or "").split("+"):
            if f:
                flags[f] = flags.get(f, 0) + 1
    srcs: dict[str, int] = {}
    for r in rows:
        srcs[str(r.get("scale_source"))] = srcs.get(str(r.get("scale_source")), 0) + 1
    timing = next((ln for ln in logs if ln.startswith("测量：") and "帧 共" in ln), "（无耗时摘要）")
    disp = next((ln for ln in logs if "标尺散布" in ln), "（无标尺散布日志）")
    lines = [
        f"# 端到端批量运行（生产路径，{frames} 帧）",
        "",
        f"- **行数**：{len(rows)}（每帧一行，`kind=measurement`；列数 {len(RECORD_FIELDS)}+kind）",
        f"- **菌簇直径 `mean_cluster_w_mm`**：出值 **{len(w)}/{frames}** 帧；"
        f"中位 {med(w)} mm（P10 {q(w, .1)} / P90 {q(w, .9)}）",
        f"- **菌簇高度 `mean_cluster_h_mm`（未补偿）**：出值 **{len(h)}/{frames}** 帧；"
        f"中位 {med(h)} mm（P10 {q(h, .1)} / P90 {q(h, .9)}）",
        f"- **朵数 `n`**：中位 {med(n_inst)}（P10 {q(n_inst, .1)} / P90 {q(n_inst, .9)}）",
        f"- **`scale_source`**：{srcs}",
        f"- **quality 标记计数**：{dict(sorted(flags.items(), key=lambda kv: -kv[1]))}",
        f"- **耗时**：整批 {wall:.1f} s ⇒ 均 {wall / max(frames, 1):.2f} s/帧",
        f"- 轮摘要日志：`{timing}`",
        f"- 标尺散布日志：`{disp}`",
        "",
        "> 精度类指标（P/R、袋肩门假阴/假阳）**需要 20 帧人工复核**（票 15 §13.5 已预登记判据），",
        "> 本批只给「能不能跑通 + 量级 + 口径」。",
    ]
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    raise SystemExit(main())
