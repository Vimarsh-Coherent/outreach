from datetime import datetime
from typing import Literal

from fastapi import APIRouter, BackgroundTasks, Depends
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from outreach.db import get_session
from outreach.deps import get_current_user
from outreach.models.user import User
from outreach.services import watchdog_state
from outreach.workers import watchdog

router = APIRouter(prefix="/api/watchdog", tags=["watchdog"])


class WatchdogEventOut(BaseModel):
    tier: str
    status: str
    message: str
    at: datetime
    detail: dict


class WatchdogStateOut(BaseModel):
    last_quick_check: datetime | None
    last_channel_patrol: datetime | None
    last_stuck_sweep: datetime | None
    last_deep_verify: datetime | None
    last_daily_reset: datetime | None

    backend_alive: bool
    db_alive: bool
    anthropic_alive: bool
    openai_alive: bool
    chroma_alive: bool
    email_channels_healthy: int
    email_channels_broken: int
    linkedin_channels_alive: int
    linkedin_channels_stale: int

    stuck_step_runs: int
    stuck_li_commands: int
    stuck_enrolments: int
    unclassified_events: int

    # Connect → acceptance → DM gate
    awaiting_acceptance: int = 0
    awaiting_past_deadline: int = 0
    acceptance_detection_stalled: bool = False
    connection_accepts_today: int = 0
    last_connection_accept_at: datetime | None = None

    detections_today: int
    linkedin_command_failures_today: int
    consecutive_failures: int
    disabled_until: datetime | None

    # Live LinkedIn extension telemetry (updated on every command outcome)
    linkedin_command_successes_today: int = 0
    li_last_success_at: datetime | None = None
    li_last_failure_at: datetime | None = None
    li_failure_by_intent: dict[str, int] = {}
    li_fragile_intents: list[str] = []

    events: list[WatchdogEventOut]


@router.get("/state", response_model=WatchdogStateOut)
async def get_state(
    _user: User = Depends(get_current_user),
) -> WatchdogStateOut:
    st = watchdog_state.state()
    events = [
        WatchdogEventOut(
            tier=e.tier, status=e.status, message=e.message,
            at=e.at, detail=e.detail,
        )
        for e in list(st.events)[:60]
    ]
    return WatchdogStateOut(
        last_quick_check=st.last_quick_check,
        last_channel_patrol=st.last_channel_patrol,
        last_stuck_sweep=st.last_stuck_sweep,
        last_deep_verify=st.last_deep_verify,
        last_daily_reset=st.last_daily_reset,
        backend_alive=st.backend_alive,
        db_alive=st.db_alive,
        anthropic_alive=st.anthropic_alive,
        openai_alive=st.openai_alive,
        chroma_alive=st.chroma_alive,
        email_channels_healthy=st.email_channels_healthy,
        email_channels_broken=st.email_channels_broken,
        linkedin_channels_alive=st.linkedin_channels_alive,
        linkedin_channels_stale=st.linkedin_channels_stale,
        stuck_step_runs=st.stuck_step_runs,
        stuck_li_commands=st.stuck_li_commands,
        stuck_enrolments=st.stuck_enrolments,
        unclassified_events=st.unclassified_events,
        awaiting_acceptance=st.awaiting_acceptance,
        awaiting_past_deadline=st.awaiting_past_deadline,
        acceptance_detection_stalled=st.acceptance_detection_stalled,
        connection_accepts_today=st.connection_accepts_today,
        last_connection_accept_at=st.last_connection_accept_at,
        detections_today=st.detections_today,
        linkedin_command_failures_today=st.linkedin_command_failures_today,
        linkedin_command_successes_today=st.linkedin_command_successes_today,
        li_last_success_at=st.li_last_success_at,
        li_last_failure_at=st.li_last_failure_at,
        li_failure_by_intent=dict(st.li_failure_by_intent),
        li_fragile_intents=watchdog_state.fragile_intents(),
        consecutive_failures=st.consecutive_failures,
        disabled_until=st.disabled_until,
        events=events,
    )


TierName = Literal["quick_check", "channel_patrol", "stuck_state_sweep", "deep_verify", "daily_reset"]


@router.post("/run/{tier}")
async def run_tier(
    tier: TierName,
    background: BackgroundTasks,
    _user: User = Depends(get_current_user),
) -> dict:
    """Manually trigger a patrol tier — useful for testing + dashboard buttons."""
    fn_map = {
        "quick_check": watchdog.quick_check,
        "channel_patrol": watchdog.channel_patrol,
        "stuck_state_sweep": watchdog.stuck_state_sweep,
        "deep_verify": watchdog.deep_verify,
        "daily_reset": watchdog.daily_reset,
    }
    fn = fn_map[tier]
    result = await fn()
    return {"tier": tier, "result": result}


@router.post("/circuit-breaker/reset")
async def reset_breaker(_user: User = Depends(get_current_user)) -> dict:
    st = watchdog_state.state()
    st.consecutive_failures = 0
    st.disabled_until = None
    watchdog_state.log_event(
        "circuit_breaker", "healed", "manually reset via API",
    )
    return {"ok": True}
