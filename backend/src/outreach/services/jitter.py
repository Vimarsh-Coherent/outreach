import random
from datetime import datetime, timedelta

from outreach.config import get_settings


def add_jitter(dt: datetime) -> datetime:
    """Spread a scheduled send time forward by 0..JITTER_MAX_MS milliseconds.

    Why: when many enrolments hit the same window-open moment (e.g. 09:00 Monday),
    sending them all in the same second causes provider rate-limit spikes. Jittering
    smooths the burst without changing the order.
    """
    max_ms = get_settings().jitter_max_ms
    if max_ms <= 0:
        return dt
    offset_ms = random.randint(0, max_ms)
    return dt + timedelta(milliseconds=offset_ms)
