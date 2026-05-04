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

    # Comma-separated allow-list of email addresses that may invoke
    # ``/api/admin/*`` endpoints (e.g. the v23.3 manual cleanup
    # trigger). When empty (the default), every admin endpoint
    # returns 403 — production stays locked unless an operator
    # explicitly sets ``ADMIN_EMAILS=tobi@baulv.at`` (or several
    # comma-separated). The check is plain string-equality against
    # ``user.email`` after normal JWT auth, so the audit trail keeps
    # the regular login event for accountability.
    admin_emails: str = ""

    @property
    def admin_email_list(self) -> set[str]:
        """Normalised allow-list — lower-cased, whitespace-trimmed,
        empties dropped. Memoised by the implicit settings-singleton
        lifecycle (Settings is built once at boot)."""
        return {
            e.strip().lower()
            for e in self.admin_emails.split(",")
            if e.strip()
        }

    # Resend transactional email (DS-3 password reset, future
    # privacy-update notifications). When ``resend_api_key`` is
    # **unset**, ``app.services.email`` logs a warning and returns
    # without sending — dev runs without a Resend account stay
    # functional, the password-reset endpoint still returns 200 OK
    # (so we don't leak whether an account exists), only the email
    # itself never goes out. Production *must* set this; the
    # DEPLOY.md checklist enforces it.
    resend_api_key: str = ""
    # The verified sender. ``send.baulv.at`` is the DKIM-signed
    # subdomain so the SPF/DKIM/DMARC alignment passes. Bouncing
    # this back to the apex (``baulv.at``) would break DMARC unless
    # we also re-verify there.
    resend_from_email: str = "noreply@send.baulv.at"
    # Friendly From-Name shown in the recipient's inbox. Kept short
    # so it doesn't get truncated on mobile clients.
    resend_from_name: str = "BauLV"
    # Public-facing base URL the password-reset link is built on.
    # In dev that's the Vite server (``http://localhost:5173``); in
    # production Railway sets this to the canonical ``https://baulv.at``
    # (or whichever domain is currently primary). The link template is
    # ``{app_base_url}/passwort-zuruecksetzen?token={token}`` — must
    # be HTTPS in production or the token is exposed in transit.
    app_base_url: str = "http://localhost:5173"

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
