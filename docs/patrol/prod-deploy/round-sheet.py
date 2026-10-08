#!/usr/bin/env python3
"""把**每一轮巡检**的图拼成一张联系表（一层一行、一框一列），供人一眼看完整轮。

为什么要有它：一轮 48~60 张，在页面上点着看要几十次；拼成一张"轮次总图"后，
哪一层/哪一框取景不对、哪一格是黑的，一眼就能看出来。

用法（在能访问巡检台的机器上跑）：
    python round-sheet.py --out <输出目录> [--since 2026-10-06] [--api http://10.77.77.39:8001]
                          [--rotate 180] [--thumb 400] [--workers 4] [--min-images 10]

口径与边界：
- **一轮怎么切**：索引里相邻两张的时间差 > 5 分钟就算新一轮（现场一轮 7~10 分钟、
  轮间隔 3 小时，这个阈值离两边都很远）。
- **格子怎么排**：`S<层><两位框>` 解出 (层, 框)；层自上而下、框自左而右。行数按该轮
  实际最大层数（旧表是 5 层，2026-10-07 起是 4 层）。
- **标注**用索引里的**原值**（站位 id / 坐标 / 时间），不是推算的。
- `--rotate 180` 是给"相机装反"这个现状用的默认值（原始图上下颠倒、且时间戳是上游在
  翻好的画面上正着烧进去的，所以整幅转 180° 看最顺）；**上游修好后要改成 `--rotate 0`**，
  否则会翻两次。
"""
from __future__ import annotations

import argparse
import io
import json
import sys
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

FONT_CANDIDATES = (r"C:\Windows\Fonts\msyh.ttc", "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf")
GAP_S = 5 * 60


def font(size: int):
    for p in FONT_CANDIDATES:
        if Path(p).exists():
            try:
                return ImageFont.truetype(p, size)
            except Exception:  # noqa: BLE001
                pass
    return ImageFont.load_default()


def fetch_json(url: str):
    with urllib.request.urlopen(url, timeout=60) as r:
        return json.load(r)


def fetch_bytes(url: str) -> bytes:
    with urllib.request.urlopen(url, timeout=180) as r:
        return r.read()


def split_rounds(rows: list[dict]) -> list[list[dict]]:
    """按时间聚轮：相邻两张间隔 > 5 分钟 = 新一轮。"""
    rows = [r for r in rows if r.get("ts")]
    rows.sort(key=lambda r: r["ts"])
    out, cur = [], []
    for r in rows:
        if cur:
            prev = datetime.fromisoformat(cur[-1]["ts"])
            now = datetime.fromisoformat(r["ts"])
            if (now - prev).total_seconds() > GAP_S:
                out.append(cur)
                cur = []
        cur.append(r)
    if cur:
        out.append(cur)
    return out


def build_sheet(rows: list[dict], api: str, *, thumb: int, rotate: int, workers: int):
    cells: dict[tuple[int, int], Image.Image] = {}
    failed = []
    zs: dict[tuple[int, int], float] = {}

    def one(r):
        sid = r["station_id"]
        if r.get("ok") != 1:                 # 索引里已经写明失败：不请求，直接把原因带出来
            raise RuntimeError(str(r.get("error") or "索引里 ok=0"))
        url = f"{api}/api/image?object_name={urllib.parse.quote(r['object_name'])}"
        im = Image.open(io.BytesIO(fetch_bytes(url))).convert("RGB")
        if rotate:
            im = im.transpose(Image.ROTATE_180) if rotate == 180 else im
        h = round(thumb * im.height / im.width)
        return sid, im.resize((thumb, h), Image.LANCZOS)

    with ThreadPoolExecutor(max_workers=workers) as ex:
        futures = [ex.submit(one, r) for r in rows]
        for r, fut in zip(rows, futures):
            sat = r["station_id"]
            try:
                sid, im = fut.result()
            except Exception as e:  # noqa: BLE001
                failed.append((sat, str(e)[:60]))
                continue
            layer, col = int(sid[1]), int(sid[2:])
            cells[(layer, col)] = im
            zs[(layer, col)] = r.get("z")

    layers = sorted({l for l, _ in cells}) or [1]
    cols = sorted({c for _, c in cells}) or [1]
    max_col = max(cols)
    cell_h = max(im.height for im in cells.values())
    label_h = 26
    row_h = cell_h + label_h
    f = font(18)
    f2 = font(22)
    sheet = Image.new("RGB", (thumb * max_col, row_h * len(layers) + 30), (18, 18, 18))
    d = ImageDraw.Draw(sheet)
    d.text((8, 4), f"{rows[0]['ts']} → {rows[-1]['ts']}   共 {len(rows)} 张"
                   f"（成功 {sum(1 for r in rows if r.get('ok') == 1)}，取回失败 {len(failed)}）"
                   f"   批次 {rows[0].get('batch_no') or '—'}", fill=(235, 235, 235), font=f2)
    for li, layer in enumerate(layers):
        for col in cols:
            x, y = (col - 1) * thumb, 30 + li * row_h
            d.rectangle([x, y, x + thumb, y + label_h], fill=(0, 0, 0))
            z = zs.get((layer, col))
            d.text((x + 6, y + 3), f"S{layer}{col:02d}" + (f"  z={z}" if z is not None else ""),
                   fill=(255, 220, 80), font=f)
            im = cells.get((layer, col))
            if im is not None:
                sheet.paste(im, (x, y + label_h))
            else:
                reason = next((why for sid, why in failed if sid == f"S{layer}{col:02d}"), "缺")
                d.rectangle([x + 1, y + label_h + 1, x + thumb - 1, y + label_h + cell_h - 1],
                            outline=(200, 60, 60), width=2)
                d.text((x + 8, y + label_h + 8), f"✗ {reason[:28]}", fill=(255, 120, 120), font=f)
    return sheet, failed


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="round-sheet.py", description="每一轮巡检拼成一张联系表")
    ap.add_argument("--api", default="http://10.77.77.39:8001")
    ap.add_argument("--out", required=True)
    ap.add_argument("--since", default=None, help="只处理这之后的轮次（YYYY-MM-DD）")
    ap.add_argument("--rotate", type=int, default=180, choices=(0, 180),
                    help="整幅旋转角度。默认 180：相机装反、上游未修；上游修好后改 0，否则翻两次")
    ap.add_argument("--thumb", type=int, default=400, help="每格缩略宽度（默认 400）")
    ap.add_argument("--workers", type=int, default=4, help="并发下载数（默认 4，别把巡检台打满）")
    ap.add_argument("--min-images", type=int, default=10, help="少于这个张数的不算一轮（默认 10）")
    args = ap.parse_args(argv)

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    rows = fetch_json(f"{args.api}/api/images?limit=1000")["rows"]
    if args.since:
        rows = [r for r in rows if (r.get("ts") or "") >= args.since]
    rounds = [g for g in split_rounds(rows) if len(g) >= args.min_images]
    print(f"共 {len(rows)} 张、{len(rounds)} 轮（阈值：≥{args.min_images} 张/轮）")

    ok = 0
    for i, g in enumerate(rounds, 1):
        tag = g[0]["ts"].replace(":", "").replace("-", "").replace("T", "-")[:15]
        print(f"[{i}/{len(rounds)}] {g[0]['ts']} … {g[-1]['ts']}  {len(g)} 张 …", flush=True)
        sheet, failed = build_sheet(g, args.api, thumb=args.thumb, rotate=args.rotate,
                                    workers=args.workers)
        p = out / f"round_{tag}.jpg"
        sheet.save(p, "JPEG", quality=86)
        print(f"      -> {p.name}  {sheet.width}x{sheet.height}  "
              f"{p.stat().st_size // 1024} KB" + (f"  取回失败 {failed}" if failed else ""),
              flush=True)
        ok += 1
    print(f"完成：{ok}/{len(rounds)} 轮 -> {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
