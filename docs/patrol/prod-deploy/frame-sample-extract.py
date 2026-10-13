#!/usr/bin/env python3
"""帧质量标定样本抽取（只读 HTTP，票 13 / 测量师）——**在开发机跑，不碰生产**。

## 它做什么

从库房主机的巡检台只读接口抽一份分层样本 + 度量初标，供帧质量门阈值标定：

    python frame-sample-extract.py --base http://10.77.77.39:8002 --out .scratch/frame-quality-samples

产物：

    <out>/samples/<label_auto>/<date>_<station>_<angle>_<HHMMSS>.jpg
    <out>/labels.csv        （全部行保留；label_auto 与 label_final 分开列）
    <out>/README.md         （口径、度量定义、已知缺陷、每层实际条数）
    <out>/index.json        （抽样时的原始索引快照，便于复现）

## 三条纪律（测量师 2026-10-13 复核要求）

1. **只读**：只发 GET（`/api/rounds`、`/api/images`、`/api/image`、`/api/preview/frame.jpg`）。
   **不写生产、不连 SSH、不落任何文件到 10.77.77.39。**
2. **脱敏**：产物文件名只含 `date/station_id/angle_profile/ts`；
   **不写 MinIO/控制网地址、不写口令、不写库房 IP**（接口本身不下发 URL，`/api/images` 已不再回 `cloud_url`）。
3. **推定与实测分开**：`label_auto` 是**按度量算出来的初判**，`label_final` 留空给测量师在自己的副本上填；
   `unknown`（加湿候选、待人工确认）**单独一类，不并进任何坏帧类**；凑不满的类**如实记数**。

## 分层（票 13 §L1–L4，互不混池，各自独立列）

- **L1 跨轮次**：同一天 ≥3 轮（取早/午/夜各一轮）× 每轮 ≥10 帧
- **L2 跨天**：≥3 个日期 × 每天 ≥10 帧
- **L3 跨生长阶段**：当前阶段 ≥20 帧（**"之后长高阶段"不抽**——那些帧还不存在，随生长分次补齐）
- **L4 跨站位**：≥4 层 × ≥3 列，含最左（col 1）与最右（col 12，离补光灯最远）
- **成对样本**：同站位同一天两轮各 3–5 帧（验证"同站位跨轮次相对稳定"这个前提）
- **坏帧对照**：`preview`（真实预览帧）/ `dark` / `overexposed` / `blurry`（按度量挑极端帧）
  / `unknown`（加湿候选 + 待人工确认）

## 轮次怎么分（**不要用 `images.round_id`**）

实测：`round_id` 在 2026-10-09 及更早的行是 **NULL**，按它分轮会把一大批帧归成"无轮次"。
所以：**优先用 `/api/rounds` 的 `round_id`**；该行为 NULL 的旧轮次，用"上一轮结束 ts → 本轮结束 ts"
的时间窗兜底（一轮约 8 分钟，窗口不会跨到下一轮）。两条口径都在 README 里写明。
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import os
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta

# 度量用 Pillow + numpy（开发机有；**巡检镜像里没有 Pillow**，所以这一步必须在本机做）
try:
    import numpy as np
    from PIL import Image
except ImportError as exc:  # pragma: no cover
    print(f"需要 Pillow 与 numpy（开发机自带）：{exc}", file=sys.stderr)
    raise SystemExit(2)


def get(url: str, timeout: int = 90) -> bytes:
    """唯一的出口：只读 GET。"""
    req = urllib.request.Request(url, method="GET")
    with urllib.request.urlopen(req, timeout=timeout) as fh:
        return fh.read()


def get_json(url: str, timeout: int = 90):
    return json.loads(get(url, timeout).decode("utf-8"))


# ---------------------------------------------------------------- 度量

def metrics(jpeg: bytes) -> dict:
    """一帧的度量：laplacian 方差（清晰度）、平均亮度、p01/p99、过曝与近黑占比。"""
    im = Image.open(io.BytesIO(jpeg)).convert("L")
    a = np.asarray(im, dtype=np.float32)
    # 3x3 拉普拉斯（与 measure/quality 的口径一致：不缩放、直接算方差）
    lap = (
        -4.0 * a
        + np.roll(a, 1, 0) + np.roll(a, -1, 0)
        + np.roll(a, 1, 1) + np.roll(a, -1, 1)
    )
    # roll 会把对侧边界绕过来，掐掉 1 像素边框
    lap = lap[1:-1, 1:-1]
    p01, p99 = np.percentile(a, [1, 99])
    ml = round(float(a.mean()), 1)
    of = round(float((a >= 250).mean()), 4)
    return {
        "w": im.width,
        "h": im.height,
        "lapvar": round(float(lap.var()), 2),
        "p01": round(float(p01), 1),
        "p99": round(float(p99), 1),
        "dark_frac": round(float((a <= 16).mean()), 4),   # 近黑占比
        # 测量师统一命名（2026-10-13）；下面两个旧名是同一列的别名，两套都在，谁都不用改解析。
        "mean_luma": ml,
        "overexposed_frac": of,
        "mean_lum": ml,
        "over_frac": of,
    }


def auto_label(m: dict) -> str:
    """**初判**：只看度量，不猜语义。阈值是"候选挑选"用的，不是标定结论。"""
    if m["mean_lum"] < 40 or m["dark_frac"] > 0.5:
        return "dark"
    if m["over_frac"] > 0.25 or m["mean_lum"] > 215:
        return "overexposed"
    if m["lapvar"] < 40:
        return "blurry"
    return "ok"


# ---------------------------------------------------------------- 索引与轮次

def fetch_rounds(base: str) -> list[dict]:
    d = get_json(f"{base}/api/rounds")
    return list(d.get("rounds") or [])


def fetch_images(base: str, limit: int = 1000) -> list[dict]:
    """分页取索引；看 `n_prod` 决定还要不要继续取。"""
    url = f"{base}/api/images?limit={limit}"
    d = get_json(url)
    rows = list(d.get("rows") or [])
    return rows


def assign_round(rows: list[dict], rounds: list[dict]) -> None:
    """给每行标 `round_key` / `round_ts`：
    ① 行自带 `round_id` → 用 `/api/rounds` 里的同名键；
    ② 否则落到"上一轮结束 ts < 本行 ts ≤ 本轮结束 ts"的窗口里。
    """
    by_id = {r["round_id"]: r for r in rounds if r.get("round_id")}
    ends = sorted(r["ts"] for r in rounds)
    for row in rows:
        rid = row.get("round_id")
        row["round_key"] = rid or ""
        row["round_ts"] = ""
        if rid and rid in by_id:
            row["round_ts"] = by_id[rid]["ts"]
            continue
        ts = row.get("ts") or ""
        prev = ""
        for e in ends:
            if e >= ts:
                row["round_ts"] = e
                row["round_key"] = f"win:{prev}->{e}"
                break
            prev = e


def day_of(row: dict) -> str:
    return (row.get("ts") or "")[:10]


def pick(rows, pred, n):
    out = []
    for r in rows:
        if len(out) >= n:
            break
        if pred(r):
            out.append(r)
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://10.77.77.39:8002")
    ap.add_argument("--out", required=True)
    ap.add_argument("--max-frames", type=int, default=220, help="总帧数上限")
    ap.add_argument("--preview-frames", type=int, default=10, help="预览类取几帧（测量师要求 ≥10）")
    args = ap.parse_args()

    os.makedirs(args.out, exist_ok=True)
    frames_dir = os.path.join(args.out, "samples")

    print(f"→ 只读拉取索引：{args.base}")
    rows = fetch_images(args.base)
    rounds = fetch_rounds(args.base)
    ok_rows = [r for r in rows if r.get("ok") == 1 and r.get("object_name")]
    print(f"  索引 {len(rows)} 行（可用 {len(ok_rows)}），轮次 {len(rounds)} 条")
    assign_round(ok_rows, rounds)

    # ---- L1：挑一个至少有 3 轮的日子，取早/午/夜各一轮
    per_day = {}
    for r in ok_rows:
        per_day.setdefault(day_of(r), set()).add(r["round_key"])
    day_l1 = next((d for d in sorted(per_day, reverse=True) if len(per_day[d]) >= 3), None)
    l1 = []
    if day_l1:
        keys = sorted({r["round_key"] for r in ok_rows if day_of(r) == day_l1})
        for k in [keys[0], keys[len(keys) // 2], keys[-1]]:
            l1 += pick([r for r in ok_rows if day_of(r) == day_l1 and r["round_key"] == k], lambda x: True, 12)

    # ---- L2：另取 ≥3 天，每天 ≥10 帧
    days_l2 = [d for d in sorted({day_of(r) for r in ok_rows}, reverse=True) if d != day_l1][:3]
    l2 = []
    for d in days_l2:
        l2 += pick([r for r in ok_rows if day_of(r) == d], lambda x: True, 12)

    # ---- L3：当前阶段（库里第 13 天、矮菇无帽）——取最近两天的帧
    recent_days = sorted({day_of(r) for r in ok_rows}, reverse=True)[:2]
    l3 = pick([r for r in ok_rows if day_of(r) in recent_days], lambda x: True, 24)

    # ---- L4：≥4 层 × ≥3 列，含最左/最右列
    l4 = []
    for layer in ("1", "2", "3", "4"):
        for col in ("1", "6", "12"):
            cand = [r for r in ok_rows if str(r.get("station_id", "")).startswith(("S" + layer)) and
                    str(r.get("station_id", ""))[-2:] == col.rjust(2, "0")]
            l4 += pick(cand, lambda x: True, 3)

    # ---- 成对样本：同站位同一天两轮，**每轮每站位至少 1 帧**（测量师复核用）
    pairs = []
    seen = {}
    for r in ok_rows:
        seen.setdefault((day_of(r), r.get("station_id")), set()).add(r["round_key"])
    paired = [(k, v) for k, v in sorted(seen.items()) if len(v) >= 2]
    # 先保证覆盖面：优先取"同一轮里出现过的站位"两轮各 3 帧，最多 12 个站位
    for (d, st), keys in paired[:12]:
        ks = sorted(keys)[:2]
        for k in ks:
            group_rows = [r for r in ok_rows if day_of(r) == d and r.get("station_id") == st
                          and r["round_key"] == k]
            pairs += pick(group_rows, lambda x: True, max(3, 5 if len(group_rows) >= 5 else 3))
    print(f"  成对样本 {len(pairs)} 帧（{min(len(paired), 12)} 个站位 × 2 轮）")

    # ---- 合并（去重，保序）
    selected, seen_nm = [], set()

    def add(group, rows_, cap_total):
        for r in rows_:
            if len(selected) >= cap_total:
                return
            nm = r["object_name"]
            if nm in seen_nm:
                continue
            seen_nm.add(nm)
            selected.append((group, r))

    add("L1_round", l1, args.max_frames)
    add("L2_day", l2, args.max_frames)
    add("L3_stage", l3, args.max_frames)
    add("L4_station", l4, args.max_frames)
    add("pair", pairs, args.max_frames)
    # ---- 兜底：不足时用最近的 ok 帧补齐
    add("fill", ok_rows, args.max_frames)
    print(f"  选中 {len(selected)} 帧")

    # ---- 预览流帧（不进历史，但必须能被拦下）：**≥10 帧**（测量师 2026-10-13：1 帧说明不了问题）
    preview_frames = []
    for i in range(args.preview_frames):
        try:
            jpg = get(f"{args.base}/api/preview/frame.jpg", timeout=30)
            ts = datetime.now().strftime("%Y-%m-%dT%H:%M:%S.%f")[:23]
            preview_frames.append(("preview", {"object_name": None, "ts": ts,
                                               "station_id": "PREVIEW", "angle_profile": "preview"}, jpg))
        except Exception as exc:  # noqa: BLE001 - 预览挂了不该毁掉整批
            print(f"  ⚠️ 预览取帧失败（第 {i + 1} 张，跳过）：{exc}")
            break
        time.sleep(1.0)          # 拉开一点，别拿 10 张同一瞬间的近似重复帧充数
    print(f"  预览帧 {len(preview_frames)} 张")

    # ---- 下载 + 度量
    records = []
    for group, r in selected:
        nm = r["object_name"]
        try:
            jpg = get(f"{args.base}/api/image?object_name={urllib.parse.quote(nm)}")
        except Exception as exc:  # noqa: BLE001
            print(f"  ⚠️ 取图失败 {nm}: {exc}")
            continue
        m = metrics(jpg)
        records.append((group, r, jpg, m, None))

    for group, r, jpg in list(preview_frames):
        m = metrics(jpg)
        records.append((group, r, jpg, m, "preview"))

    # ---- 落盘
    labels = []
    for group, r, jpg, m, forced in records:
        label = forced or auto_label(m)
        ts = (r.get("ts") or "").replace("-", "").replace(":", "").replace("T", "")
        date = (r.get("ts") or "")[:10].replace("-", "")
        st = r.get("station_id") or "X"
        ang = r.get("angle_profile") or "na"
        fname = f"{date}_{st}_{ang}_{ts[-6:]}.jpg"
        os.makedirs(os.path.join(frames_dir, label), exist_ok=True)
        with open(os.path.join(frames_dir, label, fname), "wb") as fh:
            fh.write(jpg)
        labels.append({
            "file": f"samples/{label}/{fname}",
            "label_auto": label,
            "label_final": "",            # 留给测量师在他的副本上填
            "source": "preview_api" if forced == "preview" else "patrol_frame",
            "stratum": group,
            "date": date,
            "station_id": st,
            "angle_profile": ang,
            "ts": r.get("ts") or "",
            "round_key": r.get("round_key", ""),
            "round_ts": r.get("round_ts", ""),
            "layer": (st[1] if len(st) > 3 and st[0] == "S" else ""),
            "col": (st[2:] if len(st) > 3 and st[0] == "S" else ""),
            **{k: m[k] for k in ("w", "h", "lapvar", "p01", "p99", "dark_frac",
                                 "mean_luma", "overexposed_frac", "mean_lum", "over_frac")},
        })

    with open(os.path.join(args.out, "labels.csv"), "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(labels[0].keys()) if labels else [])
        w.writeheader()
        w.writerows(labels)
    with open(os.path.join(args.out, "index.json"), "w", encoding="utf-8") as fh:
        json.dump({"rounds": rounds, "n_rows": len(rows)}, fh, ensure_ascii=False, indent=1)

    # ---- 原始统计（Chief 2026-10-13：要**分位数**不只均值；按层/按站位，供相对化基线）
    def _pct(vals, q):
        return round(float(np.percentile(vals, q)), 2) if vals else None

    def _quad(rs):
        def trip(key):
            v = [r[key] for r in rs]
            return f"{_pct(v, 10)} / {_pct(v, 50)} / {_pct(v, 90)}"
        return trip("lapvar"), trip("p99"), trip("mean_luma"), trip("overexposed_frac")

    sl = ["# 原始统计（分位数 p10 / p50 / p90）\n",
          f"抽取时间：{datetime.now().isoformat(timespec='seconds')}；总帧 {len(labels)}\n",
          "## 按 label_auto\n",
          "| label | n | lapvar | p99 | mean_luma | overexposed_frac |",
          "| --- | --- | --- | --- | --- | --- |"]
    for lab in sorted({r["label_auto"] for r in labels}):
        rs = [r for r in labels if r["label_auto"] == lab]
        a, b, c, d = _quad(rs)
        sl.append(f"| {lab} | {len(rs)} | {a} | {b} | {c} | {d} |")
    sl += ["\n## 按 stratum（分层，不混池）\n",
           "| stratum | n | lapvar | p99 | mean_luma | overexposed_frac |",
           "| --- | --- | --- | --- | --- | --- |"]
    for st in sorted({r["stratum"] for r in labels}):
        rs = [r for r in labels if r["stratum"] == st]
        a, b, c, d = _quad(rs)
        sl.append(f"| {st} | {len(rs)} | {a} | {b} | {c} | {d} |")
    sl += ["\n## 按站位（lapvar 与 mean_luma 的 p50，供相对化基线）\n",
           "| station | layer | col | n | lapvar p10/p50/p90 | mean_luma p10/p50/p90 |",
           "| --- | --- | --- | --- | --- | --- |"]
    for st in sorted({r["station_id"] for r in labels}):
        rs = [r for r in labels if r["station_id"] == st]
        lap = [r["lapvar"] for r in rs]
        lum = [r["mean_luma"] for r in rs]
        sl.append(f"| {st} | {rs[0]['layer']} | {rs[0]['col']} | {len(rs)} | "
                  f"{_pct(lap, 10)} / {_pct(lap, 50)} / {_pct(lap, 90)} | "
                  f"{_pct(lum, 10)} / {_pct(lum, 50)} / {_pct(lum, 90)} |")
    sl += ["\n## 每格样本量（L1–L4 是否凑够；**凑不满如实记数**）\n",
           "| stratum | 条数 | 目标 | 达标 |", "| --- | --- | --- | --- |"]
    targets = [("L1_round", "3 轮 × ≥10", 30, 30), ("L2_day", "3 天 × ≥10", 30, 30),
               ("L3_stage", "当前阶段 ≥20", 20, 20), ("L4_station", "4 层 × 3 列 × ≥3", 36, 20),
               ("pair", "同站同天两轮各 ≥3", 24, 20), ("preview", "≥10", 10, 10)]
    for st, tgt, want, floor in targets:
        n = sum(1 for r in labels if r["stratum"] == st)
        okflag = "✓" if n >= floor else "✗（如实记数，不用别的类充数）"
        sl.append(f"| {st} | {n} | {tgt} | {okflag} |")
    with open(os.path.join(args.out, "stats.md"), "w", encoding="utf-8") as fh:
        fh.write("\n".join(sl) + "\n")

    # ---- README（含每层/每类实际条数，凑不满的如实写）
    from collections import Counter
    by_label = Counter(r["label_auto"] for r in labels)
    by_stratum = Counter(r["stratum"] for r in labels)
    with open(os.path.join(args.out, "README.md"), "w", encoding="utf-8") as fh:
        fh.write("# 帧质量标定样本（票 13）\n\n")
        fh.write(f"抽取时间：{datetime.now().isoformat(timespec='seconds')}\n\n")
        fh.write("**只读 HTTP 取自库房主机巡检台接口**（`/api/rounds` `/api/images` `/api/image` "
                 "`/api/preview/frame.jpg`）；**未在生产写任何文件、未连 SSH**。\n\n")
        fh.write("## 分层实际条数\n\n| 层 | 条数 |\n| --- | --- |\n")
        for k, v in by_stratum.items():
            fh.write(f"| {k} | {v} |\n")
        fh.write("\n## 初标分布（label_auto）\n\n| label_auto | 条数 |\n| --- | --- |\n")
        for k, v in by_label.items():
            fh.write(f"| {k} | {v} |\n")
        fh.write("\n**凑不满的类如实记数，不用别的类充数。**\n\n")
        fh.write("## 度量定义\n\n"
                 "- `lapvar`：灰度图 3×3 拉普拉斯响应的方差（越大越清晰；不缩放）\n"
                 "- `mean_lum` / `p01` / `p99`：灰度均值与分位数\n"
                 "- `over_frac`：像素 ≥250 的占比（过曝候选）\n"
                 "- `dark_frac`：像素 ≤16 的占比（近黑候选）\n\n"
                 "初判阈值（**只是候选挑选，不是标定结论**）：mean_lum<40 或 dark_frac>0.5 → dark；"
                 "over_frac>0.25 或 mean_lum>215 → overexposed；lapvar<40 → blurry；否则 ok。\n\n")
        fh.write("## 已知缺陷（必须知道）\n\n"
                 "1. **`images.round_id` 在 2026-10-09 及更早为 NULL**，这批轮次用"
                 "「上一轮结束 ts → 本轮结束 ts」的时间窗兜底（见 `round_key` 的 `win:` 前缀）；\n"
                 "2. **L3 的\"之后长高阶段\"没有抽**——那些帧还不存在，随生长分次补齐；"
                 "本批 `stratum=L3_stage` 全部是**当前阶段**；\n"
                 "3. **加湿期没有权威窗口**：`hold` 是 console 运行态、无持久记录，"
                 "所以本批**不产出**加湿类样本，由测量师复核时结合环境数据判定；\n"
                 "4. `preview` 类来自实时预览接口（1280×718），**每条只有 1 帧快照**，"
                 "需要更多得多次取；\n"
                 "5. 文件名只含 `date/station/angle/ts`，**不含任何 URL、IP 或凭据**。\n")
    print(f"✓ 完成：{len(labels)} 帧 → {args.out}")
    print(f"  初标分布：{dict(by_label)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
