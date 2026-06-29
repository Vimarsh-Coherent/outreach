"""NTP-corrected UTC clock.

At startup we query pool.ntp.org once to compute the offset between the
system clock and true UTC. Every call to utcnow() returns
datetime.now(UTC) + _offset, so scheduling and timestamps are accurate
even when the host PC clock is drifted.

If NTP is unreachable we fall back to the system clock (offset = 0) and
log a warning — nothing breaks, it just uses local time.
"""
from __future__ import annotations

import logging
import socket
import struct
from datetime import datetime, timedelta, timezone

log = logging.getLogger("outreach.time_util")

_offset: timedelta = timedelta(0)
_synced: bool = False


def _fetch_ntp_utc(host: str = "pool.ntp.org", timeout: float = 3.0) -> datetime:
    """Return current UTC from an NTP server using only stdlib socket."""
    client = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    client.settimeout(timeout)
    try:
        client.sendto(b"\x1b" + 47 * b"\0", (host, 123))
        data, _ = client.recvfrom(1024)
    finally:
        client.close()
    # Word 10 (0-indexed) is Transmit Timestamp seconds since 1900-01-01
    ntp_seconds = struct.unpack("!12I", data)[10]
    unix_seconds = ntp_seconds - 2208988800  # NTP epoch → Unix epoch
    return datetime.fromtimestamp(unix_seconds, tz=timezone.utc)


def sync_clock() -> timedelta:
    """Compute and cache the offset between system clock and NTP time.
    Call once at application startup."""
    global _offset, _synced
    try:
        before = datetime.now(timezone.utc)
        ntp_time = _fetch_ntp_utc()
        after = datetime.now(timezone.utc)
        system_mid = before + (after - before) / 2
        _offset = ntp_time - system_mid
        _synced = True
        log.info(
            "NTP sync OK — system clock offset: %+.1f seconds (system=%s, ntp=%s)",
            _offset.total_seconds(),
            system_mid.strftime("%H:%M:%S"),
            ntp_time.strftime("%H:%M:%S"),
        )
    except Exception as exc:  # noqa: BLE001
        _offset = timedelta(0)
        _synced = False
        log.warning("NTP sync failed (%s) — using system clock", exc)
    return _offset


def utcnow() -> datetime:
    """Return NTP-corrected current UTC time."""
    return datetime.now(timezone.utc) + _offset
