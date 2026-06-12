import logging
from pydantic_settings import BaseSettings
from pathlib import Path


logger = logging.getLogger(__name__)

# Platzhalter-Secrets, die im Repo für die lokale Entwicklung mitgeliefert
# werden. In Produktion MÜSSEN sie per Umgebungsvariable überschrieben
# werden — andernfalls bricht der Boot fail-closed ab
# (siehe ``_enforce_production_secrets``).
_DEFAULT_JWT_SECRET = "change-me-in-production-baulv-secret-2026"
_DEFAULT_ANALYTICS_SALT = "change-me-in-production-baulv-analytics-salt-2026"
_MIN_JWT_SECRET_LENGTH = 32


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
    # v24.4.1+ — Environment-Toggle für CORS-Policy. ``development``
    # erlaubt zusätzlich ``http://localhost:5173`` und
    # ``http://localhost:3000`` als Origin. ``production`` nimmt
    # ausschließlich ``frontend_url`` + die ``allowed_origins``-Liste
    # — Localhost-Origins werden gestrichen, damit kein lokal laufender
    # Browser-Code auf den Prod-Backend zugreifen kann.
    environment: str = "development"
    # Comma-separated extra Origins die im production-Mode erlaubt
    # werden (z.B. "https://baulv.at,https://www.baulv.at"). Leer
    # bedeutet: nur ``frontend_url``. Das ist das safe default —
    # wenn die ENV-Variable in production nicht gesetzt ist und
    # ``frontend_url`` zufällig leer wäre, lässt CORS überhaupt keine
    # Origin durch (lieber broken-Frontend als wide-open Backend).
    allowed_origins: str = ""

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

    # v24.4.6 — Two-Pass-Plananalyse-Diagnose. Während der Beta-Phase
    # ``DEBUG_SAVE_CROPS=true`` in Railway setzen, damit jede Seite
    # ihre Render-Zwischenstufen als JPEG unter
    # ``{upload_dir}/debug-crops/{plan_id}/`` ablegt:
    #   * page-N-low_res.jpg     — 1536-px-Probe, an Haiku geschickt
    #   * page-N-high_res_crop.jpg — high-DPI-Clip nach BBox-Crop
    #   * page-N-resized.jpg     — falls Crop > 1536 verkleinert
    #   * page-N-tile-{0..3}.jpg — falls 2×2-Kachel-Pfad genommen
    #   * page-N-bbox.json       — BBox-Koords + Skalierungs-Faktoren
    # Default ``False`` damit Production keinen unbegrenzten Disk-
    # Verbrauch hat. Nach Beta-Ende auf ``False`` zurück; Operator
    # muss den Ordner manuell aufräumen (kein Auto-Cleanup).
    debug_save_crops: bool = False

    # v24.4.2 — Coming-Soon-Gate vor der öffentlichen Marketing-
    # Oberfläche. Solange baulv.at in der geschlossenen Test-Phase
    # läuft, fragt das Frontend an dieser Stelle einen geteilten
    # Beta-Code ab. Bei Match liefert ``POST /api/beta/verify`` ein
    # HMAC-signiertes Token (30 Tage TTL) zurück, das die SPA in
    # ``localStorage`` ablegt. Leer-Default ist *fail-safe*: ohne
    # gesetzte ENV-Variable lehnt der Gate jeden Code ab — niemand
    # kommt rein. Rotation: Wert in Railway ändern → alle aktiven
    # Token werden beim nächsten ``GET /api/beta/status`` ungültig
    # (HMAC-Signatur enthält den Code, siehe ``app/api/beta_gate.py``).
    beta_access_code: str = ""

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

    # v23.8 — DSGVO Art. 4 Nr. 5 pseudonymisation salt for the
    # ``usage_analytics`` table. Hashed with the user's UUID via
    # ``sha256(user.id || salt)`` to produce ``anonymous_user_id``,
    # so observers without the salt cannot correlate the rows back
    # to a specific user. The default value is dev-only ("change me
    # in production"); production MUST set ``ANALYTICS_SALT`` to a
    # 32+-byte secret. The analytics service refuses to record
    # events when the default value is detected on a production
    # boot — see ``app.services.analytics._is_dev_salt``. Rotating
    # the salt breaks the ability to correlate past and future
    # events from the same user; documented as a deliberate
    # operator-action in DEPLOY.md.
    analytics_salt: str = "change-me-in-production-baulv-analytics-salt-2026"

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


def _enforce_production_secrets(s: "Settings") -> None:
    """Fail the boot closed when production runs on dev placeholder secrets.

    Security-Härtung (Audit 2026-06): ``jwt_secret`` signiert ALLE
    Access-Tokens (HS256) und die Beta-Gate-HMAC; ``analytics_salt``
    pseudonymisiert die ``usage_analytics``-Tabelle. Beide haben einen
    im Repo öffentlich lesbaren Default. Vorher gab es — anders als der
    config-Docstring behauptete — keinen Boot-Check, der den Default in
    Produktion ablehnt: bei vergessener ENV-Variable lief die App mit
    bekanntem Signing-Key (forge-bare Beta-Tokens) bzw. trivial
    reversibler Pseudonymisierung.

    Greift NUR bei ``environment == "production"`` — lokale Entwicklung
    läuft unverändert mit den Defaults.
    """
    if s.environment.lower() != "production":
        return

    problems: list[str] = []

    secret = (s.jwt_secret or "").strip()
    if not secret or secret == _DEFAULT_JWT_SECRET:
        problems.append(
            "JWT_SECRET ist nicht gesetzt bzw. nutzt den Repo-Default"
        )
    elif len(secret) < _MIN_JWT_SECRET_LENGTH:
        problems.append(
            f"JWT_SECRET ist kürzer als {_MIN_JWT_SECRET_LENGTH} Zeichen"
        )

    salt = (s.analytics_salt or "").strip()
    if not salt or salt == _DEFAULT_ANALYTICS_SALT:
        problems.append(
            "ANALYTICS_SALT ist nicht gesetzt bzw. nutzt den Repo-Default"
        )

    if problems:
        raise RuntimeError(
            "Unsichere Production-Konfiguration — Boot abgebrochen:\n  - "
            + "\n  - ".join(problems)
            + "\nBitte diese Umgebungsvariablen in Railway mit starken, "
            "zufälligen Werten setzen (z.B. `openssl rand -hex 32`)."
        )


settings = Settings()

# Fail-closed-Check der Production-Secrets direkt nach dem Laden der
# Settings — bricht den Prozess-Start ab, bevor irgendein Endpoint mit
# unsicherem Schlüssel erreichbar wird.
_enforce_production_secrets(settings)
