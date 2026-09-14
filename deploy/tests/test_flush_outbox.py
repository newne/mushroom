"""``deploy.flush_outbox``：补传累积 outbox，成功即归档。

核心契约：**推成功才动文件**。传输失败时原文件必须一个字节都不变（否则数据就真丢了），
成功时归档而不是删除（补传是一次性动作，之后没人能复现"送出去了什么"）。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from deploy.flush_outbox import (
    archive_path,
    describe,
    flush_file,
    main,
    make_transport,
)
from patrol.store import JsonlStore

ROWS = [
    {"kind": "round", "ts": "2026-09-13T20:35:15", "ok": False, "n_results": 59,
     "room_id": "611", "entry_date": "2026-03-16"},
    {"kind": "image_index", "ts": "2026-09-13T20:25:42", "ok": True, "box_id": "B101",
     "station_id": "S101", "object_name": "20260913/B101.jpg"},
    {"kind": "image_index", "ts": "2026-09-13T20:26:02", "ok": True, "box_id": "B102",
     "station_id": "S102", "object_name": "20260913/B102.jpg"},
]


class FakeTransport:
    """记下每一次调用；可按需抛错。patrol.links.Transport 形状。"""

    def __init__(self, *, fail: bool = False):
        self.calls: list[tuple[str, dict]] = []
        self.fail = fail

    def __call__(self, url: str, *, params=None, body=None) -> dict:
        self.calls.append((url, body or {}))
        if self.fail:
            raise ConnectionError("prod 不在")
        return {"inserted_images": 2}


def write_outbox(path: Path, rows=ROWS) -> None:
    path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows),
                    encoding="utf-8")


def test_describe_counts_kinds(tmp_path: Path) -> None:
    p = tmp_path / "outbox.jsonl"
    write_outbox(p)
    rows, kinds = describe(JsonlStore(str(p)))
    assert rows == 3
    assert kinds == {"round": 1, "image_index": 2}


def test_flush_pushes_then_archives(tmp_path: Path) -> None:
    p = tmp_path / "outbox.jsonl"
    write_outbox(p)
    t = FakeTransport()

    r = flush_file(p, endpoint="http://10.77.77.39:8000/ingest", transport=t)

    assert r.rows == 3 and r.sent is True
    assert r.kinds == {"round": 1, "image_index": 2}
    assert len(t.calls) == 1
    url, body = t.calls[0]
    assert url == "http://10.77.77.39:8000/ingest"
    assert [row["kind"] for row in body["rows"]] == ["round", "image_index", "image_index"]

    # 原文件已清空（等待下一轮追加），归档里仍是完整的三行
    assert p.read_text(encoding="utf-8") == ""
    archived = r.archived_to
    assert archived is not None and archived == tmp_path / "sent" / "outbox.jsonl"
    assert len(archived.read_text(encoding="utf-8").splitlines()) == 3


def test_flush_failure_leaves_file_untouched(tmp_path: Path) -> None:
    p = tmp_path / "outbox.jsonl"
    write_outbox(p)
    before = p.read_text(encoding="utf-8")
    t = FakeTransport(fail=True)

    with pytest.raises(ConnectionError):
        flush_file(p, endpoint="http://x/ingest", transport=t)

    assert p.read_text(encoding="utf-8") == before      # 一个字节都没变
    assert not (tmp_path / "sent").exists()             # 也没归档


def test_flush_empty_file_is_not_an_error(tmp_path: Path) -> None:
    p = tmp_path / "outbox.jsonl"
    p.write_text("", encoding="utf-8")
    t = FakeTransport()
    r = flush_file(p, endpoint="http://x/ingest", transport=t)
    assert r.rows == 0 and r.sent is False
    assert t.calls == []
    assert p.exists()                                   # 空文件原地不动


def test_flush_tolerates_corrupt_line(tmp_path: Path) -> None:
    """撕裂的末行不该让整个 outbox 卡死（store 层的自愈）。"""
    p = tmp_path / "outbox.jsonl"
    write_outbox(p)
    with p.open("a", encoding="utf-8") as fh:
        fh.write('{"kind": "image_index", "ts": "2026-09-1')   # 断电撕裂缝
    t = FakeTransport()

    r = flush_file(p, endpoint="http://x/ingest", transport=t)

    assert r.rows == 3
    assert len(t.calls[0][1]["rows"]) == 3


def test_flush_can_skip_archive(tmp_path: Path) -> None:
    p = tmp_path / "outbox.jsonl"
    write_outbox(p)
    r = flush_file(p, endpoint="http://x/ingest", transport=FakeTransport(),
                   do_archive=False)
    assert r.sent is True and r.archived_to is None
    assert p.read_text(encoding="utf-8") == ""
    assert not (tmp_path / "sent").exists()


def test_archive_never_overwrites(tmp_path: Path) -> None:
    p = tmp_path / "outbox.jsonl"
    write_outbox(p)
    write_outbox(p)
    first = flush_file(p, endpoint="http://x/ingest", transport=FakeTransport())
    write_outbox(p, ROWS[:1])
    second = flush_file(p, endpoint="http://x/ingest", transport=FakeTransport())

    assert first.archived_to == tmp_path / "sent" / "outbox.jsonl"
    assert second.archived_to == tmp_path / "sent" / "outbox.jsonl-2"
    assert len(first.archived_to.read_text(encoding="utf-8").splitlines()) == 3
    assert len(second.archived_to.read_text(encoding="utf-8").splitlines()) == 1


def test_archive_path_respects_explicit_dir(tmp_path: Path) -> None:
    p = tmp_path / "outbox.jsonl"
    dst = archive_path(p, tmp_path / "elsewhere")
    assert dst == tmp_path / "elsewhere" / "outbox.jsonl"


def test_main_dry_run_sends_nothing(tmp_path: Path, capsys, monkeypatch) -> None:
    p = tmp_path / "outbox.jsonl"
    write_outbox(p)
    before = p.read_text(encoding="utf-8")

    def boom(*a, **k):      # dry-run 期间绝不该建传输
        raise AssertionError("dry-run 不该构造传输")

    monkeypatch.setattr("deploy.flush_outbox.make_transport", boom)
    rc = main(["--outbox", str(p), "--dry-run"])

    assert rc == 0
    out = capsys.readouterr().out
    assert "3 行待传" in out and "未发送任何请求" in out
    assert p.read_text(encoding="utf-8") == before


def test_main_batch_and_cli_archive(tmp_path: Path, capsys, monkeypatch) -> None:
    a = tmp_path / "outbox.jsonl"
    b = tmp_path / "outbox_run2.jsonl"
    write_outbox(a)
    write_outbox(b, ROWS[:1])
    t = FakeTransport()
    monkeypatch.setattr("deploy.flush_outbox.make_transport", lambda *a, **k: t)

    rc = main(["--outbox", str(a), "--outbox", str(b),
               "--ingest", "http://10.77.77.39:8000/ingest"])

    assert rc == 0
    assert len(t.calls) == 2
    out = capsys.readouterr().out
    assert "已补传 3 行" in out and "已补传 1 行" in out
    assert "补传完成：4 行，0 个文件失败" in out
    assert (tmp_path / "sent" / "outbox.jsonl").exists()
    assert (tmp_path / "sent" / "outbox_run2.jsonl").exists()


def test_main_missing_file_is_skipped(tmp_path: Path, capsys, monkeypatch) -> None:
    monkeypatch.setattr("deploy.flush_outbox.make_transport", lambda *a, **k: FakeTransport())
    rc = main(["--outbox", str(tmp_path / "nope.jsonl")])
    assert rc == 0                                  # 归档后重跑同一命令不该算失败
    assert "不存在，跳过" in capsys.readouterr().out


def test_main_reports_failure_and_keeps_file(tmp_path: Path, capsys, monkeypatch) -> None:
    p = tmp_path / "outbox.jsonl"
    write_outbox(p)
    before = p.read_text(encoding="utf-8")
    monkeypatch.setattr("deploy.flush_outbox.make_transport",
                        lambda *a, **k: FakeTransport(fail=True))

    rc = main(["--outbox", str(p)])

    assert rc == 2
    err = capsys.readouterr().err
    assert "补传失败，文件保持原样" in err
    assert p.read_text(encoding="utf-8") == before


def test_make_transport_requires_httpx(monkeypatch) -> None:
    import builtins

    real_import = builtins.__import__

    def fake_import(name, *a, **k):
        if name.startswith("httpx") or name == "deploy.transport":
            raise ImportError("no httpx")
        return real_import(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    with pytest.raises(ImportError):
        make_transport("http://x/ingest", attempts=1)
