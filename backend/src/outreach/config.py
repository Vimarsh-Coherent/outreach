from functools import lru_cache
from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# backend/src/outreach/config.py -> parents[2] == backend/
_BACKEND_DIR = Path(__file__).resolve().parents[2]
_ENV_FILE = _BACKEND_DIR / ".env"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(_ENV_FILE) if _ENV_FILE.is_file() else ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    database_url: str = "postgresql+asyncpg://outreach:outreach@localhost:5432/coherent_outreach"
    sync_database_url: str = "postgresql+psycopg2://outreach:outreach@localhost:5432/coherent_outreach"

    secret_key: str = Field(default="", description="Fernet key for at-rest encryption")
    token_secret: str = Field(default="dev-token-secret-change-me", description="HMAC seed")

    anthropic_api_key: str = ""
    openai_api_key: str = ""

    # DeepSeek (OpenAI-compatible). When deepseek_api_key is set, the AI sequence
    # generator uses DeepSeek instead of Anthropic — cheap, and not subject to the
    # Anthropic account usage cap.
    deepseek_api_key: str = ""
    deepseek_base_url: str = "https://api.deepseek.com"
    deepseek_model: str = "deepseek-chat"

    # HubSpot native CRM connector (OAuth). Register a HubSpot app, set its
    # redirect URI to hubspot_redirect_uri, and put the app's client id/secret
    # here (or in .env). Empty client_id = connector disabled.
    hubspot_client_id: str = ""
    hubspot_client_secret: str = ""
    hubspot_redirect_uri: str = "http://localhost:8000/api/crm/hubspot/callback"
    # Where to bounce the browser back to after the OAuth dance completes.
    frontend_base_url: str = "http://localhost:5173"

    # WhatsApp (Baileys sidecar). The sidecar drives a logged-in WhatsApp number
    # over the multi-device protocol and exposes a small REST API; wa_api_url is
    # where the backend reaches it, wa_api_key is the shared secret. Sends flow
    # through the existing send-window + jitter + daily-cap machinery — keep the
    # cap conservative: this is the unofficial Web protocol, so high volume risks
    # number bans. The boot agent manages the sidecar process (industrial mode).
    wa_api_url: str = "http://127.0.0.1:8085"
    wa_api_key: str = ""
    wa_timeout_seconds: int = 20
    whatsapp_daily_cap: int = 100

    # Strip stray whitespace from pasted API keys (common copy-paste issue).
    @field_validator("anthropic_api_key", "openai_api_key", "deepseek_api_key", mode="before")
    @classmethod
    def _strip_api_keys(cls, v: object) -> object:
        return v.strip() if isinstance(v, str) else v

    # Vector-store extras (Aman's vault layer). The active qdrant_url +
    # embedding_model for the RAG eval pipeline are defined just below.
    qdrant_api_key: str = ""
    qdrant_local_path: str = "data/qdrant"
    embedding_dim: int = 1536

    # Send-time per-lead personalization (Phase 3). Cheap, high-volume → Haiku.
    # Fail-open: any AI error/timeout → the plain rendered template is sent.
    personalize_model: str = "claude-haiku-4-5-20251001"
    personalize_timeout_seconds: int = 10
    # AI sequence generator (Sequences page → "Generate with AI"). Same cheap
    # model; this is a one-shot interactive call so it gets a longer timeout.
    sequence_gen_timeout_seconds: int = 40
    # When true, LinkedIn connect/DM bodies are personalized even if a step's
    # config doesn't set ai_personalize — lets manual sequences benefit too.
    ai_personalize_linkedin_default: bool = True

    vault_storage_dir: str = "../data/vectorvault"
    vault_local: bool = True

    # Sequence RAG (Qdrant + MiniLM)
    qdrant_url: str = "http://127.0.0.1:6333"
    qdrant_collection: str = "sequence_rag_chunks"
    embedding_model: str = "all-MiniLM-L6-v2"
    rag_chunk_size: int = 1000
    rag_chunk_overlap: int = 200
    rag_top_k: int = 5
    rag_fetch_k: int = 8
    rag_min_score: float = 0.35
    rag_chunk_prompt_chars: int = 1000  # full chunk to the LLM → more source text to ground in

    # Conditional branching: how long a step with reply/open/click transitions
    # waits for those events before evaluating the branch (default path otherwise).
    branch_wait_hours: int = 48

    tick_interval_seconds: int = 60
    imap_poll_interval_seconds: int = 60
    send_concurrency: int = 8
    jitter_max_ms: int = 300_000

    li_daily_cap_connect: int = 20
    li_daily_cap_dm: int = 50  # bumped from 25 for testing
    # LinkedIn caps reset at local MIDNIGHT in this timezone (calendar-day reset),
    # so "a new day" always starts at 0 — not a rolling 24h window.
    li_cap_reset_timezone: str = "Asia/Kolkata"
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

    # Open/click tracking. Pixels + click redirects are served from this PUBLIC
    # base URL (the recipient's mail client must reach it) — e.g. a Cloudflare
    # Tunnel / ngrok URL in local dev, or the deployed host. Empty = tracking
    # disabled regardless of per-sequence flags.
    tracking_base_url: str = ""

    @property
    def qdrant_path(self) -> Path:
        raw = Path(self.qdrant_local_path)
        if raw.is_absolute():
            p = raw
        else:
            project_root = Path(__file__).resolve().parents[3]
            p = (project_root / raw).resolve()
        p.mkdir(parents=True, exist_ok=True)
        return p

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
    # .env in backend/ should win over stale machine-level env vars
    # (common on Windows when keys are updated locally but not in system env).
    try:
        from dotenv import load_dotenv

        env_path = Path(__file__).resolve().parents[2] / ".env"
        if env_path.is_file():
            load_dotenv(env_path, override=True)
    except Exception:  # noqa: BLE001
        pass
    return Settings()
