"""Watchdog state + circuit breaker.

Ported pattern from watchlink-main's query/watchdog.ts:
  - In-process state tracked here (no shared/Redis state — single-worker platform)
  - 5 patrol tiers each have their own `last_run` timestamp
  - Circuit breaker: N consecutive failures across all tiers -> disable patrols
    for cooldown_seconds, then auto-reset.
  - Rolling event log (last 200) for the dashboard.
"""
from __future__ import annotations

import logging
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Literal

log = logging.getLogger("outreach.watchdog")

TierName = Literal[
    "quick_check",
    "channel_patrol",
    "stuck_state_sweep",
    "deep_verify",
    "daily_reset",
    "circuit_breaker",
    "extension_failure",
    "selector_heal",
]
EventStatus = Literal["healthy", "issue", "emergency", "healed", "pattern_change"]


@dataclass(slots=True)
class WatchdogEvent:
    tier: TierName
    status: EventStatus
    message: str
    at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    detail: dict = field(default_factory=dict)


@dataclass(slots=True)
class WatchdogState:
    # Per-tier high-water marks
    last_quick_check: datetime | None = None
    last_channel_patrol: datetime | None = None
    last_stuck_sweep: datetime | None = None
    last_deep_verify: datetime | None = None
    last_daily_reset: datetime | None = None

    # Health signals
    backend_alive: bool = True
    db_alive: bool = True
    anthropic_alive: bool = True
    openai_alive: bool = True
    qdrant_alive: bool = True
    email_channels_healthy: int = 0
    email_channels_broken: int = 0
    linkedin_channels_alive: int = 0      # heartbeat < 30 min
    linkedin_channels_stale: int = 0

    # Backlog counters (refreshed each stuck-sweep)
    stuck_step_runs: int = 0              # status='reserving' > 10 min
    stuck_li_commands: int = 0            # status='claimed' > 10 min
    stuck_enrolments: int = 0             # next_send_at < now - 24h
    unclassified_events: int = 0          # reply/auto_reply/bounce without reply_sentiment

    # Connect → acceptance → DM gate (refreshed each stuck-sweep)
    awaiting_acceptance: int = 0          # enrolments parked waiting for an accept
    awaiting_past_deadline: int = 0       # parked + deadline elapsed (DM will be skipped)
    acceptance_detection_stalled: bool = False

    # Daily counters
    detections_today: int = 0
    api_cost_usd_today: float = 0.0
    linkedin_command_failures_today: int = 0
    linkedin_command_successes_today: int = 0
    connection_accepts_today: int = 0
    last_connection_accept_at: datetime | None = None

    # Extension inbox-scan telemetry (scanInbox self-loop)
    inbox_scans_today: int = 0
    inbox_replies_today: int = 0
    last_inbox_scan_at: datetime | None = None

    # Manual DM commands queued from Leads page
    manual_li_commands_today: int = 0
    last_manual_li_command_at: datetime | None = None

    # LinkedIn like/engage commands
    like_posts_today: int = 0

    # Per-intent failure counters (last 24h, in-process). Keyed by content-script
    # error name (e.g. "composer_remained_empty_after_insertion") so a dashboard
    # or operator can see which DOM intent is currently fragile.
    li_failure_by_intent: dict[str, int] = field(default_factory=dict)
    li_last_failure_at: datetime | None = None
    li_last_success_at: datetime | None = None

    # Circuit breaker
    consecutive_failures: int = 0
    disabled_until: datetime | None = None

    # Rolling event log
    events: deque[WatchdogEvent] = field(default_factory=lambda: deque(maxlen=200))


_STATE = WatchdogState()
_MAX_CONSECUTIVE_FAILURES = 5
_COOLDOWN_SECONDS = 30 * 60


def state() -> WatchdogState:
    return _STATE


def log_event(tier: TierName, status: EventStatus, message: str, **detail) -> None:
    ev = WatchdogEvent(tier=tier, status=status, message=message[:500], detail=detail)
    _STATE.events.appendleft(ev)
    fn = log.info if status == "healthy" else log.warning if status == "issue" else log.error
    fn("watchdog %s [%s] %s", tier, status, message)


def is_disabled() -> bool:
    if _STATE.disabled_until is None:
        return False
    if datetime.now(timezone.utc) >= _STATE.disabled_until:
        # Auto-reset
        _STATE.disabled_until = None
        _STATE.consecutive_failures = 0
        log_event(
            "circuit_breaker", "healed",
            "Circuit breaker reset after cooldown — patrols resuming",
        )
    return _STATE.disabled_until is not None


def record_failure() -> None:
    _STATE.consecutive_failures += 1
    if _STATE.consecutive_failures >= _MAX_CONSECUTIVE_FAILURES:
        from datetime import timedelta
        _STATE.disabled_until = datetime.now(timezone.utc) + timedelta(seconds=_COOLDOWN_SECONDS)
        log_event(
            "circuit_breaker", "emergency",
            f"CIRCUIT BREAKER OPEN after {_STATE.consecutive_failures} consecutive failures — "
            f"all patrols paused for {_COOLDOWN_SECONDS // 60} minutes",
        )


def record_success() -> None:
    if _STATE.consecutive_failures > 0:
        _STATE.consecutive_failures = 0


# ── Real-time LinkedIn-command telemetry ────────────────────────────────────
# Called from routes_extension.complete_command so the watchdog dashboard
# reflects extension activity live, instead of waiting for the hourly sweep.
_DOM_FRAGILITY_KEYS = (
    "messageButton_not_found_even_after_heal",
    "composeEditor_not_found_even_after_heal",
    "composer_remained_empty_after_insertion",
    "sendDmButton_not_enabled_text_didnt_register",
    "sendDmButton_not_found_even_after_heal",
    "send_click_was_noop_composer_still_has_text",
    "connectButton_not_found_even_after_heal",
    "addNoteButton_not_found_even_after_heal",
    "noteTextarea_not_found_even_after_heal",
    "sendInvitationButton_not_found_even_after_heal",
    "moreButton_not_found_even_after_heal",
    "Receiving end does not exist",
    "no_linkedin_tab_open",
    "all_tabs_unresponsive",
)


def _classify_extension_error(err: str | None) -> str:
    if not err:
        return "unknown"
    for key in _DOM_FRAGILITY_KEYS:
        if key in err:
            return key
    return "other"


def note_li_command_outcome(
    cmd_id: int, status: str, command_type: str = "dm", error: str | None = None,
) -> None:
    """Wire-in point: every li_command final outcome flows through here so the
    watchdog dashboard reflects extension activity in real time (not only on
    the hourly sweep) AND we accumulate per-intent failure counters that the
    selector_healer can use to decide which intents are currently fragile.
    """
    st = _STATE
    now = datetime.now(timezone.utc)
    if status == "done":
        st.linkedin_command_successes_today += 1
        st.li_last_success_at = now
        if command_type == "like_posts":
            st.like_posts_today += 1
            log_event(
                "extension_failure", "healthy",
                f"li_command#{cmd_id} (like_posts) completed (today: {st.like_posts_today})",
                cmd_id=cmd_id, command_type=command_type,
            )
        else:
            log_event(
                "extension_failure", "healthy",
                f"li_command#{cmd_id} ({command_type}) delivered",
                cmd_id=cmd_id, command_type=command_type,
            )
        return

    # status == "failed" path
    st.linkedin_command_failures_today += 1
    st.li_last_failure_at = now
    key = _classify_extension_error(error)
    st.li_failure_by_intent[key] = st.li_failure_by_intent.get(key, 0) + 1
    severity = "issue" if key != "other" else "issue"
    if key == "other":
        # Unknown failure — escalate so it gets seen
        severity = "emergency" if st.li_failure_by_intent[key] >= 3 else "issue"
    log_event(
        "extension_failure", severity,
        f"li_command#{cmd_id} ({command_type}) failed: {key}",
        cmd_id=cmd_id, command_type=command_type, error=(error or "")[:300],
        intent_failure_count=st.li_failure_by_intent[key],
    )


def note_inbox_scan(reply_count: int) -> None:
    """Called from /replies when the extension posts inbound LinkedIn DMs."""
    st = _STATE
    st.inbox_scans_today += 1
    st.inbox_replies_today += reply_count
    st.last_inbox_scan_at = datetime.now(timezone.utc)
    status: EventStatus = "healthy" if reply_count > 0 else "healthy"
    log_event(
        "extension_failure", status,
        f"inbox_scan: {reply_count} new LinkedIn repl{'y' if reply_count == 1 else 'ies'} captured "
        f"(total today: {st.inbox_replies_today})",
        reply_count=reply_count,
        inbox_scans_today=st.inbox_scans_today,
    )


def note_manual_li_command(lead_id: int, target_url: str) -> None:
    """Called when a manual LinkedIn DM is queued from the Leads page."""
    st = _STATE
    st.manual_li_commands_today += 1
    st.last_manual_li_command_at = datetime.now(timezone.utc)
    log_event(
        "extension_failure", "healthy",
        f"manual_li_dm queued for lead#{lead_id} → {target_url[:60]}",
        lead_id=lead_id, manual_today=st.manual_li_commands_today,
    )


def note_like_posts_outcome(cmd_id: int, status: str, liked: int = 0) -> None:
    """Called when a like_posts command completes."""
    st = _STATE
    if status == "done":
        st.like_posts_today += 1
        st.li_last_success_at = datetime.now(timezone.utc)
        log_event(
            "extension_failure", "healthy",
            f"like_posts#{cmd_id} done — liked {liked} post(s) (today: {st.like_posts_today})",
            cmd_id=cmd_id, liked=liked,
        )
    else:
        log_event(
            "extension_failure", "issue",
            f"like_posts#{cmd_id} failed",
            cmd_id=cmd_id,
        )


def note_connection_accepted(enrolment_id: int, li_url: str | None = None) -> None:
    """Real-time signal: a gated invitation was detected as accepted and its DM
    step released. Lets the dashboard show the acceptance funnel live and lets
    the Tier-3 stall check know detection is working."""
    st = _STATE
    st.connection_accepts_today += 1
    st.last_connection_accept_at = datetime.now(timezone.utc)
    log_event(
        "extension_failure", "healed",  # tier reused for ext signal
        f"connection accepted for enrolment#{enrolment_id} — DM released",
        enrolment_id=enrolment_id, li_url=li_url,
    )


def reset_li_failure_counters() -> None:
    """Called by daily_reset at Tier 5 (or manually) so the per-intent
    counters represent a rolling 24h window."""
    _STATE.li_failure_by_intent.clear()
    _STATE.linkedin_command_failures_today = 0
    _STATE.linkedin_command_successes_today = 0
    _STATE.connection_accepts_today = 0


def fragile_intents(min_failures: int = 2) -> list[str]:
    """Return intents that have failed at least `min_failures` times in the
    current 24h window. The selector_healer / Tier 3 retry can use this to
    pre-emptively request fresh selectors before re-queueing the command.
    """
    return [
        k for k, v in _STATE.li_failure_by_intent.items()
        if v >= min_failures and k not in ("unknown", "other")
    ]
