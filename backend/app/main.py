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
from app.mcp import build_mcp_app
from app.rate_limit import select_backend_at_boot as _select_rate_limit_backend

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


def _prewarm_reportlab() -> None:
    """Import every reportlab module the PDF exporter touches.

    Reportlab lazy-imports a dozen submodules (pdfgen.canvas,
    platypus.SimpleDocTemplate, lib.pagesizes, lib.styles, …) on the
    first call to ``export_lv_pdf``. On a cold Railway container that
    first call can take 15-40s just to hydrate the imports, which
    blows past the edge proxy's request timeout and surfaces to the
    user as a 502 Bad Gateway — the PDF "fails" then works on retry.
    Importing the heavy bits at startup trades ~3s of boot time for
    a consistent first-PDF latency in the 1-2s range.

    Failures here are non-fatal: if reportlab can't import, the PDF
    export endpoint will surface a clean 500 later instead of crashing
    the whole process at boot.
    """
    try:
        # These three are the top of the dependency graph used in
        # app/export/pdf_exporter.py — importing them warms everything
        # transitively.
        import reportlab.pdfgen.canvas  # noqa: F401
        import reportlab.lib.pagesizes  # noqa: F401
        import reportlab.lib.styles  # noqa: F401
        import reportlab.platypus  # noqa: F401
        logger.info("startup.reportlab_prewarmed")
    except Exception as exc:  # pragma: no cover — best-effort
        logger.warning("startup.reportlab_prewarm_failed exc=%s", exc)


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

    # Pre-warm reportlab — see _prewarm_reportlab docstring. This
    # runs before we yield so the first PDF export request doesn't
    # pay the cold-start cost (was: ~30s → 502).
    _prewarm_reportlab()

    # Print every diagnostic-worthy setting exactly once at boot so
    # Railway startup logs answer "is my env var actually set?" without
    # needing a live request. Keep the anthropic key OPAQUE — log its
    # presence and length only, never the value itself.
    anthropic_key = settings.anthropic_api_key or ""
    logger.info(
        "startup.settings beta_unlock_all_features=%s "
        "anthropic_api_key_present=%s anthropic_api_key_len=%d "
        "frontend_url=%s",
        settings.beta_unlock_all_features,
        bool(anthropic_key.strip()),
        len(anthropic_key),
        settings.frontend_url,
    )
    if settings.beta_unlock_all_features:
        # A loud, unambiguous banner line that a grep for
        # "BETA_UNLOCK" in Railway logs will surface instantly.
        logger.warning(
            "startup.BETA_UNLOCK_ACTIVE — all Pro features are unlocked "
            "for every authenticated user. Flip BETA_UNLOCK_ALL_FEATURES "
            "back to false (or delete the env var) to restore normal "
            "plan gating."
        )

    # Pick the rate-limit backend once at boot. Either Redis (if
    # ``REDIS_URL`` is set) or the in-memory fallback that emits a
    # WARN log. The first call to a rate-limited handler will lazily
    # initialize too, but doing it here means the operator-visible
    # "which backend am I on" log line lands at startup time, not on
    # the first MCP request.
    _select_rate_limit_backend()
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

    # MCP (Model Context Protocol) endpoint for headless agents —
    # Claude Desktop, n8n, ChatGPT custom connectors. Mounted as a
    # Starlette sub-app because the SSE transport ships its own ASGI
    # POST handler that doesn't compose with FastAPI's body parsing
    # (would double-consume the JSON-RPC payload). See ``app/mcp/``
    # for the auth model and tool catalogue. Must be mounted BEFORE
    # the SPA catch-all below so ``/mcp/*`` requests reach this app
    # instead of being served the index.html.
    app.mount("/mcp", build_mcp_app())

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

        # Agent-discovery files. These two URLs are what AI agents and
        # headless connectors (Claude Desktop, ChatGPT Custom
        # Connectors, n8n, etc.) crawl to find our MCP server. The
        # source files live in ``frontend/public/`` so the dev server
        # (Vite) and the production build (Docker stage 1) both pick
        # them up. We add explicit handlers — instead of relying on
        # the SPA catch-all below — for three reasons:
        #
        # 1. Bulletproof routing: ``.well-known/`` is a hidden
        #    directory and we don't want to bet on every static-file
        #    pipeline copying it without surprises.
        # 2. Correct ``Content-Type``: ``FileResponse``'s mime
        #    auto-detect maps ``.txt`` → ``text/plain``, but agent
        #    crawlers expect ``text/markdown`` for ``llms.txt`` and a
        #    JSON-typed response for ``mcp.json``. Wrong type can
        #    cause some crawlers to skip the file.
        # 3. CORS: agents come from arbitrary origins. The discovery
        #    files are public-by-design, so a wildcard ACAO is the
        #    right answer here.
        @app.get("/llms.txt")
        async def llms_txt():
            f = STATIC_DIR / "llms.txt"
            if f.exists():
                return FileResponse(
                    str(f),
                    media_type="text/markdown; charset=utf-8",
                    headers={"Access-Control-Allow-Origin": "*"},
                )
            return JSONResponse({"error": "not found"}, status_code=404)

        @app.get("/.well-known/mcp.json")
        async def well_known_mcp():
            f = STATIC_DIR / ".well-known" / "mcp.json"
            if f.exists():
                return FileResponse(
                    str(f),
                    media_type="application/json; charset=utf-8",
                    headers={"Access-Control-Allow-Origin": "*"},
                )
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
            # MCP requests are handled by the Starlette sub-app mounted
            # at /mcp; the mount registration above already takes
            # precedence over this catch-all. We add the explicit guard
            # anyway so a future refactor that shuffles registration
            # order can't accidentally start serving index.html for
            # missing MCP routes — better to 404 cleanly.
            if full_path.startswith("mcp/") or full_path == "mcp":
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
