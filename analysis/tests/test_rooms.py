"""票 13 §3.3（Chief 2026-10-14 裁定）：库房号别名表 —— 显式、可见、绝不猜。

要钉住四件事：
1. 别名只从**文件**读，**没有 `evidence` 的条目不生效**（"不许猜"由结构保证，不靠自觉）；
2. 解不出的编号 → `room_id_canonical = None` + 计数 + 样例（NULL，不是原文、更不是默认值）；
3. 显示层不洗白（`607（原始 7）` / `7（原始编号，未登记别名）`）；
4. **代码里没有第二份映射** —— 仓库里那份别名表的 `map` 只有确证条目（当前为空）。
"""

from __future__ import annotations

import json
from pathlib import Path

from analysis.rooms import (
    canonical_room,
    canonical_rooms,
    display_room,
    load_room_alias,
    resolve_rooms,
)


def _write_alias(tmp_path, mapping: dict) -> str:
    path = tmp_path / "room_alias.json"
    path.write_text(json.dumps({"map": mapping}, ensure_ascii=False), encoding="utf-8")
    return str(path)


def test_canonical_rooms_come_from_the_single_registry():
    """规范集合取自 `env.tdengine.ROOM_DEVICE_CODES` 的**键**（值是设备前缀），不另抄一份。"""
    from analysis.env.tdengine import ROOM_DEVICE_CODES

    assert set(canonical_rooms()) == set(ROOM_DEVICE_CODES)
    assert canonical_rooms() == ("607", "608", "611", "612")


def test_unregistered_legacy_id_resolves_to_none_not_to_itself():
    """"7" 未确证 ⇒ **None**。返回 "7" 会让下游以为它是规范号、join 又静默落空。"""
    assert canonical_room("7") is None
    assert canonical_room("8") is None
    assert canonical_room("607") == "607", "本来就是规范号 ⇒ 原样"
    assert canonical_room("611") == "611"
    assert canonical_room("") is None and canonical_room(None) is None


def test_confirmed_alias_applies(tmp_path):
    path = _write_alias(tmp_path, {"7": {"to": "607", "evidence": "现场对照表 2026-10-15"}})
    alias = load_room_alias(path)
    assert alias == {"7": "607"}
    assert canonical_room("7", alias=alias) == "607"
    assert canonical_room("607", alias=alias) == "607"


def test_entry_without_evidence_is_skipped(tmp_path, capsys):
    """没证据的对应**不许生效** —— 这是"不许猜"的机制，不是提醒。"""
    path = _write_alias(tmp_path, {
        "7": "607",                                       # 旧写法：无证据
        "8": {"to": "608"},                               # 有 to 没 evidence
        "9": {"to": "611", "evidence": "现场对照表"},       # 唯一合规的一条
    })
    alias = load_room_alias(path)
    assert alias == {"9": "611"}
    assert canonical_room("7", alias=alias) is None
    assert canonical_room("8", alias=alias) is None
    printed = capsys.readouterr().out
    assert "没有 evidence" in printed


def test_alias_target_must_be_a_canonical_room(tmp_path, capsys):
    """挡住"再引入第三种编码"：别名目标不是规范库房号就不生效。"""
    path = _write_alias(tmp_path, {"7": {"to": "999", "evidence": "随手写的"}})
    assert load_room_alias(path) == {}
    assert canonical_room("7", alias={}) is None
    assert "不是规范库房号" in capsys.readouterr().out


def test_missing_alias_file_is_loud_not_silent(tmp_path, capsys):
    assert load_room_alias(str(tmp_path / "nope.json")) == {}
    assert "没有别名表" in capsys.readouterr().out


def test_resolve_rooms_counts_and_samples_unresolved(capsys):
    """口径 2：解不出 ⇒ NULL + 计数 + 样例（哪几条、原始值是什么）。"""
    rows, report = resolve_rooms(
        [{"room_id": "611", "entry_date": "2026-09-30"},
         {"room_id": "7", "entry_date": "2026-10-10"},
         {"room_id": "7", "entry_date": "2026-08-16"},
         {"room_id": "8", "entry_date": "2026-09-27"}],
        label="/environment/batches",
    )
    assert [r["room_id_canonical"] for r in rows] == ["611", None, None, None]
    assert [r["room_id"] for r in rows] == ["611", "7", "7", "8"], "原始列不许改"
    assert report["n_total"] == 4 and report["n_resolved"] == 1 and report["n_unresolved"] == 3
    assert report["unresolved_values"] == {"7": 2, "8": 1}
    assert report["unresolved_samples"][0] == {"index": 1, "raw": "7"}
    printed = capsys.readouterr().out
    assert "/environment/batches" in printed and "'7'×2" in printed


def test_display_room_never_launders_the_original_code(tmp_path):
    """口径 3：页面拿到的字符串必须能看出"这原本是 7"。"""
    alias = {"7": {"to": "607", "evidence": "现场对照表"}}
    table = load_room_alias(_write_alias(tmp_path, alias))
    assert display_room("611", alias=table) == "611"
    assert display_room("7", alias=table) == "607（原始 7）"
    assert display_room("7", alias={}) == "7（原始编号，未登记别名）"


def test_shipped_alias_file_has_no_guessed_entries():
    """仓库里那份别名表：**未确证之前 map 必须是空的**（7/8 只出现在 unconfirmed 里）。

    确证后这条会红 —— 那时把新条目（带 evidence）加进 map、并同步改这条断言即可，
    这是有意为之的"改口径要过一下手"。
    """
    path = Path(__file__).resolve().parents[2] / "analysis/data/room_alias.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["map"] == {}, "未确证的对应不许写进 map"
    assert data["generated_at"] and data["source"]
    by_from = {item["from"]: item for item in data["unconfirmed"]}
    assert set(by_from) == {"7", "8"}, "未确证的候选要留痕，不能只字不提"
    assert all(item["blocker"] for item in data["unconfirmed"]), "每条都要写清卡在哪"
    assert load_room_alias(str(path)) == {}
