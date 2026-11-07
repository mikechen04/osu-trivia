"""All "what day is it" logic lives here. The game day is the UTC date; it resets at 00:00 UTC."""

from datetime import date, datetime, time, timedelta, timezone as dt_timezone

from django.utils import timezone


def now() -> datetime:
    return timezone.now()


def today(at: datetime | None = None) -> date:
    return (at or now()).astimezone(dt_timezone.utc).date()


def next_reset(at: datetime | None = None) -> datetime:
    tomorrow = today(at) + timedelta(days=1)
    return datetime.combine(tomorrow, time.min, tzinfo=dt_timezone.utc)


def week_bounds(day: date) -> tuple[date, date]:
    """Monday..Sunday (UTC) containing `day`."""
    start = day - timedelta(days=day.weekday())
    return start, start + timedelta(days=6)
