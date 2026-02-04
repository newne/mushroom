from datetime import datetime

DATETIME_FORMAT = "%Y-%m-%d %H:%M:%S"


def format_datetime(value: datetime | None) -> str | None:
    if not value:
        return None
    return value.strftime(DATETIME_FORMAT)


def parse_datetime(value: str) -> datetime:
    return datetime.strptime(value, DATETIME_FORMAT)
