"""Pure time-slot logic (no database access), so it can be unit-tested in isolation.

All datetimes are timezone-aware. Opening hours are local wall-clock times in the
app timezone; a turf that closes at or before its opening time closes after midnight
(e.g. 06:00-01:00).
"""

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, tzinfo

from app.services.errors import RuleViolation


@dataclass(frozen=True)
class Window:
    start: datetime
    end: datetime


def opening_window(day: date, opening: time, closing: time, tz: tzinfo) -> Window:
    start = datetime.combine(day, opening, tzinfo=tz)
    end = datetime.combine(day, closing, tzinfo=tz)
    if end <= start:
        end += timedelta(days=1)
    return Window(start, end)


def resolve_start(day: date, start_time: time, window: Window, tz: tzinfo) -> datetime:
    """Map a wall-clock start time on a booking day to an absolute datetime.

    For turfs open past midnight, a time like 00:30 on day D means the early hours of D+1.
    """
    start = datetime.combine(day, start_time, tzinfo=tz)
    if start < window.start and window.end.date() > day:
        start += timedelta(days=1)
    return start


def validate_slot(
    start_at: datetime,
    duration: timedelta,
    window: Window,
    *,
    step_minutes: int,
    now: datetime,
    max_days_ahead: int,
) -> datetime:
    """Check a requested slot against opening hours and booking rules. Returns end_at."""
    end_at = start_at + duration
    if (start_at - window.start) % timedelta(minutes=step_minutes) != timedelta(0):
        raise RuleViolation(f"Start time must be on a {step_minutes}-minute boundary")
    if start_at < window.start or end_at > window.end:
        raise RuleViolation(
            f"The turf is open {window.start:%H:%M}-{window.end:%H:%M}; the booking must fit inside opening hours"
        )
    if start_at <= now:
        raise RuleViolation("The start time must be in the future")
    if start_at > now + timedelta(days=max_days_ahead):
        raise RuleViolation(f"Bookings can be made at most {max_days_ahead} days in advance")
    return end_at


def overlaps(a_start: datetime, a_end: datetime, b_start: datetime, b_end: datetime) -> bool:
    """Half-open interval overlap: back-to-back slots do not overlap."""
    return a_start < b_end and a_end > b_start


def available_starts(
    window: Window,
    duration: timedelta,
    busy: list[tuple[datetime, datetime]],
    *,
    step_minutes: int,
    now: datetime,
) -> list[datetime]:
    step = timedelta(minutes=step_minutes)
    starts: list[datetime] = []
    current = window.start
    while current + duration <= window.end:
        end = current + duration
        if current > now and not any(overlaps(current, end, b_start, b_end) for b_start, b_end in busy):
            starts.append(current)
        current += step
    return starts
