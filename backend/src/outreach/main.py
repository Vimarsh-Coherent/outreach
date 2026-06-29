from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from outreach.api import (
    routes_ai_sequences,
    routes_analytics,
    routes_channels,
    routes_dashboard,
    routes_enrolments,
    routes_extension,
    routes_followups,
    routes_health,
    routes_leads,
    routes_sequence_rag,
    routes_sequences,
    routes_timeline,
    routes_watchdog,
    routes_whatsapp,
)
from outreach.config import get_settings
from outreach.services.time_util import sync_clock
from outreach.workers import scheduler


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Reload .env on every process start so API key changes take effect
    # without requiring a manual cache clear.
    get_settings.cache_clear()
    sync_clock()
    scheduler.start()
    try:
        yield
    finally:
        scheduler.stop()


app = FastAPI(
    title="Coherent Outreach",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "http://localhost:5174",
        "http://127.0.0.1:5174",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(routes_health.router)
app.include_router(routes_ai_sequences.router)
app.include_router(routes_channels.router)
app.include_router(routes_leads.router)
app.include_router(routes_sequences.router)
app.include_router(routes_sequence_rag.router)
app.include_router(routes_enrolments.router)
app.include_router(routes_timeline.router)
app.include_router(routes_dashboard.router)
app.include_router(routes_followups.router)
app.include_router(routes_extension.router)
app.include_router(routes_watchdog.router)
app.include_router(routes_whatsapp.router)
app.include_router(routes_analytics.router)
