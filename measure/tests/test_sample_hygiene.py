"""样本卫生与内容完整性排除的单测（票 13-round-content-integrity）。

两件事必须钉住：
1. **标定样本侧**：跨站重复帧 / 已知坏轮 / 内容校验没查成的轮，一律不参与拟合；
2. **产行侧**：带 `frame_dup` / `suspect_duplicate` 的帧不产测量行（默认），
   `emit_empty=True` 时才留一行 `n=0` 的痕迹。
"""

import numpy as np
import pytest

from measure.production import content_integrity_reason, frame_from_meta, rows_from_frames
from measure.sample_hygiene import KNOWN_BAD_ROUNDS, is_excluded, is_known_bad_round

BIG = np.full((64, 64, 3), 200, np.uint8)


# ---------- 样本侧 ----------

def test_known_bad_round_is_excluded():
    """2026-10-09 18:22–18:27 那轮（35 帧同 sha）必须被排除。"""
    assert KNOWN_BAD_ROUNDS, "排除表不能为空"
    assert is_known_bad_round("2026-10-09T18:22:05")
    assert is_known_bad_round("2026-10-09T18:27:16")
    assert is_known_bad_round("2026-10-09T18:21:59") is None
    assert is_known_bad_round("2026-10-09T18:28:01") is None


def test_frame_dup_and_round_flags_excluded():
    assert is_excluded({"ts": "2026-10-13T12:00:00", "frame_dup": True,
                        "dup_group": "41685c09be7b"})
    assert is_excluded({"ts": "2026-10-13T12:00:00", "suspect_duplicate": True})
    assert is_excluded({"ts": "2026-10-13T12:00:00", "content_check_error": True})
    assert is_excluded({"ts": "2026-10-13T12:00:00"}) is None


def test_content_check_error_is_fail_closed():
    """"没查成" ≠ "确认无重复"：样本侧一律排除，理由要写清。"""
    why = is_excluded({"ts": "2026-10-13T12:00:00", "content_check_error": True})
    assert why and "fail-closed" in why


# ---------- 产行侧 ----------

def frame_meta(**kw) -> dict:
    base = {"ts": "2026-10-13T15:00:00", "box_id": "B105", "station_id": "S105",
            "angle_profile": "level0", "cloud_url": "http://x/1.jpg"}
    return {**base, **kw}


def stubs():
    return lambda u: b"<jpeg>", lambda b: BIG


def test_duplicate_frame_produces_no_row_by_default():
    fetch, decode = stubs()
    rows = rows_from_frames([frame_meta(frame_dup=True, dup_group="41685c09be7b")],
                            room_id="611", mm_per_px=0.5, fetch=fetch, decode=decode,
                            log=lambda *_: None)
    assert rows == []


def test_suspect_duplicate_round_produces_no_row():
    fetch, decode = stubs()
    rows = rows_from_frames([frame_meta(suspect_duplicate=True)],
                            room_id="611", mm_per_px=0.5, fetch=fetch, decode=decode,
                            log=lambda *_: None)
    assert rows == []


def test_duplicate_frame_leaves_a_marked_row_when_asked():
    fetch, decode = stubs()
    rows = rows_from_frames([frame_meta(frame_dup=True, dup_group="41685c09be7b")],
                            room_id="611", mm_per_px=0.5, fetch=fetch, decode=decode,
                            emit_empty=True, log=lambda *_: None)
    assert len(rows) == 1
    assert rows[0]["n"] == 0
    assert "no_detection" in rows[0]["quality"] and "low_confidence" in rows[0]["quality"]


def test_content_check_error_does_not_block_measurement():
    """"没查成"不是"图坏了"：生产测量照常做，只是日志里说出来。"""
    fetch, decode = stubs()
    seen: list[str] = []
    rows = rows_from_frames([frame_meta(content_check_error=True)],
                            room_id="611", mm_per_px=0.5, fetch=fetch, decode=decode,
                            log=seen.append)
    assert any("content_check_error" in m for m in seen)
    assert all("frame_dup" not in (r["quality"] or "") for r in rows)


def test_frame_from_meta_reads_integrity_fields():
    f = frame_from_meta(frame_meta(frame_dup=True, dup_group="abc123",
                                   content_sha256="abc123def456"))
    assert f is not None and f.frame_dup and f.dup_group == "abc123"
    assert content_integrity_reason(f).startswith("frame_dup")
    f2 = frame_from_meta(frame_meta())
    assert f2 is not None and content_integrity_reason(f2) == ""
