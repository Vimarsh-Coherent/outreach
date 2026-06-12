from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    database_url: str = "postgresql+asyncpg://outreach:outreach@localhost:5432/coherent_outreach"
    sync_database_url: str = "postgresql+psycopg2://outreach:outreach@localhost:5432/coherent_outreach"

    secret_key: str = Field(default="", description="Fernet key for at-rest encryption")
    token_secret: str = Field(default="dev-token-secret-change-me", description="HMAC seed")

    anthropic_api_key: str = ""
    openai_api_key: str = ""

    vault_storage_dir: str = "../data/vectorvault"
    vault_local: bool = True

    tick_interval_seconds: int = 60
    imap_poll_interval_seconds: int = 60
    send_concurrency: int = 8
    jitter_max_ms: int = 300_000

    li_daily_cap_connect: int = 20
    li_daily_cap_dm: int = 25
    email_daily_cap: int = 200

    # Connect → wait-for-acceptance → DM gate: how long to wait for a sent
    # invitation to be accepted before giving up on the gated DM step.
    li_accept_timeout_days: int = 7

    # When the extension is offline at send time, a parked lead is woken by the
    # next heartbeat. This is only the safety-net backoff if that wake is missed.
    li_offline_backstop_minutes: int = 60

    # A li_command the extension claimed but never completed (tab closed / SW
    # killed / dispatch bailed mid-command) is reclaimed to 'pending' after this
    # many minutes, on every dispatcher tick — fast self-healing vs the hourly
    # watchdog sweep. Keep it above the worst-case execute time (~2-3 min with
    # retries+heals) so we never reclaim a command that's still being worked.
    li_claim_stale_minutes: int = 5

    app_host: str = "127.0.0.1"
    app_port: int = 8000
    app_log_level: str = "info"

    seed_user_email: str = "user@local"
    seed_user_name: str = "Local User"

    # Workers
    workers_enabled: bool = True
    recovery_interval_seconds: int = 300
    recovery_reservation_ttl_seconds: int = 600

    # Email
    email_trace_host: str = "outreach.local"

    @property
    def vault_dir(self) -> Path:
        raw = Path(self.vault_storage_dir)
        if raw.is_absolute():
            p = raw
        else:
            # Anchor relative paths to the project root (parent of backend/).
            # config.py -> outreach/ -> src/ -> backend/ -> <project root>
            project_root = Path(__file__).resolve().parents[3]
            p = (project_root / raw).resolve()
        p.mkdir(parents=True, exist_ok=True)
        return p


@lru_cache
def get_settings() -> Settings:
    return Settings()
