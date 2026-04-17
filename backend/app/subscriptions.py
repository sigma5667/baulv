"""Feature gating based on subscription plans."""

from fastapi import Depends, HTTPException

from app.auth import get_current_user
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

# basis gets 3 projects max; pro/enterprise unlimited
PROJECT_LIMITS = {"basis": 3, "pro": None, "enterprise": None}


def has_feature(plan: str, feature: str) -> bool:
    required = FEATURE_REQUIREMENTS.get(feature)
    if required is None:
        return True  # feature not gated
    return PLAN_HIERARCHY.get(plan, 0) >= PLAN_HIERARCHY.get(required, 0)


def check_project_limit(plan: str, current_count: int) -> bool:
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


def get_feature_matrix(plan: str) -> dict[str, bool]:
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
        "project_limit": PROJECT_LIMITS.get(plan),
    }
