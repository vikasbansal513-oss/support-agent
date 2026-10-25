from datetime import UTC, datetime, time, timedelta
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")


def utc_now() -> datetime:
    return datetime.now(UTC)


def to_ist(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        raise ValueError("naive datetime not allowed")
    return dt.astimezone(IST)


def _local(dt: datetime, tz: str) -> datetime:
    if dt.tzinfo is None:
        raise ValueError("naive datetime not allowed")
    return dt.astimezone(ZoneInfo(tz))


def _minute(value: time) -> time:
    return value.replace(second=0, microsecond=0)


def is_within_hours(
    dt: datetime, start: str, end: str, days: list[int], tz: str = "Asia/Kolkata"
) -> bool:
    local = _local(dt, tz)
    if local.weekday() not in days:
        return False
    return time.fromisoformat(start) <= _minute(local.time()) < time.fromisoformat(end)


def next_open_utc(
    dt: datetime, start: str, end: str, days: list[int], tz: str = "Asia/Kolkata"
) -> datetime | None:
    if not days:
        return None
    zone = ZoneInfo(tz)
    if is_within_hours(dt, start, end, days, tz):
        return dt.astimezone(UTC)
    local = _local(dt, tz)
    opens = time.fromisoformat(start)
    for offset in range(0, 8):
        day = local.date() + timedelta(days=offset)
        if day.weekday() not in days:
            continue
        candidate = datetime.combine(day, opens, tzinfo=zone)
        if candidate > local:
            return candidate.astimezone(UTC)
    return None
