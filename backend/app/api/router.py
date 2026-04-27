from fastapi import APIRouter

from app.api.projects import router as projects_router
from app.api.plans import router as plans_router
from app.api.buildings import router as buildings_router
from app.api.rooms import router as rooms_router
from app.api.lv import router as lv_router
from app.api.templates import router as templates_router
from app.api.chat import router as chat_router
from app.api.support_chat import router as support_chat_router
from app.api.auth import router as auth_router
from app.api.api_keys import router as api_keys_router
from app.api.stripe_api import router as stripe_router

api_router = APIRouter()

api_router.include_router(auth_router, prefix="/auth", tags=["Auth"])
# Programmatic-access tokens (PATs) for headless agents. Sub-mounted
# under ``/auth/me/api-keys`` so they live with the rest of the
# "current user" surface; auth itself is JWT-only on this router
# (PATs can't manage their own lifecycle — see app/api/api_keys.py).
api_router.include_router(
    api_keys_router, prefix="/auth/me/api-keys", tags=["API Keys"]
)
api_router.include_router(stripe_router, prefix="/stripe", tags=["Stripe"])
api_router.include_router(projects_router, prefix="/projects", tags=["Projects"])
api_router.include_router(plans_router, prefix="/plans", tags=["Plans"])
api_router.include_router(buildings_router, tags=["Buildings"])
api_router.include_router(rooms_router, tags=["Rooms"])
api_router.include_router(lv_router, prefix="/lv", tags=["Leistungsverzeichnis"])
api_router.include_router(templates_router, prefix="/templates", tags=["LV-Vorlagen"])
api_router.include_router(chat_router, prefix="/chat", tags=["Chat"])
# Public, unauthenticated: landing-page support widget.
api_router.include_router(support_chat_router, tags=["Support Chat"])
