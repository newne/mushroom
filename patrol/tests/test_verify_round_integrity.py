"""复验工具（票 06/13）：正常轮全绿；事故形状轮标出"可疑且账对得上"。"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

from verify_round_integrity import classify, main, verify  # noqa: E402  (路径在上一行注入)


def _frame(i: int, *, sha: str, dup: bool = False, same: bool = False, streak: int = 0) -> dict:
    row = {"kind": "image_index", "round_id": "r1", "ts": f"2026-10-13T21:27:{i:02d}",
           "station_id": f"S{i:03d}", "box_id": f"B{i:03d}", "ok": True,
           "object_name": f"x/B{i:03d}_S{i:03d}_level0_2127{i:02d}.jpg",
           "content_sha256": sha, "same_as_previous": same, "duplicate_streak": streak}
    if dup:
        row["frame_dup"] = True
        row["dup_group"] = sha[:12]
    return row


def _round(**kw) -> dict:
    row = {"kind": "round", "round_id": "r1", "ts": "2026-10-13T21:35:00", "ok": True,
           "n_results": kw.pop("n_results", 48), "n_failures": 0, "aborted": False,
           "n_frames_recorded": kw.pop("n_frames_recorded", 48),
           "n_duplicate": kw.pop("n_duplicate", 0)}
    row.update(kw)
    return row


def test_normal_round_passes_every_check(tmp_path):
    rows = [_round()] + [_frame(i, sha=f"{i:064d}", streak=1) for i in range(1, 49)]
    checks = verify(rows)
    assert all(ok for _, ok, _ in checks), checks
    assert any("正常轮" in name for name, _, _ in checks)


def test_incident_shape_round_is_flagged_but_the_books_balance(tmp_path):
    """前 13 帧各不相同、之后 35 帧同一份：账要对得上，且必须标成可疑。"""
    rows = [_round(n_results=13, n_duplicate=35, suspect_duplicate=True,
                   dup_groups=[{"sha256_12": "a" * 12, "n": 35, "stations": ["S211", "S401"]}])]
    rows += [_frame(i, sha=f"{i:064d}", streak=1) for i in range(1, 14)]
    rows += [_frame(i, sha="a" * 64, dup=True, same=True, streak=i - 13) for i in range(14, 49)]

    checks = verify(rows)
    assert all(ok for _, ok, _ in checks), checks
    names = [name for name, _, _ in checks]
    assert "可疑轮 suspect_duplicate 已置位" in names


def test_accepts_rows_pulled_from_the_db_without_kind():
    """库层的行没有 `kind`（`/rounds`+`/images` 直接拉回来）⇒ 也要能认出层与轮次。

    并且 `dup_groups` 必须是**列表**（环境官 `_as_text`/`_as_list` 的写读成对），
    读回来是 JSON 字符串就该 FAIL。
    """
    round_row = {"round_id": "r1", "ts": "2026-10-13T21:35:00", "ok": 1, "n_results": 13,
                 "n_failures": 0, "aborted": 0, "n_frames_recorded": 48, "n_duplicate": 35,
                 "suspect_duplicate": 1,
                 "dup_groups": [{"sha256_12": "a" * 12, "n": 35, "stations": ["S211", "S401"]}]}
    frames = [{"round_id": "r1", "ts": f"2026-10-13T21:28:{i:02d}", "station_id": f"S{i:03d}",
               "content_sha256": "a" * 64, "frame_dup": 1, "dup_group": "a" * 12,
               "duplicate_streak": i - 13, "same_as_previous": 1} for i in range(14, 49)]
    frames += [{"round_id": "r1", "ts": f"2026-10-13T21:27:{i:02d}", "station_id": f"S{i:03d}",
                "content_sha256": f"{i:064d}", "frame_dup": 0, "duplicate_streak": 1,
                "same_as_previous": 0} for i in range(1, 14)]

    checks = dict((name, ok) for name, ok, _ in verify([round_row, *frames]))
    assert checks["n_results = 帧数 - 重复帧数"] is True
    assert checks["dup_groups 读回来是列表（不是字符串）"] is True
    assert checks["可疑轮 suspect_duplicate 已置位"] is True

    # 读回来变成 JSON 字符串 ⇒ 明确 FAIL（写读没成对时就是这个症状）
    round_row["dup_groups"] = json.dumps(round_row["dup_groups"], ensure_ascii=False)
    checks = dict((name, ok) for name, ok, _ in verify([round_row, *frames]))
    assert checks["dup_groups 读回来是列表（不是字符串）"] is False


def test_old_version_output_is_classified_as_not_deployed():
    """生产跑旧版（帧上没有服务侧指纹）⇒ 报告必须标"未部署/版本不符"，不是"采图坏了"。"""
    old_frames = [{"kind": "image_index", "round_id": "r1", "ts": f"2026-10-13T21:27:{i:02d}",
                   "station_id": f"S{i:03d}", "object_name": f"x/{i}.jpg"} for i in range(1, 4)]
    old_round = {"kind": "round", "round_id": "r1", "ts": "2026-10-13T21:35:00",
                 "ok": True, "n_results": 3, "n_failures": 0, "aborted": False}

    checks = verify([old_round, *old_frames])
    assert dict((n, ok) for n, ok, _ in checks)["服务侧实时指纹落在帧上"] is False
    assert "未部署" in classify(checks)
    assert classify([("服务侧实时指纹落在帧上", True, ""), ("48 帧全入库", True, "")]) == ""


def test_wrong_counters_fail_loudly():
    """账错了必须 FAIL（比如 `n_results` 还按 48 报）。"""
    rows = [_round(n_results=48, n_duplicate=35)] + [
        _frame(i, sha="a" * 64, dup=True) for i in range(1, 49)]
    checks = dict((name, ok) for name, ok, _ in verify(rows))
    assert checks["n_results = 帧数 - 重复帧数"] is False
    assert checks["48 帧全入库"] is True


def test_cli_reads_jsonl_and_reports_failure(tmp_path):
    path = tmp_path / "rows.jsonl"
    path.write_text("\n".join(json.dumps(r) for r in
                              [_round(n_results=99)] + [_frame(i, sha="a" * 64)
                                                        for i in range(1, 49)]),
                    encoding="utf-8")
    assert main([str(path)]) == 1          # 账不对 ⇒ 非零退出
    path2 = tmp_path / "ok.jsonl"
    path2.write_text("\n".join(json.dumps(r) for r in
                               [_round()] + [_frame(i, sha=f"{i:064d}") for i in range(1, 49)]),
                     encoding="utf-8")
    assert main([str(path2)]) == 0
