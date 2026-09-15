from datetime import datetime

from storage.repositories import decision_analysis_repository
from utils import create_table


def test_create_table_store_results_delegates_to_repository(monkeypatch):
    calls = []

    def fake_store(**kwargs):
        calls.append(kwargs)
        return {"batch_id": kwargs["batch_id"]}

    monkeypatch.setattr(
        decision_analysis_repository,
        "store_decision_analysis_results",
        fake_store,
    )

    analysis_time = datetime(2026, 1, 5, 12, 11, 30)
    result = create_table.store_decision_analysis_results(
        json_data={"ok": True},
        room_id="611",
        analysis_time=analysis_time,
        batch_id="batch-1",
    )

    assert result == {"batch_id": "batch-1"}
    assert calls == [
        {
            "json_data": {"ok": True},
            "room_id": "611",
            "analysis_time": analysis_time,
            "batch_id": "batch-1",
        }
    ]


def test_create_table_query_dynamic_results_delegates_to_repository(monkeypatch):
    calls = []

    def fake_query(**kwargs):
        calls.append(kwargs)
        return ["row"]

    monkeypatch.setattr(
        decision_analysis_repository,
        "query_decision_analysis_dynamic_results",
        fake_query,
    )

    result = create_table.query_decision_analysis_dynamic_results(
        room_id="611",
        batch_id="batch-1",
        device_alias="air_cooler_611",
        point_alias="temp_set",
        change_only=True,
        limit=10,
    )

    assert result == ["row"]
    assert calls == [
        {
            "room_id": "611",
            "batch_id": "batch-1",
            "device_alias": "air_cooler_611",
            "point_alias": "temp_set",
            "change_only": True,
            "start_time": None,
            "end_time": None,
            "limit": 10,
        }
    ]