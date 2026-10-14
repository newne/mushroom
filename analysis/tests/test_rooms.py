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
    """"999" 未确证 ⇒ **None**。返回 "999" 会让下游以为它是规范号、join 又静默落空。"""
    empty: dict[str, str] = {}
    assert canonical_room("999", alias=empty) is None
    assert canonical_room("607", alias=empty) == "607", "本来就是规范号 ⇒ 原样"
    assert canonical_room("611", alias=empty) == "611"
    assert canonical_room("", alias=empty) is None and canonical_room(None, alias=empty) is None


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


def test_default_paths_do_not_depend_on_the_working_directory():
    """两份数据文件的默认路径按**包位置**解析。

    踩过的坑：写成相对路径时，换个 cwd 就"文件不存在 ⇒ 一律解不出"，而那个失败
    长得跟"还没确证/还没生成映射"一模一样 —— 静默降级成空表，正好是最难发现的那种。
    """
    from pathlib import Path

    from analysis.batches import DEFAULT_MAP_PATH
    from analysis.rooms import DEFAULT_ALIAS_PATH

    for path in (DEFAULT_ALIAS_PATH, DEFAULT_MAP_PATH):
        assert Path(path).is_absolute(), path
        assert Path(path).exists(), path


def test_resolve_rooms_counts_and_samples_unresolved(capsys):
    """口径 2：解不出 ⇒ NULL + 计数 + 样例（哪几条、原始值是什么）。"""
    rows, report = resolve_rooms(
        [{"room_id": "611", "entry_date": "2026-09-30"},
         {"room_id": "999", "entry_date": "2026-10-10"},
         {"room_id": "999", "entry_date": "2026-08-16"},
         {"room_id": "998", "entry_date": "2026-09-27"}],
        alias={},                       # 空表：专测"未登记"这条路径
        label="/environment/batches",
    )
    assert [r["room_id_canonical"] for r in rows] == ["611", None, None, None]
    assert [r["room_id"] for r in rows] == ["611", "999", "999", "998"], "原始列不许改"
    assert report["n_total"] == 4 and report["n_resolved"] == 1 and report["n_unresolved"] == 3
    assert report["unresolved_values"] == {"999": 2, "998": 1}
    assert report["unresolved_samples"][0] == {"index": 1, "raw": "999"}
    printed = capsys.readouterr().out
    assert "/environment/batches" in printed and "'999'×2" in printed


def test_display_room_never_launders_the_original_code(tmp_path):
    """口径 3：页面拿到的字符串必须能看出"这原本是 7"。"""
    alias = {"7": {"to": "607", "evidence": "现场对照表"}}
    table = load_room_alias(_write_alias(tmp_path, alias))
    assert display_room("611", alias=table) == "611"
    assert display_room("7", alias=table) == "607（原始 7）"
    assert display_room("7", alias={}) == "7（原始编号，未登记别名）"


def test_shipped_alias_file_is_confirmed_with_evidence():
    """仓库里那份别名表：`7→607` / `8→608` 已**确证**（发版官 2026-10-14 日期集合包含 + 排它）。

    钉住三件事：① 每条都有 `evidence`（"不许猜"由结构保证）；
    ② 两个历史编码都在；③ 备注里写清"这是旧 MySQL 通路遗留、timer 已停"（免得下一个人
    以为新口径在用 1 位编号）。
    """
    path = Path(__file__).resolve().parents[2] / "analysis/data/room_alias.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["generated_at"] and data["source"]
    assert data["map"], "确证后就不再是空表了"
    for src, entry in data["map"].items():
        assert entry.get("to"), f"{src} 没有 to"
        assert entry.get("evidence"), f"{src} 没有 evidence —— 未确证的对应不许生效"
        assert entry.get("confirmed_by"), f"{src} 没有写谁确证的"
    assert {src: e["to"] for src, e in data["map"].items()} == {"7": "607", "8": "608"}
    assert "timer" in json.dumps(data, ensure_ascii=False), "要写清历史编码的来源与已停用"
    assert load_room_alias(str(path)) == {"7": "607", "8": "608"}


def test_shipped_alias_resolves_the_legacy_codes_by_default():
    """默认（不传 alias）就该解出 `7`/`8` —— 否则 `/environment/batches?zone=607` 还是空手而归。"""
    assert canonical_room("7") == "607"
    assert canonical_room("8") == "608"
    assert display_room("7") == "607（原始 7）"
