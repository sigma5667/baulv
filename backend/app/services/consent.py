"""Helper for writing ``consent_snapshots`` rows.

The four entry points (registration, privacy_update,
terms_update, marketing_optin_change) all share enough logic that
factoring them into one ``record_consent`` function plus thin
wrappers keeps the call sites short and prevents one variant from
silently drifting away from the rest (e.g. forgetting to capture
the IP).

DSGVO Art. 7 angle: this is the *evidence* layer. Every public
write to ``users.current_privacy_version`` /
``users.current_terms_version`` / ``users.marketing_email_opt_in``
must be paired with a snapshot. If you find a code path mutating
those columns without a corresponding ``record_consent`` call,
that's the audit-evidence gap DS-1 was meant to close — fix it
before merging.
"""

from __future__ import annotations

import logging
from uuid import UUID

from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.consent import (
    EVENT_MARKETING_OPTIN_CHANGE,
    EVENT_PRIVACY_UPDATE,
    EVENT_REGISTRATION,
    EVENT_TERMS_UPDATE,
    ConsentSnapshot,
)
from app.legal_versions import (
    PRIVACY_POLICY_VERSION,
    TERMS_VERSION,
)

logger = logging.getLogger(__name__)


def _client_ip(request: Request | None) -> str | None:
    """Same XFF-aware extraction as ``app.services.audit._client_ip``.

    Duplicated locally so this module stays import-light — no need
    to pull in the audit-event helper just to read a header.
    """
    if request is None:
        return None
    fwd = request.headers.get("x-forwarded-for")
    if fwd:
        return fwd.split(",")[0].strip()
    if request.client:
        return request.client.host
    return None


def _user_agent(request: Request | None) -> str | None:
    if request is None:
        return None
    ua = request.headers.get("user-agent")
    if ua is None:
        return None
    # Trim to column size; some bots send kilobyte-long UAs.
    return ua[:500]


async def record_consent(
    db: AsyncSession,
    *,
    event_type: str,
    user_id: UUID | None,
    privacy_version: str | None,
    terms_version: str | None,
    marketing_optin: bool,
    request: Request | None = None,
) -> ConsentSnapshot:
    """Write a single consent-snapshot row.

    The caller owns the surrounding transaction — we ``flush`` so
    the row is queryable inside the same session, but we never
    commit. That lets the register endpoint write the user, the
    snapshot, and the audit-event entry as a single atomic unit.

    On internal failure we log and re-raise — losing a consent
    snapshot must NOT silently succeed. This is the difference
    between consent-evidence (must not be lost) and the audit
    log (best-effort by design); they look similar but their
    failure-modes are mirror images.
    """
    snapshot = ConsentSnapshot(
        user_id=user_id,
        event_type=event_type,
        privacy_version=privacy_version,
        terms_version=terms_version,
        marketing_optin=marketing_optin,
        ip_address=_client_ip(request),
        user_agent=_user_agent(request),
    )
    db.add(snapshot)
    try:
        await db.flush()
    except Exception:
        logger.exception(
            "consent.snapshot_write_failed event=%s user=%s",
            event_type, user_id,
        )
        raise
    logger.info(
        "consent.snapshot_recorded event=%s user=%s privacy=%s terms=%s marketing=%s",
        event_type, user_id, privacy_version, terms_version, marketing_optin,
    )
    return snapshot


# ---------------------------------------------------------------------------
# Convenience wrappers — one per event_type, so call sites read like
# ``await record_registration_consent(...)`` instead of stuffing the
# event_type string in by hand on every invocation.
# ---------------------------------------------------------------------------


async def record_registration_consent(
    db: AsyncSession,
    *,
    user_id: UUID,
    privacy_version: str,
    terms_version: str,
    marketing_optin: bool,
    request: Request | None = None,
) -> ConsentSnapshot:
    """Snapshot for the initial sign-up moment. Both versions
    required — registration is the one event where neither can
    be NULL."""
    return await record_consent(
        db,
        event_type=EVENT_REGISTRATION,
        user_id=user_id,
        privacy_version=privacy_version,
        terms_version=terms_version,
        marketing_optin=marketing_optin,
        request=request,
    )


async def record_consent_refresh(
    db: AsyncSession,
    *,
    user_id: UUID,
    privacy_version: str,
    terms_version: str,
    marketing_optin: bool,
    privacy_changed: bool,
    terms_changed: bool,
    request: Request | None = None,
) -> ConsentSnapshot:
    """Snapshot for the consent-refresh modal flow.

    The ``event_type`` is picked from whichever document actually
    changed since the user last accepted: ``privacy_update`` if
    the privacy policy bumped, ``terms_update`` if the terms did.
    If both changed simultaneously (rare but possible on a major
    legal review), we use ``privacy_update`` — privacy carries
    more user-impact in DSGVO terms, so it gets the dominant tag.
    """
    if privacy_changed:
        event_type = EVENT_PRIVACY_UPDATE
    elif terms_changed:
        event_type = EVENT_TERMS_UPDATE
    else:
        # Caller shouldn't have asked to refresh if nothing
        # changed, but log and fall through with privacy_update
        # rather than crashing.
        logger.warning(
            "consent.refresh_with_no_change user=%s — using privacy_update tag",
            user_id,
        )
        event_type = EVENT_PRIVACY_UPDATE
    return await record_consent(
        db,
        event_type=event_type,
        user_id=user_id,
        privacy_version=privacy_version,
        terms_version=terms_version,
        marketing_optin=marketing_optin,
        request=request,
    )


async def record_marketing_optin_change(
    db: AsyncSession,
    *,
    user_id: UUID,
    new_value: bool,
    request: Request | None = None,
) -> ConsentSnapshot:
    """Snapshot when the user toggles their marketing-mail flag.

    Even though the legal-text versions don't change here, we
    record them so the row is self-contained — "what was the
    user's full consent state at this moment?" can be answered
    from a single snapshot without joining to the user table.
    """
    return await record_consent(
        db,
        event_type=EVENT_MARKETING_OPTIN_CHANGE,
        user_id=user_id,
        privacy_version=PRIVACY_POLICY_VERSION,
        terms_version=TERMS_VERSION,
        marketing_optin=new_value,
        request=request,
    )


# Re-export the event-type constants so call sites only import from
# this module and don't need to know about the model file.
__all__ = (
    "EVENT_MARKETING_OPTIN_CHANGE",
    "EVENT_PRIVACY_UPDATE",
    "EVENT_REGISTRATION",
    "EVENT_TERMS_UPDATE",
    "record_consent",
    "record_consent_refresh",
    "record_marketing_optin_change",
    "record_registration_consent",
)
