import os
import subprocess
import logging
from pathlib import Path

from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse

from app.config import settings
from app.api.router import api_router

# Configure root logger once at import time so our ``app.*`` loggers
# actually emit to stdout under gunicorn/uvicorn on Railway. Without
# this the default threshold is WARNING and every `logger.info(...)`
# diagnostic line we add is silently dropped. Gunicorn's own logs are
# unaffected — they use their own logger hierarchy.
logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
# Belt-and-suspenders: even if someone else configures the root logger
# before we get here, force app.* to INFO so our diagnostics survive.
logging.getLogger("app").setLevel(logging.INFO)

logger = logging.getLogger(__name__)

STATIC_DIR = Path("/app/static")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Run Alembic migrations on startup
    try:
        result = subprocess.run(
            ["alembic", "upgrade", "head"],
            capture_output=True, text=True, timeout=60,
        )
        if result.returncode == 0:
            logger.info("Alembic migrations applied successfully")
        else:
            logger.error("Alembic migration failed: %s", result.stderr)
    except Exception as e:
        logger.error("Failed to run migrations: %s", e)
    yield


def create_app() -> FastAPI:
    app = FastAPI(
        title="BauLV",
        description="AI-gestuetzte Bau-Ausschreibungssoftware",
        version="0.1.0",
        lifespan=lifespan,
    )

    # CORS — allow frontend origin (dev) and Railway domain
    origins = [
        settings.frontend_url,
        "http://localhost:5173",
        "http://localhost:3000",
    ]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Health check endpoint (used by Railway)
    @app.get("/api/health")
    async def health():
        return JSONResponse({"status": "ok", "version": "0.1.0"})

    # API routes
    app.include_router(api_router, prefix="/api")

    # Serve frontend static files in production
    if STATIC_DIR.exists() and (STATIC_DIR / "index.html").exists():
        # Serve static assets (JS, CSS, images)
        app.mount("/assets", StaticFiles(directory=str(STATIC_DIR / "assets")), name="assets")

        # Serve files from public/ that ended up in dist/ (manifest.json, sw.js, icons/)
        @app.get("/manifest.json")
        async def manifest():
            f = STATIC_DIR / "manifest.json"
            if f.exists():
                return FileResponse(str(f), media_type="application/manifest+json")
            return JSONResponse({"error": "not found"}, status_code=404)

        @app.get("/sw.js")
        async def service_worker():
            f = STATIC_DIR / "sw.js"
            if f.exists():
                return FileResponse(str(f), media_type="application/javascript")
            return JSONResponse({"error": "not found"}, status_code=404)

        @app.get("/icons/{path:path}")
        async def icons(path: str):
            f = STATIC_DIR / "icons" / path
            if f.exists():
                return FileResponse(str(f))
            return JSONResponse({"error": "not found"}, status_code=404)

        # Headers we want on every index.html response so no browser,
        # CDN, or proxy ever serves a stale HTML referencing a dead
        # hashed bundle. We tolerate caching of JS/CSS (content hash
        # in filename protects against staleness) but index.html MUST
        # always revalidate. Past bug: a Chrome that cached index.html
        # under a long-lived expiry kept loading the old /assets/*.js
        # across deploys, making it look like the deploy didn't land.
        _INDEX_NOCACHE_HEADERS = {
            "Cache-Control": "no-cache, no-store, must-revalidate",
            "Pragma": "no-cache",
            "Expires": "0",
        }

        # SPA catch-all: serve index.html for any non-API, non-asset route
        @app.get("/{full_path:path}")
        async def serve_spa(request: Request, full_path: str):
            # Don't catch API routes
            if full_path.startswith("api/"):
                return JSONResponse({"error": "not found"}, status_code=404)
            # Try to serve actual file first (e.g., favicon.ico). Static
            # files get normal (browser-default) caching — they're
            # either content-hashed or OS icons, both safe.
            file_path = STATIC_DIR / full_path
            if file_path.is_file():
                return FileResponse(str(file_path))
            # SPA fallback: always index.html, always no-cache. The
            # catch-all path matches empty string too, so ``/`` lands
            # here as well.
            return FileResponse(
                str(STATIC_DIR / "index.html"),
                headers=_INDEX_NOCACHE_HEADERS,
            )

    return app


app = create_app()
