from datetime import date, datetime
from types import SimpleNamespace

import pytest

from utils.create_table import ImageTextQuality
from vision.encoder_storage.text_quality_repository import (
    insert_text_quality_record,
    save_text_quality_only,
)


class FakeQuery:
    def __init__(self, row):
        self.row = row
        self.filters = []

    def filter_by(self, **kwargs):
        self.filters.append(kwargs)
        return self

    def first(self):
        return self.row


class FakeSession:
    def __init__(self, row=None, fail_on_add=False):
        self.row = row
        self.fail_on_add = fail_on_add
        self.added = []
        self.committed = False
        self.rolled_back = False
        self.closed = False
        self.query_obj = FakeQuery(row)

    def query(self, model):
        self.query_model = model
        return self.query_obj

    def add(self, row):
        if self.fail_on_add:
            raise RuntimeError("boom")
        self.added.append(row)

    def commit(self):
        self.committed = True

    def rollback(self):
        self.rolled_back = True

    def close(self):
        self.closed = True


def test_insert_text_quality_record_adds_orm_row():
    session = FakeSession()

    insert_text_quality_record(
        session=session,
        image_path="mogu/611/image.jpg",
        embedding_id=123,
        room_id="1",
        in_date=date(2026, 1, 5),
        collection_datetime=datetime(2026, 1, 5, 12, 0),
        llama_description="Primordia Stage",
        chinese_description="原基期",
        image_quality_score=95.0,
    )

    assert len(session.added) == 1
    row = session.added[0]
    assert isinstance(row, ImageTextQuality)
    assert row.mushroom_embedding_id == 123
    assert row.image_path == "mogu/611/image.jpg"
    assert row.image_quality_score == 95.0


def test_save_text_quality_only_uses_existing_embedding_and_commits():
    session = FakeSession(row=SimpleNamespace(id=456))

    result = save_text_quality_only(
        session_factory=lambda: session,
        image_path="mogu/611/image.jpg",
        room_id="1",
        in_date=date(2026, 1, 5),
        collection_datetime=datetime(2026, 1, 5, 12, 0),
        growth_stage_description="Primordia Stage",
        chinese_description="原基期",
        llama_quality_score=95.0,
    )

    assert result is True
    assert session.query_obj.filters == [{"image_path": "mogu/611/image.jpg"}]
    assert session.added[0].mushroom_embedding_id == 456
    assert session.committed
    assert session.closed
    assert not session.rolled_back


def test_save_text_quality_only_rolls_back_and_reraises_on_error():
    session = FakeSession(fail_on_add=True)

    with pytest.raises(RuntimeError, match="boom"):
        save_text_quality_only(
            session_factory=lambda: session,
            image_path="mogu/611/image.jpg",
            room_id="1",
            in_date=date(2026, 1, 5),
            collection_datetime=datetime(2026, 1, 5, 12, 0),
            growth_stage_description="Primordia Stage",
            chinese_description="原基期",
            llama_quality_score=95.0,
        )

    assert session.rolled_back
    assert session.closed