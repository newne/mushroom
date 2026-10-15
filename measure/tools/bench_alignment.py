"""L1 对齐的单帧预算基准（spec §6.1：单帧总耗时 ≤3.75 s）。

**只用合成帧**——不读生产帧、不连库、不连网（票 01 的 P0 红线）。合成图是"低频结构
背景 + 掩码外在长的菌簇"，与被测实现无关；位移真值写在 `SHIFTS` 里。

用法（仓库根）：

    uv run --no-project --offline --with numpy --with opencv-python-headless \
        python measure/tools/bench_alignment.py
    python measure/tools/bench_alignment.py --runs 9 --out .scratch/align-bench.json

产物：stdout 表（每次 + 分位数）＋可选 JSON。**超门（中位 >3.75 s）退出码 1**，
好让 CI/验收一眼看见。这里量的是**对齐自身**：下游（L2–L5）还要在同 3.75 s 里分时间，
所以这条是必要不充分条件。
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import numpy as np

_HERE = Path(__file__).resolve()
sys.path.insert(0, str(_HERE.parents[1] / "src"))  # measure/src

from measure.align import ALIGNED, AlignConfig, estimate_translation  # noqa: E402

#: 合成帧的机位平移真值（原图像素；都在 max_shift_px 之内）
SHIFTS: tuple[tuple[int, int], ...] = ((0, 0), (9, 5), (-17, 11), (23, -11), (-29, -19))
FRAME_BUDGET_S = 3.75


def synth_background(h: int, w: int, *, seed: int = 0) -> np.ndarray:
    """货架/管线风格的**低频结构**背景（合成；与 measure/tests 同一性格）。"""
    rng = np.random.default_rng(seed)
    lay = np.random.default_rng(seed + 1)
    beam_y = 0.20 + 0.45 * lay.random(2)
    pipe_x = 0.08 + 0.84 * lay.random(3)
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    img = 0.45 + 0.12 * np.sin(xx / 170.0) + 0.10 * np.cos(yy / 90.0)
    for y0, sigma, amp in zip(beam_y, (14.0, 10.0), (0.20, 0.18)):
        img += amp * np.exp(-((yy - y0 * h) ** 2) / (2.0 * sigma ** 2))
    for x0, sigma, amp in zip(pipe_x, (9.0, 12.0, 7.0), (0.22, 0.20, 0.15)):
        img += amp * np.exp(-((xx - x0 * w) ** 2) / (2.0 * sigma ** 2))
    coarse = rng.normal(0.0, 0.05, (h // 8 + 1, w // 8 + 1)).astype(np.float32)
    img += np.kron(coarse, np.ones((8, 8), np.float32))[:h, :w]
    img += rng.normal(0.0, 0.015, (h, w)).astype(np.float32)
    return np.clip(img, 0.0, 1.0).astype(np.float32)


def synthetic_pair(h: int, w: int, dx: int, dy: int, *, seed: int = 0,
                   pad: int = 16) -> tuple[np.ndarray, np.ndarray]:
    """同一画布取两窗：内容 `(x, y)` 处 → 第二帧 `(x+dx, y+dy)`（真值就是 `(dx, dy)`）。"""
    big = synth_background(h + abs(dy) + pad, w + abs(dx) + pad, seed=seed)
    ky, kx = max(0, dy), max(0, dx)
    return (np.ascontiguousarray(big[ky:ky + h, kx:kx + w]),
            np.ascontiguousarray(big[ky - dy:ky - dy + h, kx - dx:kx - dx + w]))


def background_mask(h: int, w: int, *, frac: float = 0.6) -> np.ndarray:
    """静态背景掩码：上半屏货架/管线；下半屏是"在长的菌簇"，不参与配准。"""
    mask = np.zeros((h, w), bool)
    mask[: int(h * frac), :] = True
    return mask


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="L1 对齐单帧预算基准（合成帧，只读本地）")
    ap.add_argument("--runs", type=int, default=5, help="每个位移跑几次（含预热）")
    ap.add_argument("--width", type=int, default=2880)
    ap.add_argument("--height", type=int, default=1616)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--out", default=None, help="可选：把结果写成 JSON")
    args = ap.parse_args(argv)

    h, w = args.height, args.width
    mask = background_mask(h, w)
    cfg = AlignConfig()
    print(f"帧 {w}×{h}｜估变换长边 ≤{cfg.max_estimate_edge_px}px｜"
          f"位移上限 {cfg.max_shift_px}px｜残差门 score≥{cfg.min_score}｜{args.runs} 次/位移")

    rows: list[dict] = []
    for (dx, dy) in SHIFTS:
        ref, mov = synthetic_pair(h, w, dx, dy, seed=args.seed)
        for k in range(args.runs):
            t0 = time.perf_counter()
            al = estimate_translation(ref, mov, mask, config=cfg)
            wall = time.perf_counter() - t0
            ok_shift = (al.shift_px is not None
                        and abs(al.shift_px[0] - dx) <= 1.0 and abs(al.shift_px[1] - dy) <= 1.0)
            rows.append({"truth_px": [dx, dy], "run": k, "status": al.status,
                         "elapsed_s": round(al.elapsed_s, 4), "wall_s": round(wall, 4),
                         "score": None if al.score is None else round(al.score, 5),
                         "shift_px": None if al.shift_px is None else [round(v, 3) for v in al.shift_px],
                         "estimate_scale": al.estimate_scale, "within_1px": bool(ok_shift)})
        last = rows[-1]
        print(f"  真值 ({dx:+4d},{dy:+4d})｜{last['status']:>22s}｜"
              f"估计 {str(last['shift_px']):>18s}｜score {str(last['score']):>7s}｜"
              f"对齐自身 {last['elapsed_s']:.4f}s｜含合成图墙钟 {last['wall_s']:.4f}s")

    measured = sorted(r["elapsed_s"] for r in rows)
    med = float(np.median(measured))
    p95 = float(np.percentile(measured, 95))
    bad = [r for r in rows if r["status"] != ALIGNED or not r["within_1px"]]
    print(f"\n对齐自身耗时：min {measured[0]:.4f}s｜median {med:.4f}s｜p95 {p95:.4f}s｜"
          f"max {measured[-1]:.4f}s（门 {FRAME_BUDGET_S}s；本值**不含** L2–L5）")
    print(f"定向：{len(rows) - len(bad)}/{len(rows)} 次状态 ok 且位移误差 ≤1 px")

    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(json.dumps(
            {"frame_hw": [h, w], "config": cfg.__dict__, "budget_s": FRAME_BUDGET_S,
             "median_s": med, "p95_s": p95, "rows": rows}, ensure_ascii=False, indent=2),
            encoding="utf-8")
        print(f"JSON → {args.out}")

    if med > FRAME_BUDGET_S or bad:
        print("FAIL：预算或定向没过")
        return 1
    print("PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
