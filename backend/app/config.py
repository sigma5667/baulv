from pydantic_settings import BaseSettings
from pathlib import Path


def _fix_postgres_url(url: str) -> str:
    """Convert postgres:// or postgresql:// to postgresql+asyncpg:// for asyncpg."""
    if url.startswith("postgres://"):
        return url.replace("postgres://", "postgresql+asyncpg://", 1)
    if url.startswith("postgresql://") and "+asyncpg" not in url:
        return url.replace("postgresql://", "postgresql+asyncpg://", 1)
    return url


class Settings(BaseSettings):
    # Database
    database_url: str = "postgresql+asyncpg://baulv:baulv_password@localhost:5432/baulv"

    # Claude API
    anthropic_api_key: str = ""

    # Server
    backend_host: str = "0.0.0.0"
    backend_port: int = 8000

    # File uploads
    upload_dir: str = "./uploads"
    # Upper bound on plan PDF uploads. Anything bigger is almost
    # certainly a scan at excessive DPI; we reject it rather than
    # trying to process it and running out of memory. Kept in sync
    # with the frontend's client-side check in PlanAnalysisPage.
    max_plan_file_mb: int = 25
    # Each page is a Claude Vision call (~30s and non-trivial cost).
    # Cap at something a human would plausibly upload as a single
    # building's plan set.
    max_plan_pages: int = 20

    # CORS
    frontend_url: str = "http://localhost:5173"

    # JWT Auth
    jwt_secret: str = "change-me-in-production-baulv-secret-2026"

    # Stripe
    stripe_secret_key: str = ""
    stripe_publishable_key: str = ""
    stripe_webhook_secret: str = ""
    stripe_price_basis: str = ""
    stripe_price_pro: str = ""

    # Beta / tester override — when true, EVERY authenticated user
    # gets Pro-level features regardless of their subscription_plan
    # and the project limit is effectively removed. Flip to true on
    # the server (Railway env var BETA_UNLOCK_ALL_FEATURES=true) for
    # tester days, then back to false for regular operation. The
    # flag is intentionally server-side only — do NOT expose it
    # directly to the client; the frontend reads the resolved
    # feature matrix from /auth/me/features instead.
    beta_unlock_all_features: bool = False

    # Optional Redis URL (e.g. ``redis://default:pwd@host:6379/0``) used
    # by the MCP per-key rate-limiter. When **set**, the limiter uses
    # Redis token-bucket counters (correct under multi-worker / multi-
    # node Railway deploys). When **unset**, the limiter falls back to
    # an in-process dict — *single-worker only*, with a WARN log at
    # boot. See ``app.rate_limit`` for details.
    redis_url: str | None = None

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}

    def model_post_init(self, __context) -> None:
        # Railway provides postgres:// but asyncpg needs postgresql+asyncpg://
        # Use object.__setattr__ because pydantic models are frozen after init
        object.__setattr__(self, "database_url", _fix_postgres_url(self.database_url))

    @property
    def upload_path(self) -> Path:
        path = Path(self.upload_dir)
        path.mkdir(parents=True, exist_ok=True)
        return path


settings = Settings()
