from datetime import datetime

import pytest

from scripts.common.datetime_parser import parse_datetime_or_now


def test_parse_datetime_or_now_supports_full_timestamp():
    parsed = parse_datetime_or_now("2026-01-05 12:11:30")

    assert parsed == datetime(2026, 1, 5, 12, 11, 30)


def test_parse_datetime_or_now_supports_minute_precision():
    parsed = parse_datetime_or_now("2026-01-05 12:11")

    assert parsed == datetime(2026, 1, 5, 12, 11)


def test_parse_datetime_or_now_supports_date_only():
    parsed = parse_datetime_or_now("2026-01-05")

    assert parsed == datetime(2026, 1, 5)


def test_parse_datetime_or_now_raises_custom_error_message():
    with pytest.raises(ValueError, match="无效的日期时间格式"):
        parse_datetime_or_now(
            "2026/01/05",
            error_message="无效的日期时间格式: 2026/01/05",
        )