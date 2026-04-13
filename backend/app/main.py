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

        # SPA catch-all: serve index.html for any non-API, non-asset route
        @app.get("/{full_path:path}")
        async def serve_spa(request: Request, full_path: str):
            # Don't catch API routes
            if full_path.startswith("api/"):
                return JSONResponse({"error": "not found"}, status_code=404)
            # Try to serve actual file first (e.g., favicon.ico)
            file_path = STATIC_DIR / full_path
            if file_path.is_file():
                return FileResponse(str(file_path))
            # Fallback to index.html for SPA routing
            return FileResponse(str(STATIC_DIR / "index.html"))

    return app


app = create_app()
