from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from outreach.api import (
    routes_channels,
    routes_dashboard,
    routes_enrolments,
    routes_extension,
    routes_followups,
    routes_health,
    routes_leads,
    routes_sequences,
    routes_timeline,
    routes_watchdog,
)
from outreach.config import get_settings
from outreach.workers import scheduler

settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
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
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(routes_health.router)
app.include_router(routes_channels.router)
app.include_router(routes_leads.router)
app.include_router(routes_sequences.router)
app.include_router(routes_enrolments.router)
app.include_router(routes_timeline.router)
app.include_router(routes_dashboard.router)
app.include_router(routes_followups.router)
app.include_router(routes_extension.router)
app.include_router(routes_watchdog.router)
