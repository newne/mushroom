from unittest.mock import MagicMock

from scripts.processing.offline_batch_runner import run_offline_batch


def test_run_offline_batch_success(tmp_path, monkeypatch):
    processor = MagicMock()
    add_calls = []
    info_calls = []
    error_calls = []

    monkeypatch.setattr(
        "scripts.processing.offline_batch_runner.logger.add",
        lambda sink: add_calls.append(sink),
    )
    monkeypatch.setattr(
        "scripts.processing.offline_batch_runner.logger.info",
        lambda *args: info_calls.append(args),
    )
    monkeypatch.setattr(
        "scripts.processing.offline_batch_runner.logger.error",
        lambda *args: error_calls.append(args),
    )

    log_file = tmp_path / "logs" / "offline_processing_{time}.log"
    exit_code = run_offline_batch(
        limit_per_room_day=4,
        start_date="2025-01-01",
        end_date="2025-01-31",
        log_file=str(log_file),
        processor_factory=lambda: processor,
    )

    assert exit_code == 0
    assert add_calls == [str(log_file)]
    processor.process_daily_batch.assert_called_once_with(
        limit_per_room_day=4,
        start_date="2025-01-01",
        end_date="2025-01-31",
    )
    assert len(info_calls) == 2
    assert error_calls == []


def test_run_offline_batch_failure(monkeypatch):
    monkeypatch.setattr(
        "scripts.processing.offline_batch_runner.logger.info",
        lambda *args: None,
    )
    error_calls = []
    monkeypatch.setattr(
        "scripts.processing.offline_batch_runner.logger.error",
        lambda *args: error_calls.append(args),
    )

    def _raise_error():
        raise RuntimeError("boom")

    exit_code = run_offline_batch(processor_factory=_raise_error)

    assert exit_code == 1
    assert len(error_calls) == 1