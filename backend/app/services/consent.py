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
    EVENT_ANALYTICS_OPTIN_CHANGE,
    EVENT_BUSINESS_STATUS_CONFIRMED,
    EVENT_MARKETING_OPTIN_CHANGE,
    EVENT_PRIVACY_UPDATE,
    EVENT_REGISTRATION,
    EVENT_TERMS_UPDATE,
    ConsentSnapshot,
)
from app.legal_versions import (
    BUSINESS_TERMS_VERSION,
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
    analytics_consent: bool = False,
    business_terms_version: str | None = None,
    request: Request | None = None,
) -> ConsentSnapshot:
    """Write a single consent-snapshot row.

    The caller owns the surrounding transaction — we ``flush`` so
    the row is queryable inside the same session, but we never
    commit. That lets the register endpoint write the user, the
    snapshot, and the audit-event entry as a single atomic unit.

    ``analytics_consent`` (v23.8) defaults to False so older call
    sites that haven't been updated still record a truthful
    "analytics-off" snapshot. New call sites should always pass
    the user's current flag.

    ``business_terms_version`` (v24.4.8) defaults to None for call
    sites that pre-date this column — older snapshots had no notion
    of business-terms. New snapshots SHOULD pass the user's current
    value (or the freshly-accepted value for registration/refresh/
    business-status-confirmed events), so a single snapshot row
    reconstructs the user's full consent state at that moment.

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
        analytics_consent=analytics_consent,
        business_terms_version=business_terms_version,
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
        "consent.snapshot_recorded event=%s user=%s privacy=%s terms=%s "
        "marketing=%s analytics=%s business_terms=%s",
        event_type,
        user_id,
        privacy_version,
        terms_version,
        marketing_optin,
        analytics_consent,
        business_terms_version,
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
    business_terms_version: str,
    marketing_optin: bool,
    analytics_consent: bool = False,
    request: Request | None = None,
) -> ConsentSnapshot:
    """Snapshot for the initial sign-up moment. Both versions
    required — registration is the one event where neither can
    be NULL.

    ``analytics_consent`` records the user's optional opt-in (v23.8)
    so the registration snapshot is a complete picture of the
    consent state at signup — every later analytics-toggle row in
    ``consent_snapshots`` describes a *change* relative to this
    baseline.

    ``business_terms_version`` (v24.4.8) is required at registration
    — the Unternehmer-Bestätigung is a Pflicht-Checkbox in
    RegisterPage. NULL would mean the user signed up without ever
    confirming UGB-Status, which the v24.4.8+ register endpoint
    refuses.
    """
    return await record_consent(
        db,
        event_type=EVENT_REGISTRATION,
        user_id=user_id,
        privacy_version=privacy_version,
        terms_version=terms_version,
        marketing_optin=marketing_optin,
        analytics_consent=analytics_consent,
        business_terms_version=business_terms_version,
        request=request,
    )


async def record_consent_refresh(
    db: AsyncSession,
    *,
    user_id: UUID,
    privacy_version: str,
    terms_version: str,
    business_terms_version: str,
    marketing_optin: bool,
    analytics_consent: bool,
    privacy_changed: bool,
    terms_changed: bool,
    business_terms_changed: bool = False,
    request: Request | None = None,
) -> ConsentSnapshot:
    """Snapshot for the consent-refresh modal flow.

    The ``event_type`` is picked from whichever document actually
    changed since the user last accepted: ``privacy_update`` if
    the privacy policy bumped, ``terms_update`` if the terms did,
    ``business_status_confirmed`` if the UGB-Klausel bumped (or if
    the user was grandfathered and is confirming for the first time).
    If multiple changed simultaneously (rare but possible on a major
    legal review), we prefer ``privacy_update`` — privacy carries
    more user-impact in DSGVO terms.

    ``analytics_consent`` (v23.8) is captured on every refresh too
    — the modal exposes the analytics checkbox alongside the
    legal-doc acceptance, so the user might flip it during the
    same gesture.

    ``business_terms_version`` (v24.4.8) is always written into
    the snapshot so the row stays self-contained.
    """
    if privacy_changed:
        event_type = EVENT_PRIVACY_UPDATE
    elif terms_changed:
        event_type = EVENT_TERMS_UPDATE
    elif business_terms_changed:
        event_type = EVENT_BUSINESS_STATUS_CONFIRMED
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
        analytics_consent=analytics_consent,
        business_terms_version=business_terms_version,
        request=request,
    )


async def record_marketing_optin_change(
    db: AsyncSession,
    *,
    user_id: UUID,
    new_value: bool,
    analytics_consent: bool,
    business_terms_version: str | None = None,
    request: Request | None = None,
) -> ConsentSnapshot:
    """Snapshot when the user toggles their marketing-mail flag.

    Even though the legal-text versions don't change here, we
    record them so the row is self-contained — "what was the
    user's full consent state at this moment?" can be answered
    from a single snapshot without joining to the user table.

    ``business_terms_version`` (v24.4.8) — caller should pass the
    user's current value (which may be NULL for grandfathered
    pre-v24.4.8 accounts). We do NOT auto-fill with the canonical
    BUSINESS_TERMS_VERSION here — that would falsely claim the user
    had confirmed something they never confirmed.
    """
    return await record_consent(
        db,
        event_type=EVENT_MARKETING_OPTIN_CHANGE,
        user_id=user_id,
        privacy_version=PRIVACY_POLICY_VERSION,
        terms_version=TERMS_VERSION,
        marketing_optin=new_value,
        analytics_consent=analytics_consent,
        business_terms_version=business_terms_version,
        request=request,
    )


async def record_analytics_optin_change(
    db: AsyncSession,
    *,
    user_id: UUID,
    new_value: bool,
    marketing_optin: bool,
    business_terms_version: str | None = None,
    request: Request | None = None,
) -> ConsentSnapshot:
    """Snapshot when the user toggles the analytics-opt-in flag.

    v23.8 — the analytics pipeline is opt-in, and DSGVO Art. 7
    requires we can demonstrate consent (and consent withdrawal).
    Every flip — both directions — produces a row here. The
    ``new_value`` lands in both ``analytics_consent`` (the new
    state at the moment of the snapshot) and the snapshot's
    ``event_type`` (``analytics_optin_change``) so the row is
    self-describing without a join to ``users``.

    ``business_terms_version`` (v24.4.8) — same handling as in
    ``record_marketing_optin_change``: pass the user's current
    value (may be NULL for grandfathered accounts).
    """
    return await record_consent(
        db,
        event_type=EVENT_ANALYTICS_OPTIN_CHANGE,
        user_id=user_id,
        privacy_version=PRIVACY_POLICY_VERSION,
        terms_version=TERMS_VERSION,
        marketing_optin=marketing_optin,
        analytics_consent=new_value,
        business_terms_version=business_terms_version,
        request=request,
    )


async def record_business_status_confirmation(
    db: AsyncSession,
    *,
    user_id: UUID,
    business_terms_version: str,
    marketing_optin: bool,
    analytics_consent: bool,
    request: Request | None = None,
) -> ConsentSnapshot:
    """Snapshot for the Unternehmer-Bestätigung am Vertragsschluss-Moment
    (v24.4.8).

    Wird vor jedem Stripe-Checkout-Aufruf geschrieben, sodass jeder
    Kaufversuch einen frischen Beweis mit aktuellem IP/UA/Timestamp
    erzeugt. Auch wenn die User-Row schon ``current_business_terms_version
    == BUSINESS_TERMS_VERSION`` trägt (= bei Registrierung schon
    bestätigt) — Defense in Depth gegen die OGH-"hätte-wissen-müssen"-
    Linie und für die forensische Beweiskette bei einem späteren
    Stripe-Dispute.

    Privacy/Terms-Versionen werden mit den kanonischen Werten gefüllt
    (gleiches Pattern wie ``record_marketing_optin_change``) — so
    bleibt der Snapshot self-contained.
    """
    return await record_consent(
        db,
        event_type=EVENT_BUSINESS_STATUS_CONFIRMED,
        user_id=user_id,
        privacy_version=PRIVACY_POLICY_VERSION,
        terms_version=TERMS_VERSION,
        marketing_optin=marketing_optin,
        analytics_consent=analytics_consent,
        business_terms_version=business_terms_version,
        request=request,
    )


# Re-export the event-type constants so call sites only import from
# this module and don't need to know about the model file.
__all__ = (
    "EVENT_ANALYTICS_OPTIN_CHANGE",
    "EVENT_MARKETING_OPTIN_CHANGE",
    "EVENT_PRIVACY_UPDATE",
    "EVENT_REGISTRATION",
    "EVENT_TERMS_UPDATE",
    "record_analytics_optin_change",
    "record_consent",
    "record_consent_refresh",
    "record_marketing_optin_change",
    "record_registration_consent",
)
