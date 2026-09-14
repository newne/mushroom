from datetime import datetime


SUPPORTED_DATETIME_FORMATS = (
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%d %H:%M",
    "%Y-%m-%d",
)


def parse_datetime_or_now(
    datetime_str: str | None,
    *,
    error_message: str | None = None,
) -> datetime:
    if datetime_str is None:
        return datetime.now()

    for datetime_format in SUPPORTED_DATETIME_FORMATS:
        try:
            return datetime.strptime(datetime_str, datetime_format)
        except ValueError:
            continue

    if error_message is None:
        error_message = (
            f"Invalid datetime format: {datetime_str}. Expected formats: "
            "'YYYY-MM-DD HH:MM:SS', 'YYYY-MM-DD HH:MM', or 'YYYY-MM-DD'"
        )
    raise ValueError(error_message)