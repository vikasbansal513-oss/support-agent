from datetime import UTC, date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from app.core import timeutil

_LOOKAHEAD_DAYS = 400


def _extras(policy: Any) -> tuple[set[date], dict[str, Any]]:
    holidays = {date.fromisoformat(d) for d in (getattr(policy, "holidays", None) or [])}
    return holidays, dict(getattr(policy, "day_hours", None) or {})


def _window(policy: Any, day_hours: dict[str, Any], weekday: int) -> tuple[str, str]:
    override = day_hours.get(str(weekday))
    if override is not None:
        return override.start, override.end
    hours = policy.office_hours
    return hours.start, hours.end


def is_open(policy: Any, dt: datetime, tz: str) -> bool:
    holidays, day_hours = _extras(policy)
    hours = policy.office_hours
    if not holidays and not day_hours:
        return timeutil.is_within_hours(dt, hours.start, hours.end, hours.days, tz)
    local = dt.astimezone(ZoneInfo(tz))
    if local.date() in holidays or local.weekday() not in hours.days:
        return False
    start, end = _window(policy, day_hours, local.weekday())
    now = local.time().replace(second=0, microsecond=0)
    return time.fromisoformat(start) <= now < time.fromisoformat(end)


def next_open(policy: Any, dt: datetime, tz: str) -> datetime | None:
    holidays, day_hours = _extras(policy)
    hours = policy.office_hours
    if not holidays and not day_hours:
        return timeutil.next_open_utc(dt, hours.start, hours.end, hours.days, tz)
    if not hours.days:
        return None
    if is_open(policy, dt, tz):
        return dt.astimezone(UTC)
    zone = ZoneInfo(tz)
    local = dt.astimezone(zone)
    for offset in range(_LOOKAHEAD_DAYS):
        day = local.date() + timedelta(days=offset)
        if day in holidays or day.weekday() not in hours.days:
            continue
        start, _ = _window(policy, day_hours, day.weekday())
        candidate = datetime.combine(day, time.fromisoformat(start), tzinfo=zone)
        if candidate > local:
            return candidate.astimezone(UTC)
    return None
