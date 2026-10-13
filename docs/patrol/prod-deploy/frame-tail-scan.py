#!/usr/bin/env python3
"""全池帧质量下尾扫描（只读 HTTP，票 13 复核用）——**在开发机跑，不碰生产**。

## 为什么有这一步

测量师按 218 帧样本把"清晰度绝对下限"定在 **45.0**（依据：ok 帧 `lapvar` min=48.0 / p05=52.6），
误杀率报 **0/218**。这是**用最小值撑起来的判据**：样本里恰好没有更钝的帧，就说明不了"下限安全"。
而巡检池实际有 **3147 帧 / 跨 2026-09-13 → 10-13**，之前那 230 帧只来自**最新 1000 行窗口**。

所以本脚本把**全池**扫一遍，给出：
  - 真实的 `lapvar` 下尾（min / p01 / p05）与**逐分辨率**分布（不同分辨率 `lapvar` 不可混池）；
  - `lapvar < 45 / 40 / 35 / 30 / 20` 各档**真实条数** ⇒ 45.0 在全池上的**误杀率**；
  - 下尾 / 过曝 / 近黑三类的**原始帧副本**，供测量师人工复核。

## 定向抽"失焦帧"的事件窗口（核查结论）

测量师要求"按事件窗口定向抽失焦帧（换角/重固定支架后的头几帧，或现场说拍糊了的那几帧）"。
查遍仓库记录：**换角/重固定支架从未发生**（2026-10-10 现场确认支架仍在平视位，且"是否换角不预设"），
现场也没有"拍糊了"的登记 ⇒ **事件窗口为空**。唯一真实的失焦来源记录是 2026-10-06/07 的
**预览**扫描（`station-retarget.md` §6/§7），那是预览帧、不在本池。

⇒ 所以不用"事件窗口"而用**全池下尾穷举**做定向：不是"挑极端帧"，是把整池的低尾**穷举**出来。

## 纪律

1. **只读**：只发 GET（`/api/images`、`/api/image`）。不写生产、不连 SSH。
2. **脱敏**：文件名只含 `date/station/angle/ts`；不写 IP、URL、凭据。
3. **不混池**：逐分辨率分别统计；`preview` 不进本扫描。
4. **如实记数**：失败、凑不满、看不见的一律写明，不用别的类充数。

用法：

    # 全量扫（约 30 分钟，3147 帧）
    python frame-tail-scan.py --base http://10.77.77.39:8002 --out .scratch/frame-quality-samples/tail

    # 已扫过一轮（all-metrics.csv 在），只重算统计+补抽副本（几分钟）
    python frame-tail-scan.py --out .scratch/frame-quality-samples/tail --from-csv <all-metrics.csv>
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
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

try:
    import numpy as np
    from PIL import Image
except ImportError as exc:  # pragma: no cover
    print(f"需要 Pillow 与 numpy（开发机自带）：{exc}", file=sys.stderr)
    raise SystemExit(2)

METRIC_KEYS = ("w", "h", "lapvar", "p01", "p99", "dark_frac", "mean_luma", "overexposed_frac")


def get(url: str, timeout: int = 90, tries: int = 3) -> bytes:
    """唯一的出口：只读 GET，失败重试（**不静默吞掉**）。"""
    last: Exception | None = None
    for i in range(tries):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, method="GET"), timeout=timeout) as fh:
                return fh.read()
        except Exception as exc:  # noqa: BLE001
            last = exc
            time.sleep(1.0 + i)
    raise RuntimeError(f"GET 失败 {tries} 次：{url}：{last}")


def metrics(jpeg: bytes) -> dict:
    """与 `frame-sample-extract.py` **逐字同口径**（测量师已核过两处逐帧一致）。"""
    im = Image.open(io.BytesIO(jpeg)).convert("L")
    a = np.asarray(im, dtype=np.float32)
    lap = (
        -4.0 * a
        + np.roll(a, 1, 0) + np.roll(a, -1, 0)
        + np.roll(a, 1, 1) + np.roll(a, -1, 1)
    )
    lap = lap[1:-1, 1:-1]
    p01, p99 = np.percentile(a, [1, 99])
    return {
        "w": im.width,
        "h": im.height,
        "lapvar": round(float(lap.var()), 2),
        "p01": round(float(p01), 1),
        "p99": round(float(p99), 1),
        "dark_frac": round(float((a <= 16).mean()), 4),
        "mean_luma": round(float(a.mean()), 1),
        "overexposed_frac": round(float((a >= 250).mean()), 4),
    }


def fname_of(r: dict) -> str:
    ts = (r.get("ts") or "").replace("-", "").replace(":", "").replace("T", "")
    return (f"{(r.get('ts') or '')[:10].replace('-', '')}_{r.get('station_id') or 'X'}_"
            f"{r.get('angle_profile') or 'na'}_{ts[-6:]}.jpg")


def enumerate_pool(base: str) -> tuple[list[dict], dict]:
    """48 个站位各拉一次（接口无 offset，但 station_id 过滤足够覆盖单站全部历史）。"""
    head = json.loads(get(f"{base}/api/images?limit=2000").decode())
    stations = sorted({r["station_id"] for r in head["rows"] if r.get("station_id")})
    by_name: dict[str, dict] = {}
    for st in stations:
        q = urllib.parse.urlencode({"station_id": st, "limit": 2000})
        d = json.loads(get(f"{base}/api/images?{q}").decode())
        for r in d.get("rows") or []:
            if r.get("object_name"):
                by_name[r["object_name"]] = r
    meta = {
        "stations": stations,
        "n_head_window": len(head["rows"]),
        "head_newest": head["rows"][0]["ts"] if head["rows"] else None,
        "head_oldest": head["rows"][-1]["ts"] if head["rows"] else None,
    }
    return list(by_name.values()), meta


def load_csv(path: str):
    """从上一轮 all-metrics.csv 复原 (r, m, None)；省掉再下一次 3147 帧。"""
    results = []
    with open(path, newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            r = {k: row.get(k) for k in
                 ("object_name", "ts", "round_id", "station_id", "angle_profile", "source")}
            m = {k: (float(row[k]) if k not in ("w", "h") else int(row[k])) for k in METRIC_KEYS}
            results.append((r, m, None))
    return results


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://10.77.77.39:8002")
    ap.add_argument("--out", required=True)
    ap.add_argument("--from-csv", default="", help="复用已扫好的 all-metrics.csv，只重算统计 + 补抽副本")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--tail-floor", type=float, default=50.0,
                    help="把 lapvar 低于此值的帧留副本供人工复核（不是判据）")
    ap.add_argument("--over-floor", type=float, default=0.05,
                    help="过曝候选：overexposed_frac 大于此值（measure 默认门就是 0.05）")
    args = ap.parse_args()

    os.makedirs(args.out, exist_ok=True)
    errors: list[str] = []

    if args.from_csv:
        print(f"→ 复用已扫好的度量：{args.from_csv}")
        results = load_csv(args.from_csv)
        rows = [r for r, _, _ in results]
        all_ts = sorted((r.get("ts") or "") for r in rows)
        meta = {"stations": sorted({r["station_id"] for r in rows if r.get("station_id")}),
                "n_head_window": None,
                "head_newest": all_ts[-1] if all_ts else None,
                "head_oldest": all_ts[0] if all_ts else None}
        print(f"  {len(results)} 帧（不重新下载）")
        t0 = time.time()
    else:
        print(f"→ 枚举全池（只读）：{args.base}")
        rows_all, meta = enumerate_pool(args.base)
        ok = [r for r in rows_all if r.get("ok") == 1 and r.get("object_name")]
        print(f"  池内 {len(rows_all)} 帧（ok 且有名 {len(ok)}），站位 {len(meta['stations'])}")
        print(f"  最新 2000 行窗口：{meta['head_newest']} → {meta['head_oldest']}")

        def one(r: dict):
            nm = r["object_name"]
            jpg = get(f"{args.base}/api/image?object_name={urllib.parse.quote(nm)}", timeout=120)
            return r, metrics(jpg), jpg

        results = []
        done = 0
        t0 = time.time()
        with ThreadPoolExecutor(max_workers=args.workers) as pool:
            for res in pool.map(lambda r: _safe(one, r, errors), ok):
                done += 1
                if res is not None:
                    results.append(res)
                if done % 250 == 0:
                    print(f"  … {done}/{len(ok)}  ({time.time() - t0:.0f}s)")
        print(f"  下载+度量完成：{len(results)} 帧，失败 {len(errors)}，用时 {time.time() - t0:.0f}s")

        all_csv = os.path.join(args.out, "all-metrics.csv")
        fields = ["object_name", "ts", "date", "round_id", "station_id", "angle_profile", "source",
                  "bytes", "w", "h", "lapvar", "p01", "p99", "dark_frac", "mean_luma",
                  "overexposed_frac"]
        with open(all_csv, "w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=fields)
            w.writeheader()
            for r, m, jpg in sorted(results, key=lambda x: x[0].get("ts") or ""):
                w.writerow({"object_name": r["object_name"], "ts": r.get("ts"),
                            "date": (r.get("ts") or "")[:10], "round_id": r.get("round_id") or "",
                            "station_id": r.get("station_id"), "angle_profile": r.get("angle_profile"),
                            "source": r.get("source"), "bytes": len(jpg) if jpg else "", **m})

    # ------------------------------------------------------------------ 统计
    groups: dict[tuple, list] = defaultdict(list)
    for r, m, _ in results:
        groups[(m["w"], m["h"])].append((r, m))
    cuts = [20, 30, 35, 40, 45, 48, 50, 55, 100]

    lines = ["# 全池帧质量下尾扫描（票 13 复核）", "",
             f"扫描时间：{datetime.now().isoformat(timespec='seconds')}；"
             "只读 HTTP（`/api/images` `/api/image`），**未在生产写任何文件**。", "",
             "## 池子覆盖", "",
             f"- 本批覆盖时间范围：`{meta.get('head_oldest')}` → `{meta.get('head_newest')}`",
             f"- 按 {len(meta['stations'])} 个站位分页穷举 → 实际算完：**{len(results)} 帧**"
             f"（取图失败 **{len(errors)}**）",
             f"- 站位：{len(meta['stations'])} 个",
             "",
             "> ⚠️ 已知覆盖限制：接口无 offset，只能靠 `station_id` 过滤回捞；"
             "**站名为空或不在 48 站之列的更老帧看不到**。数字如实记，不拿估计顶替。", ""]

    lines += ["## 事件窗口核查（测量师要的\"定向抽失焦\"）", "",
              "| 候选事件窗口 | 是否存在 | 依据 |", "| --- | --- | --- |",
              "| 换角 / 重固定相机支架后的头几帧 | **不存在** | 2026-10-10 现场确认支架仍在平视位；"
              "「是否换角不预设」（`station-retarget.md` §10.2/§10.3、`field-checklist.md`）→ 该作业从未发生 |",
              "| 现场登记\"拍糊了\"的那几帧 | **不存在** | 仓库内无此类登记（全仓库搜 `拍糊/拍虚/失焦帧/重固定` "
              "只命中取景建议与设计文档，无事件记录） |",
              "| git 历史里 10-07 之后的支架/对焦动作 | **不存在** | `git log --since=2026-10-07` 里只有两条"
              "**文档**提交（`d86f491` 换角不预设、`1d5c167` 票名更正），无现场作业记录 |",
              "| 2026-10-06/07 深夜 Z/Y 扫描的\"失焦平面\"帧 | 存在但**不在本池** | `station-retarget.md` §6/§7："
              "那是**预览帧**（`/api/preview/frame.jpg`），产物在会话目录，未进巡检历史 |",
              "",
              "⇒ **事件窗口为空**，所以用**全池下尾穷举**代替\"按事件定向取\"："
              f"把 {len(results)} 帧的 `lapvar` 全算一遍，低尾有多少就报多少，不做主观挑选。", ""]

    for (w_, h_), rs in sorted(groups.items(), key=lambda kv: -len(kv[1])):
        v = [m["lapvar"] for _, m in rs]
        lines += [f"## 分组 {w_}×{h_}（n={len(v)}）", "",
                  "| 指标 | 值 |", "| --- | --- |",
                  f"| lapvar min | {min(v)} |",
                  f"| p01 | {round(float(np.percentile(v, 1)), 2)} |",
                  f"| p05 | {round(float(np.percentile(v, 5)), 2)} |",
                  f"| p10 | {round(float(np.percentile(v, 10)), 2)} |",
                  f"| p50 | {round(float(np.percentile(v, 50)), 2)} |",
                  f"| p90 | {round(float(np.percentile(v, 90)), 2)} |",
                  f"| max | {max(v)} |",
                  "",
                  "| 阈值 | 低于它的帧数 | 占比 |", "| --- | --- | --- |"]
        for c in cuts:
            n = sum(1 for x in v if x < c)
            lines.append(f"| lapvar < {c} | {n} | {round(100 * n / len(v), 2)}% |")
        lines.append("")

    perday: dict[str, list] = defaultdict(list)
    for r, m, _ in results:
        perday[(r.get("ts") or "")[:10]].append(m["lapvar"])
    lines += ["## 逐日 lapvar（min / p05 / p50）", "",
              "| 日期 | n | min | p05 | p50 |", "| --- | --- | --- | --- | --- |"]
    for d in sorted(perday):
        v = perday[d]
        lines.append(f"| {d} | {len(v)} | {min(v)} | {round(float(np.percentile(v, 5)), 2)} | "
                     f"{round(float(np.percentile(v, 50)), 2)} |")
    lines.append("")

    perst: dict[str, list] = defaultdict(list)
    for r, m, _ in results:
        perst[r.get("station_id") or "?"].append(m["lapvar"])
    lines += ["## 逐站位 lapvar（min / p50）", "",
              "| 站位 | n | min | p50 |", "| --- | --- | --- | --- |"]
    for s in sorted(perst):
        v = perst[s]
        lines.append(f"| {s} | {len(v)} | {min(v)} | {round(float(np.percentile(v, 50)), 2)} |")
    lines.append("")

    # ------------------------------------------------------------------ 副本
    def want(r, m):
        if m["lapvar"] < args.tail_floor:
            return "low_lapvar"
        if m["overexposed_frac"] > args.over_floor:
            return "overexposed"
        if m["dark_frac"] > 0.5 or m["p99"] < 5:
            return "dark"
        return None

    sel = [(want(r, m), r, m, jpg) for r, m, jpg in results]
    sel = [s_ for s_ in sel if s_[0]]
    counts = Counter(s_[0] for s_ in sel)
    print(f"  副本候选：{dict(counts)}")

    # 缺字节的（--from-csv）只补抽这些帧
    need = [(r, m) for _, r, m, jpg in sel if jpg is None]
    fetched: dict[str, bytes] = {}
    if need:
        print(f"  --from-csv 模式：只补下 {len(need)} 帧副本（不重下全池）")
        def grab(r):
            return r["object_name"], get(
                f"{args.base}/api/image?object_name={urllib.parse.quote(r['object_name'])}", timeout=120)
        with ThreadPoolExecutor(max_workers=args.workers) as pool:
            for nm, jpg in pool.map(lambda r: _safe(grab, r, errors), [r for r, _ in need]):
                if nm:
                    fetched[nm] = jpg

    cand_rows = []
    for cls, r, m, jpg in sel:
        jpg = jpg or fetched.get(r["object_name"])
        if not jpg:
            errors.append(f"副本缺失 {r['object_name']}")
            continue
        d = os.path.join(args.out, cls)
        os.makedirs(d, exist_ok=True)
        fn = fname_of(r)
        with open(os.path.join(d, fn), "wb") as f2:
            f2.write(jpg)
        cand_rows.append({"file": f"{cls}/{fn}", "class": cls, "lapvar": m["lapvar"],
                          "overexposed_frac": m["overexposed_frac"], "dark_frac": m["dark_frac"],
                          "p99": m["p99"], "mean_luma": m["mean_luma"], "ts": r.get("ts"),
                          "station_id": r.get("station_id"), "angle_profile": r.get("angle_profile"),
                          "w": m["w"], "h": m["h"], "object_name": r["object_name"]})

    with open(os.path.join(args.out, "candidates.csv"), "w", newline="", encoding="utf-8") as fh:
        if cand_rows:
            w_ = csv.DictWriter(fh, fieldnames=list(cand_rows[0].keys()))
            w_.writeheader()
            w_.writerows(cand_rows)

    n_over = sum(1 for _, m, _ in results if m["overexposed_frac"] > args.over_floor)
    n_dark = sum(1 for _, m, _ in results if m["dark_frac"] > 0.5 or m["p99"] < 5)
    lines += [f"## 副本（人工复核用，**不是判据**）", "",
              f"- `low_lapvar/`：`lapvar < {args.tail_floor}` → **{counts.get('low_lapvar', 0)} 帧**",
              f"- `overexposed/`：`overexposed_frac > {args.over_floor}`（measure 默认门）→ "
              f"**全池 {n_over} 帧**，本次留副本 {counts.get('overexposed', 0)} 帧",
              f"- `dark/`：`dark_frac > 0.5` 或 `p99 < 5` → **全池 {n_dark} 帧**，"
              f"本次留副本 {counts.get('dark', 0)} 帧",
              "",
              "清单：`candidates.csv`。低 `lapvar` 既可能是**真失焦**，也可能是**低纹理的正常帧**"
              "（平视 + 矮菇 + 强晕影本就低纹理）——**这个区分必须人工看图，脚本不替谁下结论**。", ""]
    for cls in ("low_lapvar", "overexposed", "dark"):
        sub = [c for c in cand_rows if c["class"] == cls]
        if not sub:
            continue
        lines.append(f"### {cls}（{len(sub)} 帧）")
        lines.append("")
        for c in sorted(sub, key=lambda x: float(x["lapvar"]))[:30]:
            lines.append(f"- `lapvar={c['lapvar']}` `over={c['overexposed_frac']}` "
                         f"`p99={c['p99']}` {c['ts']} {c['station_id']}")
        if len(sub) > 30:
            lines.append(f"- …另 {len(sub) - 30} 帧见 `candidates.csv`")
        lines.append("")

    if errors:
        lines += ["## 取图失败 / 缺失（如实记账，未用别的帧顶替）", ""] + [f"- {e}" for e in errors] + [""]

    with open(os.path.join(args.out, "summary.md"), "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines))

    print(f"✓ 完成：{len(results)} 帧 → {args.out}")
    for (w_, h_), rs in sorted(groups.items(), key=lambda kv: -len(kv[1])):
        v = [m["lapvar"] for _, m in rs]
        print(f"  {w_}×{h_} n={len(v)} min={min(v)} p05={round(float(np.percentile(v, 5)), 2)} "
              f"<45={sum(1 for x in v if x < 45)} <40={sum(1 for x in v if x < 40)}")
    return 0


def _safe(fn, r, errors):
    try:
        return fn(r)
    except Exception as exc:  # noqa: BLE001
        errors.append(f"{r.get('object_name')}: {exc}")
        return None


if __name__ == "__main__":
    raise SystemExit(main())
