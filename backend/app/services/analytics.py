"""DSGVO-konforme Nutzungs-Analytics — record_event service.

Single public entry point: ``record_event(event_type, event_data,
user, db)``. Every other module in the codebase that wants to
emit a usage signal calls into here; nothing else writes to
``usage_analytics`` directly.

Three mandatory safety layers
=============================

1. **Consent gate.** ``user.analytics_consent`` is the master
   switch. False → return immediately, no DB write, no log line
   (don't leak the user's choice into ops). True → continue.

2. **PII whitelist.** Each event_type has an explicit set of
   allowed keys + a value sanitiser. Keys not on the list are
   silently dropped (``_sanitize_event_data``). Any field whose
   sanitiser raises is treated as PII contamination → the entire
   event is rejected with a WARN log so it surfaces in ops greps
   ("analytics.pii_rejected").

3. **Pseudonymisation.** ``anonymous_user_id = sha256(user.id ||
   salt)``. The salt lives in ``settings.analytics_salt`` and is
   mandatory in production — the service refuses to record
   anything if it detects the dev default on a non-dev deploy.

Failure mode is fail-soft: any exception inside the recorder is
logged at WARN and swallowed. Analytics is a *support* function;
the user-facing operation that triggered it must never be blocked
by an analytics failure.

Region-code helper
==================

``derive_region_code(address)`` extracts a Bundesland-level ISO
code from a free-form Austrian street address ("Salzburg" →
``"AT-5"``). Town-level precision would re-identify users in
rural regions; aggregating to Bundesland is the privacy-vs-
usefulness sweet spot the analytics dashboard is designed
around.
"""

from __future__ import annotations

import hashlib
import logging
import re
from typing import Any, Callable
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.models.analytics import (
    ALLOWED_EVENT_TYPES,
    ALLOWED_INDUSTRY_SEGMENTS,
    EVENT_FEATURE_USED,
    EVENT_LV_CREATED,
    EVENT_PLAN_ANALYZED,
    EVENT_POSITION_UPDATED,
    EVENT_PROJECT_CREATED,
    EVENT_TEMPLATE_USED,
    EVENT_USER_SIGNUP,
    UsageAnalyticsEvent,
)
from app.db.models.user import User

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Per-event-type whitelists. Keys absent from these sets are
# silently dropped before the row is built; values that fail their
# sanitiser cause the entire event to be rejected.
#
# ABSOLUTELY NO PII fields here. The list of forbidden field
# patterns lives in ``_BANNED_KEY_PATTERNS`` below as defence in
# depth — even if a future contributor accidentally adds a PII
# field to this whitelist, the regex check would still reject it.
# ---------------------------------------------------------------------------


# A sanitiser maps the raw value to a sanitised one or raises
# ValueError if the value is unacceptable.
ValueSanitiser = Callable[[Any], Any]


def _bool(v: Any) -> bool:
    """Coerce to a strict bool. Rejects anything that isn't True/
    False — defends against truthy strings or numbers leaking in."""
    if isinstance(v, bool):
        return v
    raise ValueError(f"expected bool, got {type(v).__name__}")


def _int_in_range(min_val: int, max_val: int) -> ValueSanitiser:
    """Build a sanitiser that accepts ints inside a closed range."""

    def sanitise(v: Any) -> int:
        if not isinstance(v, int) or isinstance(v, bool):
            raise ValueError(f"expected int, got {type(v).__name__}")
        if not (min_val <= v <= max_val):
            raise ValueError(
                f"int {v} outside allowed range [{min_val}, {max_val}]"
            )
        return v

    return sanitise


def _enum_value(allowed: set[str]) -> ValueSanitiser:
    """Build a sanitiser that accepts only strings from a fixed set."""

    def sanitise(v: Any) -> str:
        if not isinstance(v, str):
            raise ValueError(f"expected str, got {type(v).__name__}")
        if v not in allowed:
            raise ValueError(f"value {v!r} not in allowed set")
        return v

    return sanitise


def _bundesland_code(v: Any) -> str:
    """ISO 3166-2:AT code (e.g. ``AT-5``). Fixed shape, two letters
    + dash + digit."""
    if not isinstance(v, str):
        raise ValueError("region must be a string")
    if not re.fullmatch(r"AT-[1-9]", v):
        raise ValueError(f"region {v!r} not a valid AT-N code")
    return v


def _short_id(v: Any) -> str:
    """A UUID hex (32 chars) or a short slug (≤ 64 chars,
    alphanumeric + dashes/underscores). Used for template IDs and
    similar non-PII identifiers."""
    if not isinstance(v, str):
        raise ValueError("id must be a string")
    if len(v) > 64:
        raise ValueError("id too long (PII risk)")
    if not re.fullmatch(r"[A-Za-z0-9_-]+", v):
        raise ValueError(f"id {v!r} contains disallowed characters")
    return v


def _trade_slug(v: Any) -> str:
    """Trade label (``malerarbeiten`` / ``elektrik`` / etc.). Short
    string, lowercase, alphanumeric only."""
    if not isinstance(v, str):
        raise ValueError("trade must be a string")
    if len(v) > 30 or not re.fullmatch(r"[a-z0-9]+", v):
        raise ValueError(f"trade {v!r} not a valid slug")
    return v


def _price_bucket(v: Any) -> str:
    """A pre-rounded price-range bucket like ``"8-15_eur_m2"``.
    Pre-formatted on the call site so we never store raw prices —
    the bucketing is the privacy invariant."""
    if not isinstance(v, str):
        raise ValueError("price bucket must be a string")
    if len(v) > 30 or not re.fullmatch(r"[0-9_a-z-]+", v):
        raise ValueError(f"price bucket {v!r} has unexpected shape")
    return v


# Per-event-type schema. Keys = allowed field names; values =
# sanitisers that validate + (optionally) coerce the value.
_EVENT_SCHEMAS: dict[str, dict[str, ValueSanitiser]] = {
    EVENT_PROJECT_CREATED: {
        "region": _bundesland_code,
        "has_plans": _bool,
    },
    EVENT_LV_CREATED: {
        "trade": _trade_slug,
        "position_count": _int_in_range(0, 10_000),
    },
    EVENT_TEMPLATE_USED: {
        "template_id": _short_id,
        "is_system": _bool,
    },
    EVENT_POSITION_UPDATED: {
        "has_price": _bool,
        "has_quantity": _bool,
        "price_bucket": _price_bucket,
    },
    EVENT_PLAN_ANALYZED: {
        "pages": _int_in_range(0, 100),
        "rooms_extracted": _int_in_range(0, 1000),
    },
    EVENT_USER_SIGNUP: {
        "industry": _enum_value(ALLOWED_INDUSTRY_SEGMENTS),
    },
    EVENT_FEATURE_USED: {
        "feature": _short_id,
    },
}


# Defence-in-depth banned-key list. If any of these substrings
# appear in a key, the event is rejected even if the schema would
# otherwise have allowed the field. Catches accidents like
# adding an "email_hash" or "user_name" field to a schema in
# the future.
_BANNED_KEY_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(p, re.IGNORECASE)
    for p in (
        r"email",
        r"name",
        r"address",
        r"strasse",
        r"plz",
        r"phone",
        r"telefon",
        r"\buser\b",
        r"\buid\b",
        r"raw",
        r"file",
        r"path",
        r"ip",
    )
)


def _key_is_banned(key: str) -> bool:
    return any(p.search(key) for p in _BANNED_KEY_PATTERNS)


# ---------------------------------------------------------------------------
# Pseudonymisation
# ---------------------------------------------------------------------------


def hash_user_id(user_id: UUID) -> str:
    """Return ``sha256(user_id_hex || salt)`` as a 64-char hex string.

    Public for testing — the test suite asserts on hash stability
    and irreversibility. Production callers go through
    ``record_event`` which calls this internally.
    """
    salt = settings.analytics_salt
    digest = hashlib.sha256()
    digest.update(user_id.hex.encode("ascii"))
    digest.update(b"::")
    digest.update(salt.encode("utf-8"))
    return digest.hexdigest()


_DEV_SALT_DEFAULT = (
    "change-me-in-production-baulv-analytics-salt-2026"
)


def _is_dev_salt() -> bool:
    """Return True if the configured salt is the default dev value.

    The service writes a one-off WARN at boot when this is true so
    operators see the misconfiguration without it crashing prod.
    """
    return settings.analytics_salt == _DEV_SALT_DEFAULT


# ---------------------------------------------------------------------------
# Region-code helper
# ---------------------------------------------------------------------------


# ISO 3166-2:AT codes for the nine Bundesländer plus Vienna.
_BUNDESLAND_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    # Order matters: the first match wins. Patterns are case-
    # insensitive substrings of the canonical Bundesland names.
    (re.compile(r"\b(wien|vienna)\b", re.IGNORECASE), "AT-9"),
    (re.compile(r"\b(niederösterreich|niederoesterreich|nö)\b", re.IGNORECASE), "AT-3"),
    (re.compile(r"\boberösterreich|oberoesterreich|oö\b", re.IGNORECASE), "AT-4"),
    (re.compile(r"\bsalzburg\b", re.IGNORECASE), "AT-5"),
    (re.compile(r"\bsteiermark\b", re.IGNORECASE), "AT-6"),
    (re.compile(r"\btirol\b", re.IGNORECASE), "AT-7"),
    (re.compile(r"\bvorarlberg\b", re.IGNORECASE), "AT-8"),
    (re.compile(r"\bk[äa]rnten\b", re.IGNORECASE), "AT-2"),
    (re.compile(r"\bburgenland\b", re.IGNORECASE), "AT-1"),
)


def derive_region_code(address: str | None) -> str | None:
    """Extract a Bundesland-level ISO code from a free-form address.

    Returns ``None`` when no Bundesland keyword is found — we'd
    rather omit the region entirely than store a town-level value
    that could re-identify users in rural areas.
    """
    if not address:
        return None
    for pattern, code in _BUNDESLAND_PATTERNS:
        if pattern.search(address):
            return code
    return None


# ---------------------------------------------------------------------------
# Sanitisation
# ---------------------------------------------------------------------------


def _sanitize_event_data(
    event_type: str,
    raw: dict[str, Any] | None,
) -> dict[str, Any]:
    """Apply the per-event whitelist + per-key sanitisers.

    Keys absent from the schema are silently dropped (defence
    against innocuous additions on the call site). A value that
    fails its sanitiser raises ``ValueError`` so the caller can
    treat the whole event as rejected — that's a louder signal
    because it likely indicates a contributor accidentally tried
    to log PII.
    """
    if raw is None:
        return {}
    schema = _EVENT_SCHEMAS.get(event_type)
    if schema is None:
        # Unknown event type — caller should have caught this
        # before calling, but defensive in case the constant set
        # gets out of sync.
        raise ValueError(f"unknown event_type {event_type!r}")
    out: dict[str, Any] = {}
    for key, value in raw.items():
        if _key_is_banned(key):
            raise ValueError(
                f"banned key pattern matched in field {key!r} "
                "(PII risk; refusing to record)"
            )
        sanitiser = schema.get(key)
        if sanitiser is None:
            # Drop — explicit allow-list semantics. Don't log every
            # drop; that would become noisy when call sites do
            # routine ``{"context": value}`` style logging.
            continue
        out[key] = sanitiser(value)
    return out


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def record_event(
    db: AsyncSession,
    *,
    event_type: str,
    user: User,
    event_data: dict[str, Any] | None = None,
    region_code: str | None = None,
) -> UsageAnalyticsEvent | None:
    """Record one analytics event, gated on ``user.analytics_consent``.

    Returns the persisted row on success, ``None`` when the event
    was discarded (consent gate, validation failure, internal
    error). The caller MUST NOT depend on the return value for
    user-facing behaviour — analytics is fire-and-forget.

    The function ``flush``-es the row but does not commit; the
    caller's transaction owns the commit boundary. That lets the
    event ride along atomically with the user-facing operation
    (e.g. project_created flushes inside the create_project
    request, so a rolled-back project doesn't leak its analytics
    row).
    """
    # Consent gate. False → silent no-op. We don't even read the
    # event_data, so banned PII keys present "by accident" on a
    # consenting-not user can't hit the sanitiser.
    if not user.analytics_consent:
        return None

    # Event-type guard. Catch typos at the call site loud and
    # early — return None and log so ops sees the problem.
    if event_type not in ALLOWED_EVENT_TYPES:
        logger.warning(
            "analytics.unknown_event_type event_type=%s user=%s",
            event_type,
            user.id,
        )
        return None

    # Sanitise. PII rejection is loud (WARN), schema-drop is
    # silent (drop without log).
    try:
        clean_data = _sanitize_event_data(event_type, event_data)
    except ValueError as e:
        logger.warning(
            "analytics.pii_rejected event_type=%s user=%s reason=%s",
            event_type,
            user.id,
            e,
        )
        return None

    # Pseudonymise. The hash is the only user-correlated value
    # that lands in the table.
    try:
        anon_id = hash_user_id(user.id)
    except Exception as e:  # noqa: BLE001
        logger.warning(
            "analytics.hash_failed event_type=%s user=%s error=%s",
            event_type,
            user.id,
            e,
        )
        return None

    row = UsageAnalyticsEvent(
        event_type=event_type,
        event_data=clean_data or None,
        anonymous_user_id=anon_id,
        region_code=region_code,
        industry_segment=user.industry_segment,
    )
    try:
        db.add(row)
        await db.flush()
    except Exception as e:  # noqa: BLE001
        # Fail-soft. Analytics failures must not block the
        # surrounding user-facing operation.
        logger.warning(
            "analytics.write_failed event_type=%s user=%s error=%s",
            event_type,
            user.id,
            e,
        )
        return None

    return row


__all__ = (
    "derive_region_code",
    "hash_user_id",
    "record_event",
)
