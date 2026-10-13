"""只读校验一轮巡检的内容完整性（票 06/13 的复验工具）。

用途：采图服务上生产后，**下一轮巡检结束**时拿那一轮的 outbox 行（或 `/api` 拉到的
round + image_index 行）跑一遍，把票 06 的五条验收一次说清：

1. 服务侧实时指纹落进了索引（`content_sha256`，或 `same_as_previous`/`duplicate_streak`）；
2. **48 帧全入库**（`n_frames_recorded` = 帧行数）；
3. `n_results` = 成功取图 = 帧数 − `n_duplicate`；
4. `n_duplicate` = 带 `frame_dup` 的帧数，且与实时指纹（相同 `content_sha256` 的跨站分组）一致；
5. **正常轮** `suspect_duplicate` 不出现（或为假）。

只用标准库；**不连生产、不改任何东西**——行从哪来由调用方决定。

    python patrol/tools/verify_round_integrity.py rows.jsonl
    python patrol/tools/verify_round_integrity.py rows.jsonl --round-id 20261013-212700-1
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path


#: 帧上没有服务侧指纹时，**先怀疑版本**——仓库有 ≠ 生产有（Chief 2026-10-13 更正：
#: 那一轮 `mushroom_analysis` 是旧镜像、`mushroom_patrol` 是没有新字段的 `b64027b`）。
_DEPLOY_NOTE = ("未部署/版本不符：帧上没有服务侧实时指纹字段 ⇒ **先确认生产跑的是哪一版**"
                "（仓库有 ≠ 生产有），不要把这条 FAIL 读成「采图坏了」")


def classify(checks: list[tuple[str, bool, str]]) -> str:
    """给报告一句"这堆 FAIL 该怎么读"。缺服务侧指纹 ⇒ 未部署，不是采集故障。"""
    verdict = {name: ok for name, ok, _ in checks}
    if not verdict.get("服务侧实时指纹落在帧上", True):
        return _DEPLOY_NOTE
    return ""


def load_rows(path: str) -> list[dict]:
    """读 JSONL（outbox 行）；也接受一个 JSON 数组（接口响应）。坏行跳过。"""
    text = Path(path).read_text(encoding="utf-8")
    stripped = text.lstrip()
    if stripped.startswith("["):
        data = json.loads(stripped)
        return [r for r in data if isinstance(r, dict)]
    out: list[dict] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(row, dict):
            out.append(row)
    return out


def _kind(row: dict) -> str | None:
    """行是哪一层来的都能认：outbox 行自带 `kind`；库里拉出来的行没有 `kind`。

    轮行有 `n_results`/`n_failures`；帧行有 `station_id`/`object_name`。认不出来就跳过，
    不猜（宁少不假）。
    """
    if row.get("kind") in ("round", "image_index"):
        return row["kind"]
    if "n_results" in row or "n_failures" in row:
        return "round"
    if "station_id" in row or "object_name" in row or "box_id" in row:
        return "image_index"
    return None


def verify(rows: list[dict], *, round_id: str | None = None) -> list[tuple[str, bool, str]]:
    """返回 `[(检查名, 是否通过, 说明)]`；空行/没有轮行时给一条失败说明。

    三层通用：**outbox 行**（带 `kind`）、**库里 `/rounds`+`/images` 拉回来的行**（无 `kind`）、
    以及两者混合。所以复验可以 outbox → 库 → 索引逐层跑同一条命令。
    """
    normalized = [(r, _kind(r)) for r in rows]
    rounds = [r for r, k in normalized if k == "round"]
    if round_id:
        rounds = [r for r in rounds if str(r.get("round_id")) == round_id]
    if not rounds:
        return [("找到轮行", False, "没有 round 行（--round-id 可能写错）")]
    latest = sorted(rounds, key=lambda r: str(r.get("ts", "")))[-1]
    rid = latest.get("round_id")
    frames = [r for r, k in normalized if k == "image_index" and r.get("round_id") == rid]

    checks: list[tuple[str, bool, str]] = []
    n_frames = len(frames)
    dup = [f for f in frames if f.get("frame_dup")]
    recorded = latest.get("n_frames_recorded")
    n_results = latest.get("n_results")
    n_dup = latest.get("n_duplicate")
    suspect = bool(latest.get("suspect_duplicate"))
    dup_groups = latest.get("dup_groups")

    has_service = [f for f in frames
                   if f.get("content_sha256") or f.get("same_as_previous") is not None
                   or f.get("duplicate_streak") is not None]
    checks.append(("服务侧实时指纹落在帧上", bool(frames) and len(has_service) == n_frames,
                   f"{len(has_service)}/{n_frames} 帧带指纹字段"
                   + ("" if n_frames else "（这一轮没有帧行）")))

    checks.append(("48 帧全入库", recorded == n_frames,
                   f"n_frames_recorded={recorded} 帧行={n_frames}"))

    expected_ok = n_frames - len(dup)
    checks.append(("n_results = 帧数 - 重复帧数", n_results == expected_ok,
                   f"n_results={n_results} 期望={expected_ok}"))

    checks.append(("n_duplicate = 带 frame_dup 的帧数", n_dup == len(dup),
                   f"n_duplicate={n_dup} frame_dup 帧={len(dup)}"))

    # 实时指纹一致性：同一 sha256 出现在 ≥2 个不同站位 ⇒ 这些帧必须都被标 frame_dup
    by_sha: dict[str, set[str]] = defaultdict(set)
    for f in frames:
        sha = f.get("content_sha256")
        if sha:
            by_sha[str(sha)].add(str(f.get("station_id") or "?"))
    cross_station = {sha for sha, stations in by_sha.items() if len(stations) >= 2}
    flagged = {str(f.get("content_sha256")) for f in dup if f.get("content_sha256")}
    consistent = cross_station == flagged
    checks.append(("重复帧与实时指纹一致", consistent,
                   f"跨站同指纹组={len(cross_station)} 已标 frame_dup 的组={len(flagged)}"))

    if not dup and not cross_station:
        checks.append(("正常轮 suspect_duplicate 不出现", not suspect,
                       f"suspect_duplicate={suspect}"))
    else:
        checks.append(("可疑轮 suspect_duplicate 已置位", suspect and bool(dup_groups),
                       f"suspect_duplicate={suspect} dup_groups={len(dup_groups or [])} 组"))

    # 库层的写读成对（环境官 db.py 的 `_as_text`/`_as_list`）：读回来必须是**列表**，不是 JSON 字符串
    if dup_groups is not None:
        checks.append(("dup_groups 读回来是列表（不是字符串）", isinstance(dup_groups, list),
                       f"类型={type(dup_groups).__name__}"))
    return checks


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="只读校验一轮巡检的内容完整性（票 06/13）")
    ap.add_argument("rows", help="outbox JSONL 路径，或一个 JSON 数组文件")
    ap.add_argument("--round-id", default=None, help="指定轮次（默认取最新一轮）")
    ap.add_argument("--producer", default="", help="本轮生产侧版本/镜像（如 mushroom_patrol:b64027b）")
    ap.add_argument("--consumer", default="", help="analysis 侧版本/镜像（加列的消费者）")
    args = ap.parse_args(argv)

    # 现场是 Linux，但本机（Windows/GBK 控制台）跑时别因为一个非 GBK 字符崩掉：能重配就重配，
    # 重配不了就让不可编码的字符退化成 "?" —— 工具的价值在结论，不在字符保真。
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except (ValueError, OSError):
            pass

    checks = verify(load_rows(args.rows), round_id=args.round_id)
    # 版本先摆出来：**先确认生产跑的是哪一版，再解释库里有没有**。
    print(f"生产版本/部署状态  producer={args.producer or '(未填写)'}  "
          f"consumer={args.consumer or '(未填写)'}")
    width = max(len(name) for name, _, _ in checks)
    for name, ok, detail in checks:
        print(f"{'PASS' if ok else 'FAIL'}  {name.ljust(width)}  {detail}")
    note = classify(checks)
    if note:
        print(f"NOTE  {note}")
    return 0 if all(ok for _, ok, _ in checks) else 1


if __name__ == "__main__":
    sys.exit(main())
