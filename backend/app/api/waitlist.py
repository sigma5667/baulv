"""Öffentliche Warteliste mit Double-Opt-In (v25).

Vier Endpoints, drei davon unauthentifiziert:

* ``POST /api/waitlist`` — Anmeldung → ``pending`` + Bestätigungs-Mail.
* ``POST /api/waitlist/confirm`` — Double-Opt-In einlösen → ``confirmed``.
* ``POST /api/waitlist/unsubscribe`` — Abmeldung, token-basiert ohne Login.
* ``GET /api/waitlist/admin`` — Auslese, nur ``ADMIN_EMAILS``.

Warum POST statt GET für confirm/unsubscribe: die Mail-Links zeigen
auf Frontend-Seiten (``/warteliste/bestaetigen?token=…``), die den
Token erst auf Button-Klick per POST einreichen — gleiches Muster wie
der Passwort-Reset. Firmen-Mail-Scanner rufen jeden Link per GET ab;
ein GET-Confirm würde Anmeldungen "bestätigen", die nie ein Mensch
geklickt hat, und der Token bliebe in API-Access-Logs liegen.

Anti-Enumeration: ``POST /api/waitlist`` antwortet in JEDEM Zweig mit
derselben generischen Nachricht (neu / schon pending / schon
confirmed / abgemeldet) — dieselbe Haltung wie beim Passwort-Reset.
Auch der Unsubscribe verrät bei formal gültigem Token nicht, ob die
Adresse überhaupt auf der Liste steht.

Einmal-Nutzung des Confirm-Tokens entsteht über den Status-Übergang:
confirm verlangt ``status == 'pending'``; nach dem ersten Einlösen
ist die Zeile ``confirmed`` und derselbe Token läuft ins generische
400.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app import auth_rate_limit
from app.api.admin import require_admin
from app.auth_rate_limit import _client_ip
from app.config import settings
from app.db.models.user import User
from app.db.models.waitlist_entry import WaitlistEntry
from app.db.session import get_db
from app.schemas.waitlist import WaitlistSignupRequest, WaitlistTokenRequest
from app.services.email import send_waitlist_confirm_email
from app.services.waitlist import (
    WAITLIST_CONSENT_VERSION,
    hash_confirm_token,
    mint_confirm_token,
    mint_unsubscribe_token,
    resolve_unsubscribe_token,
)

logger = logging.getLogger(__name__)

router = APIRouter()

# Eine Antwort für alle Signup-Zweige. Formuliert so, dass sie auch
# für "schon bestätigt" (keine Mail verschickt) wahr bleibt.
_SIGNUP_GENERIC_MSG = (
    "Bitte prüfen Sie Ihr Postfach: Falls diese Adresse noch nicht "
    "bestätigt auf der Warteliste steht, haben wir soeben eine "
    "Bestätigungs-E-Mail geschickt."
)

# Ein 400 für alle Confirm-Fehlzustände (unbekannt / abgelaufen /
# bereits eingelöst) — kein Orakel für Token-Probing.
_CONFIRM_GENERIC_MSG = (
    "Der Link ist ungültig oder abgelaufen. Bitte tragen Sie sich "
    "erneut in die Warteliste ein."
)

_SOURCE_RE = re.compile(r"[a-z0-9_-]{1,64}")

# Neutrale Antwort der drei öffentlichen Endpoints, solange der
# Master-Schalter (``WAITLIST_ENABLED``, Default aus) nicht gesetzt
# ist. Bewusst ohne jedes Detail — kein Hinweis auf Launch-Termine,
# keine Unterscheidung nach Endpoint.
_DISABLED_MSG = "Die Warteliste ist derzeit nicht verfügbar."


def _require_waitlist_enabled() -> None:
    """v25 — Master-Schalter-Guard, ERSTE Zeile jedes öffentlichen
    Endpoints (vor Rate-Limit, vor jeder DB-Arbeit, vor jeder Mail).

    ``WAITLIST_ENABLED`` ist Default-aus (fail-safe, siehe
    ``app/config.py``): ein frischer Deploy hat die Endpoints damit
    zwar gemountet, aber inert — 503 ohne Seiteneffekt. Der Admin-
    Auslese-Endpoint läuft absichtlich NICHT über diesen Guard.
    """
    if not settings.waitlist_enabled:
        raise HTTPException(status_code=503, detail=_DISABLED_MSG)


def _sanitize_source(raw: str | None) -> str | None:
    """``?ref=``-Wert auf das Whitelist-Alphabet eindampfen.

    Ungültiges wird verworfen statt abgewiesen — ein manipulierter
    ref-Parameter darf eine echte Anmeldung nicht blockieren.
    """
    if not raw:
        return None
    candidate = raw.strip().lower()[:64]
    return candidate if _SOURCE_RE.fullmatch(candidate) else None


def _as_aware_utc(value: datetime) -> datetime:
    """SQLite (Test-Harness) liefert ``DateTime(timezone=True)`` naiv
    zurück — naive Werte als UTC interpretieren, gleiche Defensive wie
    in ``app/services/password_reset.py``."""
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


@router.post("", status_code=200)
async def join_waitlist(
    data: WaitlistSignupRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Auf die Warteliste eintragen → ``pending`` + Bestätigungs-Mail.

    Zweige hinter der konstanten Antwort:

    1. Adresse neu → Zeile anlegen, Mail senden.
    2. ``pending`` → Token neu ausstellen, Mail erneut senden (der
       zuletzt verschickte Link gewinnt; gedeckelt vom acct-Bucket).
    3. ``unsubscribed`` → Re-Opt-in: zurück auf ``pending`` mit
       frischem Token und frischen Consent-Daten; ``unsubscribed_at``
       bleibt als Historie stehen.
    4. ``confirmed`` → No-Op ohne Mail, gleiche Antwort.
    """
    _require_waitlist_enabled()

    # Aktive Einwilligung ist Backend-Pflicht — unabhängig davon, dass
    # die Checkbox im Frontend nicht vorangehakt ist.
    if data.consent is not True:
        raise HTTPException(
            status_code=422,
            detail=(
                "Bitte bestätigen Sie die Einwilligung, per E-Mail "
                "informiert zu werden."
            ),
        )

    email = str(data.email).lower().strip()
    # IP- und Adress-Bucket VOR jeder DB-Arbeit — 429 wirft.
    auth_rate_limit.enforce("waitlist", request, account=email)

    now = datetime.now(timezone.utc)
    client_ip = _client_ip(request)
    source = _sanitize_source(data.source)
    company_name = data.company_name.strip()
    name = (data.name or "").strip() or None

    result = await db.execute(
        select(WaitlistEntry).where(WaitlistEntry.email == email)
    )
    row = result.scalars().first()

    if row is not None and row.status == "confirmed":
        # Bereits bestätigt: keine Mail, keine Änderung. Die Antwort
        # bleibt identisch — Enumeration-Schutz.
        logger.info("waitlist.signup.already_confirmed email=%s", email)
        return {"message": _SIGNUP_GENERIC_MSG}

    plaintext, token_hash, expires_at = mint_confirm_token()

    if row is None:
        row = WaitlistEntry(
            email=email,
            company_name=company_name,
            name=name,
            status="pending",
            signup_at=now,
            signup_ip=client_ip,
            confirm_token_hash=token_hash,
            token_expires_at=expires_at,
            consent_text_version=WAITLIST_CONSENT_VERSION,
            source=source,
        )
        db.add(row)
        outcome = "created"
    else:
        # pending oder unsubscribed: Zeile in-place erneuern. Die
        # Signup-Daten (Zeitpunkt, IP, Consent-Version) beschreiben
        # immer die JÜNGSTE Einwilligung — die alte ist damit
        # überschrieben, ``unsubscribed_at`` bleibt als Beleg der
        # zwischenzeitlichen Abmeldung erhalten.
        row.status = "pending"
        row.company_name = company_name
        row.name = name
        row.signup_at = now
        row.signup_ip = client_ip
        row.confirm_token_hash = token_hash
        row.token_expires_at = expires_at
        row.consent_text_version = WAITLIST_CONSENT_VERSION
        row.confirmed_at = None
        row.confirmed_ip = None
        if source is not None:
            row.source = source
        outcome = "reminted"

    await db.flush()
    logger.info(
        "waitlist.signup.%s email=%s source=%s", outcome, email, source
    )

    # Fire-and-forget wie beim Passwort-Reset: ein Resend-Ausfall
    # ändert die Antwort nicht (kein Leak über die Response-Form);
    # der Operator sieht den WARN-Log der Mail-Schicht.
    send_waitlist_confirm_email(
        to_email=email,
        confirm_token=plaintext,
        unsubscribe_token=mint_unsubscribe_token(email),
        name=name,
    )
    return {"message": _SIGNUP_GENERIC_MSG}


@router.post("/confirm", status_code=200)
async def confirm_waitlist(
    data: WaitlistTokenRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Double-Opt-In einlösen: ``pending`` → ``confirmed``.

    Alle Fehlzustände (unbekannter Token, abgelaufen, bereits
    eingelöst, inzwischen abgemeldet) kollabieren in dasselbe
    generische 400.
    """
    _require_waitlist_enabled()

    auth_rate_limit.enforce("waitlist-confirm", request)

    token = data.token.strip()
    result = await db.execute(
        select(WaitlistEntry).where(
            WaitlistEntry.confirm_token_hash == hash_confirm_token(token)
        )
    )
    row = result.scalars().first()
    if row is None or row.status != "pending":
        raise HTTPException(status_code=400, detail=_CONFIRM_GENERIC_MSG)

    now = datetime.now(timezone.utc)
    if _as_aware_utc(row.token_expires_at) <= now:
        raise HTTPException(status_code=400, detail=_CONFIRM_GENERIC_MSG)

    row.status = "confirmed"
    row.confirmed_at = now
    row.confirmed_ip = _client_ip(request)
    logger.info("waitlist.confirmed email=%s", row.email)
    return {
        "message": (
            "Vielen Dank! Ihre Anmeldung ist bestätigt - wir melden "
            "uns, sobald BauLV startet."
        )
    }


@router.post("/unsubscribe", status_code=200)
async def unsubscribe_waitlist(
    data: WaitlistTokenRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Von der Warteliste abmelden — token-basiert, ohne Login.

    Akzeptiert beide Token-Arten: den Confirm-Token aus der
    Double-Opt-In-Mail (SHA-256-Lookup) und den ablauffreien
    HMAC-Abmelde-Token aus dem Mail-Footer. Idempotent — und bei
    formal gültigem Token ohne zugehörige Zeile trotzdem Erfolg,
    damit die Antwort nicht verrät, ob eine Adresse gelistet ist.
    """
    _require_waitlist_enabled()

    auth_rate_limit.enforce("waitlist-unsubscribe", request)

    token = data.token.strip()
    result = await db.execute(
        select(WaitlistEntry).where(
            WaitlistEntry.confirm_token_hash == hash_confirm_token(token)
        )
    )
    row = result.scalars().first()

    if row is None:
        email = resolve_unsubscribe_token(token)
        if email is None:
            raise HTTPException(
                status_code=400,
                detail="Der Abmelde-Link ist ungültig.",
            )
        result = await db.execute(
            select(WaitlistEntry).where(WaitlistEntry.email == email)
        )
        row = result.scalars().first()

    if row is not None and row.status != "unsubscribed":
        row.status = "unsubscribed"
        row.unsubscribed_at = datetime.now(timezone.utc)
        logger.info("waitlist.unsubscribed email=%s", row.email)

    return {
        "message": (
            "Sie wurden von der Warteliste abgemeldet und erhalten "
            "keine weiteren E-Mails."
        )
    }


@router.get("/admin")
async def list_waitlist_entries(
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Warteliste auslesen — nur ``ADMIN_EMAILS``.

    Liefert Status-Zählung plus alle Einträge (neueste zuerst). Die
    ``counts``-Trennung ist die operative Regel schlechthin: nur
    ``confirmed`` darf je Marketing-Mails bekommen.
    """
    result = await db.execute(
        select(WaitlistEntry).order_by(WaitlistEntry.signup_at.desc())
    )
    rows = result.scalars().all()

    counts = {"pending": 0, "confirmed": 0, "unsubscribed": 0}
    for row in rows:
        counts[row.status] = counts.get(row.status, 0) + 1

    return {
        "total": len(rows),
        "counts": counts,
        "entries": [
            {
                "email": row.email,
                "company_name": row.company_name,
                "name": row.name,
                "status": row.status,
                "signup_at": row.signup_at,
                "confirmed_at": row.confirmed_at,
                "unsubscribed_at": row.unsubscribed_at,
                "consent_text_version": row.consent_text_version,
                "source": row.source,
            }
            for row in rows
        ],
    }
