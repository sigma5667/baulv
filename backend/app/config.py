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
