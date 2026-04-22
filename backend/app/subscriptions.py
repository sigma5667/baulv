"""Feature gating based on subscription plans.

When ``settings.beta_unlock_all_features`` is true, every gated
feature is treated as available and the project limit is lifted for
all users. That flag is intended for tester days: flip it on, and
a basis-plan user looks exactly like a pro-plan user everywhere
feature gating happens (API dependency, feature matrix returned to
the SPA, project creation limit). Flip it back off and normal plan
enforcement returns without any data migration.

The beta flag only gates the Pro-level features listed in
``FEATURE_REQUIREMENTS`` and the project limit. Enterprise-only
features (``angebotsvergleich``, ``team_multiuser``, ``api_access``)
are NOT unlocked by the beta flag, because they rely on
infrastructure that hasn't shipped to basis/pro tenants — turning
them on for a tester would surface half-built behavior.
"""

from fastapi import Depends, HTTPException

from app.auth import get_current_user
from app.config import settings
from app.db.models.user import User

PLAN_HIERARCHY = {"basis": 0, "pro": 1, "enterprise": 2}

FEATURE_REQUIREMENTS: dict[str, str] = {
    "ai_plan_analysis": "pro",
    "ai_position_generator": "pro",
    "ai_chat": "pro",
    "excel_export": "pro",
    "angebotsvergleich": "enterprise",
    "team_multiuser": "enterprise",
    "api_access": "enterprise",
}

# Features the beta unlock flag opens up. Anything NOT listed here
# (currently: the three enterprise-only features) remains gated by
# the user's real plan even during beta. This is deliberate — see
# the module docstring.
BETA_UNLOCKED_FEATURES: frozenset[str] = frozenset(
    {
        "ai_plan_analysis",
        "ai_position_generator",
        "ai_chat",
        "excel_export",
    }
)

# basis gets 3 projects max; pro/enterprise unlimited
PROJECT_LIMITS = {"basis": 3, "pro": None, "enterprise": None}

# Sentinel "unlimited-looking" number surfaced in the feature matrix
# when the beta flag is on. ``None`` already means "unlimited" in the
# existing project-limit handling, but the requested contract for the
# beta override explicitly calls for 999 so tester UIs can render a
# concrete "quota" number. Both None and 999 are accepted by every
# call site — the frontend only shows the number if it's finite and
# the backend checks against the flag directly, not the number.
BETA_PROJECT_LIMIT_SENTINEL = 999


def _beta_active() -> bool:
    """Single source of truth for the beta override.

    Wrapped in a function so tests can monkeypatch the settings object
    without having to intercept every caller."""
    return bool(settings.beta_unlock_all_features)


def has_feature(plan: str, feature: str) -> bool:
    # Beta override: all Pro-level features are unlocked regardless
    # of the user's real plan. Enterprise-only features are NOT
    # unlocked — see BETA_UNLOCKED_FEATURES.
    if _beta_active() and feature in BETA_UNLOCKED_FEATURES:
        return True
    required = FEATURE_REQUIREMENTS.get(feature)
    if required is None:
        return True  # feature not gated
    return PLAN_HIERARCHY.get(plan, 0) >= PLAN_HIERARCHY.get(required, 0)


def check_project_limit(plan: str, current_count: int) -> bool:
    # Beta override: no project limit for anyone.
    if _beta_active():
        return True
    limit = PROJECT_LIMITS.get(plan)
    if limit is None:
        return True
    return current_count < limit


def require_feature(feature: str):
    """FastAPI dependency that checks if the user's plan allows a feature."""
    async def _check(user: User = Depends(get_current_user)):
        if not has_feature(user.subscription_plan, feature):
            plan_needed = FEATURE_REQUIREMENTS[feature]
            raise HTTPException(
                status_code=403,
                detail=f"Diese Funktion erfordert den {plan_needed.title()}-Plan. Bitte upgraden Sie Ihr Abonnement.",
            )
        return user
    return _check


def get_feature_matrix(plan: str) -> dict:
    """Resolve every feature flag for the given plan.

    When the beta override is active, Pro-level features come back as
    true and the project limit is bumped to ``BETA_PROJECT_LIMIT_SENTINEL``
    so the frontend's "x / y projects used" counter can still render a
    finite number. The ``beta_unlock_active`` flag is included so the
    SPA can show its single tester banner without having to infer the
    state by comparing plan + features.
    """
    beta_on = _beta_active()
    return {
        "manual_lv_editor": True,
        "pdf_export": True,
        "ai_plan_analysis": has_feature(plan, "ai_plan_analysis"),
        "ai_position_generator": has_feature(plan, "ai_position_generator"),
        "ai_chat": has_feature(plan, "ai_chat"),
        "excel_export": has_feature(plan, "excel_export"),
        "angebotsvergleich": has_feature(plan, "angebotsvergleich"),
        "team_multiuser": has_feature(plan, "team_multiuser"),
        "api_access": has_feature(plan, "api_access"),
        "project_limit": (
            BETA_PROJECT_LIMIT_SENTINEL if beta_on else PROJECT_LIMITS.get(plan)
        ),
        "beta_unlock_active": beta_on,
    }
