from datetime import datetime, time, timedelta, timezone

from zoneinfo import ZoneInfo

DAY_FLAGS = (1, 2, 4, 8, 16, 32, 64)  # Mon..Sun


def _day_allowed(local_dt: datetime, mask: int) -> bool:
    return bool(mask & DAY_FLAGS[local_dt.weekday()])


def _at(local_dt: datetime, t: time) -> datetime:
    return local_dt.replace(hour=t.hour, minute=t.minute, second=t.second, microsecond=0)


def _advance_to_window(local_dt: datetime, window_start: time, window_end: time, mask: int) -> datetime:
    """Push `local_dt` forward to the next moment inside an allowed weekday + business window.

    Behaviour:
      - If on an allowed day AND time is before window_start, snap to window_start.
      - If on an allowed day AND time is inside [window_start, window_end), return as-is.
      - Otherwise advance to the next allowed day's window_start.
    """
    for _ in range(14):  # safety bound — at most 7 days to find a valid weekday
        if _day_allowed(local_dt, mask):
            start = _at(local_dt, window_start)
            end = _at(local_dt, window_end)
            if local_dt < start:
                return start
            if local_dt < end:
                return local_dt
        # Move to start of next day
        local_dt = (local_dt + timedelta(days=1)).replace(
            hour=window_start.hour, minute=window_start.minute, second=0, microsecond=0
        )
    raise RuntimeError(f"no allowed day found within 14 iterations (mask={mask})")


def next_valid_slot(
    *,
    base: datetime,
    delay_days: int,
    delay_hours: int,
    tz_name: str,
    window_start: time,
    window_end: time,
    days_mask: int,
) -> datetime:
    """Compute the next valid UTC send time for a step.

    Args:
        base: UTC anchor (e.g. the previous step's `sent_at`, or NOW() for the first step).
        delay_days / delay_hours: per-step delay relative to `base`.
        tz_name: IANA timezone for the sequence (e.g. 'Asia/Kolkata').
        window_start / window_end: local business hours.
        days_mask: bitmap Mon=1, Tue=2, ..., Sun=64.

    Returns:
        Aware UTC datetime, snapped into the next allowed window.
    """
    if base.tzinfo is None:
        base = base.replace(tzinfo=timezone.utc)
    tz = ZoneInfo(tz_name)
    local = base.astimezone(tz) + timedelta(days=delay_days, hours=delay_hours)
    snapped = _advance_to_window(local, window_start, window_end, days_mask)
    return snapped.astimezone(timezone.utc)
