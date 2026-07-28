"""Tests für die v25-Warteliste (Double-Opt-In).

Abdeckung entlang der Spec:

  1. Signup → ``pending``-Zeile mit gehashtem Token + Consent-Pin,
     Bestätigungs-Mail mit dem KLARTEXT-Token (Resend gepatcht).
  2. Consent-Checkbox nicht gesetzt → 422, keine Zeile.
  3. Doppelte Anmeldung → identische generische Antwort, EINE Zeile,
     Token neu ausgestellt (der letzte Link gewinnt).
  4. Bereits ``confirmed`` → gleiche Antwort, aber KEINE Mail und
     keine Zustandsänderung (Anti-Enumeration + kein Mail-Spam).
  5. acct-Rate-Limit: 4. Anmeldung derselben Adresse in der Stunde
     → 429.
  6. Confirm: Happy Path, abgelaufener Token, Doppel-Einlösung.
  7. Unsubscribe über beide Token-Arten (Confirm-Token + HMAC),
     kaputter Token → 400, Idempotenz.
  8. Re-Opt-in nach Abmeldung → wieder ``pending``,
     ``unsubscribed_at`` bleibt als Historie.
  9. ``?ref=``-Sanitisierung (Whitelist-Alphabet).
 10. Admin-Gate: leere Allow-List → 403.
 11. Nightly-Cleanup löscht nur lang-abgelaufene ``pending``-Zeilen.
 12. Master-Schalter (``WAITLIST_ENABLED``, Default AUS): alle drei
     öffentlichen Endpoints → neutrales 503, keine Mail, kein
     DB-Write. Die Funktions-Tests oben laufen über eine autouse-
     Fixture mit eingeschaltetem Schalter.
 13. Boot-Guard: Schalter EIN + Platzhalter-Firmendaten
     (``email_footer._COMPANY_DATA_IS_PLACEHOLDER``) → RuntimeError
     beim App-Start; Verdrahtung in ``main.lifespan`` gepinnt.

Wie ``test_password_reset.py`` rufen die Tests die Endpoint-
Funktionen direkt mit der ``db_session``-Fixture auf (In-Memory-
SQLite aus dem conftest) statt die FastAPI-App zu starten.
"""

from __future__ import annotations

import re
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app import auth_rate_limit
from app.api import waitlist as waitlist_api
from app.api.admin import require_admin
from app.api.waitlist import (
    confirm_waitlist,
    join_waitlist,
    list_waitlist_entries,
    send_waitlist_update,
    unsubscribe_waitlist,
)
from app.config import Settings, settings
from app.schemas.waitlist import WaitlistUpdateSendRequest
from app.services import email_footer
from app.services.email import render_waitlist_update_bodies
from app.services.email_footer import assert_company_data_ready_for_waitlist
from app.db.models.user import User
from app.db.models.waitlist_entry import WaitlistEntry
from app.services.audit_cleanup import cleanup_stale_waitlist_pending
from app.schemas.waitlist import WaitlistSignupRequest, WaitlistTokenRequest
from app.services.waitlist import (
    WAITLIST_CONSENT_TEXT,
    WAITLIST_CONSENT_VERSION,
    hash_confirm_token,
    mint_confirm_token,
    mint_unsubscribe_token,
    resolve_unsubscribe_token,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


_SEND_PATCH = "app.api.waitlist.send_waitlist_confirm_email"
_UPDATE_SEND_PATCH = "app.api.waitlist.send_waitlist_update_email"


@pytest.fixture(autouse=True)
def _reset_rate_limits():
    """Die Sliding-Window-Buckets sind prozess-global — ohne Reset
    würde der IP-Bucket ("unknown") über die Testdatei hinweg
    volllaufen und späteren Tests falsche 429er bescheren."""
    auth_rate_limit.reset_for_tests()
    yield
    auth_rate_limit.reset_for_tests()


@pytest.fixture(autouse=True)
def _waitlist_on(monkeypatch):
    """v25 — der Master-Schalter ist Default-AUS (fail-safe). Die
    Funktions-Tests dieser Datei prüfen das EINGESCHALTETE Verhalten,
    also hier global einschalten; die Schalter-Tests unten drehen ihn
    pro Test wieder ab. monkeypatch stellt den Default nach jedem
    Test zurück."""
    monkeypatch.setattr(settings, "waitlist_enabled", True)


def _mock_request():
    """Stand-in FastAPI Request — ``enforce`` liest ``headers`` und
    ``client``, mehr braucht der Waitlist-Pfad nicht."""
    request = MagicMock()
    request.headers = {}
    request.client = None
    return request


def _signup(
    email: str = "bau@example.at",
    *,
    company: str = "Muster Bau GmbH",
    name: str | None = "Maria Muster",
    consent: bool = True,
    source: str | None = None,
) -> WaitlistSignupRequest:
    return WaitlistSignupRequest(
        email=email,
        company_name=company,
        name=name,
        consent=consent,
        source=source,
    )


async def _get_entry(db: AsyncSession, email: str) -> WaitlistEntry | None:
    result = await db.execute(
        select(WaitlistEntry).where(WaitlistEntry.email == email)
    )
    return result.scalars().first()


async def _seed_entry(
    db: AsyncSession,
    *,
    email: str = "bau@example.at",
    status: str = "pending",
    token_plaintext: str | None = None,
    expires_delta: timedelta = timedelta(days=7),
    source: str | None = None,
    signup_at: datetime | None = None,
) -> tuple[WaitlistEntry, str]:
    """Direkt eine Zeile einpflanzen; Returns ``(row, plaintext)``."""
    plaintext = token_plaintext or mint_confirm_token()[0]
    row = WaitlistEntry(
        id=uuid.uuid4(),
        email=email,
        company_name="Muster Bau GmbH",
        name=None,
        status=status,
        signup_at=signup_at or datetime.now(timezone.utc),
        signup_ip="unknown",
        confirm_token_hash=hash_confirm_token(plaintext),
        token_expires_at=datetime.now(timezone.utc) + expires_delta,
        consent_text_version=WAITLIST_CONSENT_VERSION,
        source=source,
    )
    db.add(row)
    await db.commit()
    return row, plaintext


# ---------------------------------------------------------------------------
# 1. Signup — Happy Path
# ---------------------------------------------------------------------------


async def test_signup_creates_pending_row_and_sends_mail(
    db_session: AsyncSession,
):
    with patch(_SEND_PATCH) as mock_send:
        mock_send.return_value = True
        response = await join_waitlist(
            _signup("Neu@Example.AT"), _mock_request(), db=db_session
        )
        await db_session.commit()

    # Generische Antwort, lowercase-normalisierte Zeile.
    assert "Postfach" in response["message"]
    row = await _get_entry(db_session, "neu@example.at")
    assert row is not None
    assert row.status == "pending"
    assert row.company_name == "Muster Bau GmbH"
    assert row.consent_text_version == WAITLIST_CONSENT_VERSION
    # 7-Tage-TTL. SQLite gibt tz-aware Spalten naiv zurück → als UTC
    # deuten (gleiche Defensive wie im Produktionscode).
    expires = row.token_expires_at
    if expires.tzinfo is None:
        expires = expires.replace(tzinfo=timezone.utc)
    assert expires > datetime.now(timezone.utc) + timedelta(days=6)

    # Mail einmal, mit dem Klartext-Token, dessen Hash in der DB liegt.
    assert mock_send.call_count == 1
    kwargs = mock_send.call_args.kwargs
    assert kwargs["to_email"] == "neu@example.at"
    assert hash_confirm_token(kwargs["confirm_token"]) == (
        row.confirm_token_hash
    )
    # Klartext erreicht die DB nie.
    assert kwargs["confirm_token"] != row.confirm_token_hash


# ---------------------------------------------------------------------------
# 2. Consent ist Pflicht
# ---------------------------------------------------------------------------


async def test_signup_without_consent_is_422_and_writes_nothing(
    db_session: AsyncSession,
):
    with patch(_SEND_PATCH) as mock_send:
        with pytest.raises(HTTPException) as exc_info:
            await join_waitlist(
                _signup(consent=False), _mock_request(), db=db_session
            )

    assert exc_info.value.status_code == 422
    assert mock_send.call_count == 0
    assert await _get_entry(db_session, "bau@example.at") is None


# ---------------------------------------------------------------------------
# 3./4. Wiederholte Anmeldung — keine Enumeration
# ---------------------------------------------------------------------------


async def test_duplicate_signup_same_response_reminted_token(
    db_session: AsyncSession,
):
    with patch(_SEND_PATCH) as mock_send:
        mock_send.return_value = True
        first = await join_waitlist(
            _signup(), _mock_request(), db=db_session
        )
        await db_session.commit()
        row = await _get_entry(db_session, "bau@example.at")
        first_hash = row.confirm_token_hash

        second = await join_waitlist(
            _signup(), _mock_request(), db=db_session
        )
        await db_session.commit()

    # Identische Antwort in beiden Zweigen, weiterhin EINE Zeile,
    # aber frischer Token — der zuletzt verschickte Link gewinnt.
    assert first["message"] == second["message"]
    rows = (
        (await db_session.execute(select(WaitlistEntry))).scalars().all()
    )
    assert len(rows) == 1
    assert rows[0].confirm_token_hash != first_hash
    assert mock_send.call_count == 2


async def test_signup_when_confirmed_sends_no_mail_and_keeps_state(
    db_session: AsyncSession,
):
    row, _ = await _seed_entry(db_session, status="confirmed")
    old_hash = row.confirm_token_hash

    with patch(_SEND_PATCH) as mock_send:
        response = await join_waitlist(
            _signup(), _mock_request(), db=db_session
        )
        await db_session.commit()

    assert "Postfach" in response["message"]
    assert mock_send.call_count == 0
    refreshed = await _get_entry(db_session, "bau@example.at")
    assert refreshed.status == "confirmed"
    assert refreshed.confirm_token_hash == old_hash


# ---------------------------------------------------------------------------
# 5. Rate-Limit pro Adresse
# ---------------------------------------------------------------------------


async def test_fourth_signup_for_same_email_is_rate_limited(
    db_session: AsyncSession,
):
    with patch(_SEND_PATCH) as mock_send:
        mock_send.return_value = True
        for _ in range(3):
            await join_waitlist(_signup(), _mock_request(), db=db_session)
            await db_session.commit()

        with pytest.raises(HTTPException) as exc_info:
            await join_waitlist(_signup(), _mock_request(), db=db_session)

    assert exc_info.value.status_code == 429
    assert mock_send.call_count == 3


# ---------------------------------------------------------------------------
# 6. Confirm
# ---------------------------------------------------------------------------


async def test_confirm_happy_path_sets_confirmed(db_session: AsyncSession):
    _, plaintext = await _seed_entry(db_session)

    response = await confirm_waitlist(
        WaitlistTokenRequest(token=plaintext),
        _mock_request(),
        db=db_session,
    )
    await db_session.commit()

    assert "bestätigt" in response["message"]
    row = await _get_entry(db_session, "bau@example.at")
    assert row.status == "confirmed"
    assert row.confirmed_at is not None


async def test_confirm_expired_token_is_400_and_stays_pending(
    db_session: AsyncSession,
):
    _, plaintext = await _seed_entry(
        db_session, expires_delta=timedelta(days=-1)
    )

    with pytest.raises(HTTPException) as exc_info:
        await confirm_waitlist(
            WaitlistTokenRequest(token=plaintext),
            _mock_request(),
            db=db_session,
        )

    assert exc_info.value.status_code == 400
    row = await _get_entry(db_session, "bau@example.at")
    assert row.status == "pending"


async def test_confirm_is_single_use(db_session: AsyncSession):
    _, plaintext = await _seed_entry(db_session)

    await confirm_waitlist(
        WaitlistTokenRequest(token=plaintext),
        _mock_request(),
        db=db_session,
    )
    await db_session.commit()

    with pytest.raises(HTTPException) as exc_info:
        await confirm_waitlist(
            WaitlistTokenRequest(token=plaintext),
            _mock_request(),
            db=db_session,
        )
    assert exc_info.value.status_code == 400


# ---------------------------------------------------------------------------
# 7. Unsubscribe — beide Token-Arten
# ---------------------------------------------------------------------------


async def test_unsubscribe_via_confirm_token(db_session: AsyncSession):
    _, plaintext = await _seed_entry(db_session, status="confirmed")

    response = await unsubscribe_waitlist(
        WaitlistTokenRequest(token=plaintext),
        _mock_request(),
        db=db_session,
    )
    await db_session.commit()

    assert "abgemeldet" in response["message"]
    row = await _get_entry(db_session, "bau@example.at")
    assert row.status == "unsubscribed"
    assert row.unsubscribed_at is not None


async def test_unsubscribe_via_hmac_token_roundtrip(
    db_session: AsyncSession,
):
    await _seed_entry(db_session, status="confirmed")
    token = mint_unsubscribe_token("bau@example.at")

    # Der HMAC-Token trägt seine E-Mail selbst und ist reproduzierbar.
    assert resolve_unsubscribe_token(token) == "bau@example.at"

    await unsubscribe_waitlist(
        WaitlistTokenRequest(token=token), _mock_request(), db=db_session
    )
    await db_session.commit()

    row = await _get_entry(db_session, "bau@example.at")
    assert row.status == "unsubscribed"


async def test_unsubscribe_with_garbage_token_is_400(
    db_session: AsyncSession,
):
    with pytest.raises(HTTPException) as exc_info:
        await unsubscribe_waitlist(
            WaitlistTokenRequest(token="kaputt.nicht-hex"),
            _mock_request(),
            db=db_session,
        )
    assert exc_info.value.status_code == 400


async def test_tampered_hmac_token_resolves_to_none():
    token = mint_unsubscribe_token("bau@example.at")
    payload, _, _sig = token.partition(".")
    assert resolve_unsubscribe_token(payload + "." + "0" * 64) is None


async def test_unsubscribe_valid_hmac_for_unknown_email_is_generic_ok(
    db_session: AsyncSession,
):
    """Formal gültiger Token ohne Zeile → trotzdem Erfolg, damit die
    Antwort nicht verrät, ob die Adresse gelistet ist."""
    token = mint_unsubscribe_token("niemand@example.at")
    response = await unsubscribe_waitlist(
        WaitlistTokenRequest(token=token), _mock_request(), db=db_session
    )
    assert "abgemeldet" in response["message"]


# ---------------------------------------------------------------------------
# 8. Re-Opt-in nach Abmeldung
# ---------------------------------------------------------------------------


async def test_resignup_after_unsubscribe_reopts_in(
    db_session: AsyncSession,
):
    row, _ = await _seed_entry(db_session, status="unsubscribed")
    row.unsubscribed_at = datetime.now(timezone.utc)
    await db_session.commit()

    with patch(_SEND_PATCH) as mock_send:
        mock_send.return_value = True
        await join_waitlist(_signup(), _mock_request(), db=db_session)
        await db_session.commit()

    refreshed = await _get_entry(db_session, "bau@example.at")
    assert refreshed.status == "pending"
    # Historie der Abmeldung bleibt stehen (Art.-7-Nachweis).
    assert refreshed.unsubscribed_at is not None
    assert mock_send.call_count == 1


# ---------------------------------------------------------------------------
# 9. Source-Sanitisierung
# ---------------------------------------------------------------------------


async def test_source_is_whitelisted(db_session: AsyncSession):
    with patch(_SEND_PATCH) as mock_send:
        mock_send.return_value = True
        await join_waitlist(
            _signup("gut@example.at", source="insta_juli-2026"),
            _mock_request(),
            db=db_session,
        )
        await join_waitlist(
            _signup("boese@example.at", source="Sommer Aktion!<script>"),
            _mock_request(),
            db=db_session,
        )
        await db_session.commit()

    assert (await _get_entry(db_session, "gut@example.at")).source == (
        "insta_juli-2026"
    )
    assert (await _get_entry(db_session, "boese@example.at")).source is None


# ---------------------------------------------------------------------------
# 10. Admin-Gate
# ---------------------------------------------------------------------------


async def test_admin_gate_fails_closed_without_allowlist():
    """``ADMIN_EMAILS`` leer (Test-Default) → 403, egal wer fragt.
    Der Auslese-Endpoint hängt komplett hinter ``require_admin``."""
    user = User(
        id=uuid.uuid4(),
        email="wer-auch-immer@example.at",
        password_hash="x",
        full_name="X",
    )
    with pytest.raises(HTTPException) as exc_info:
        await require_admin(user)
    assert exc_info.value.status_code == 403


async def test_admin_listing_counts_by_status(db_session: AsyncSession):
    await _seed_entry(db_session, email="a@example.at", status="pending")
    await _seed_entry(db_session, email="b@example.at", status="confirmed")
    await _seed_entry(
        db_session, email="c@example.at", status="unsubscribed"
    )

    admin = User(
        id=uuid.uuid4(),
        email="chef@baulv.at",
        password_hash="x",
        full_name="Chef",
    )
    # require_admin ist als Depends verdrahtet und oben separat
    # getestet — hier interessiert die Auslese-Form.
    result = await list_waitlist_entries(admin=admin, db=db_session)

    assert result["total"] == 3
    assert result["counts"] == {
        "pending": 1,
        "confirmed": 1,
        "unsubscribed": 1,
    }
    assert {e["email"] for e in result["entries"]} == {
        "a@example.at",
        "b@example.at",
        "c@example.at",
    }


# ---------------------------------------------------------------------------
# 11. Nightly-Cleanup
# ---------------------------------------------------------------------------


async def test_cleanup_deletes_only_long_expired_pending(
    db_session: AsyncSession,
):
    # 40 Tage abgelaufen → weg. Frisch-pending und alt-confirmed → bleiben.
    await _seed_entry(
        db_session,
        email="stale@example.at",
        status="pending",
        expires_delta=timedelta(days=-40),
    )
    await _seed_entry(
        db_session, email="frisch@example.at", status="pending"
    )
    await _seed_entry(
        db_session,
        email="dabei@example.at",
        status="confirmed",
        expires_delta=timedelta(days=-40),
    )

    deleted = await cleanup_stale_waitlist_pending(db_session)
    await db_session.commit()

    assert deleted == 1
    assert await _get_entry(db_session, "stale@example.at") is None
    assert await _get_entry(db_session, "frisch@example.at") is not None
    assert await _get_entry(db_session, "dabei@example.at") is not None


# ---------------------------------------------------------------------------
# 12. Master-Schalter (WAITLIST_ENABLED) — Default AUS, 503 ohne
#     Seiteneffekt
# ---------------------------------------------------------------------------


def test_waitlist_switch_defaults_to_off():
    """Der fail-safe-Anker: ein frischer Deploy OHNE gesetzte Env-
    Variable hat die Warteliste aus. Gegen die Felddefinition gepinnt
    (nicht gegen eine Settings()-Instanz, die .env-Werte einliest)."""
    assert Settings.model_fields["waitlist_enabled"].default is False


async def test_signup_disabled_is_503_without_mail_or_db_write(
    db_session: AsyncSession, monkeypatch,
):
    """Schalter AUS → neutrales 503, ``send_waitlist_confirm_email``
    wird NIE aufgerufen, keine Zeile entsteht."""
    monkeypatch.setattr(settings, "waitlist_enabled", False)

    with patch(_SEND_PATCH) as send_mock:
        with pytest.raises(HTTPException) as excinfo:
            await join_waitlist(_signup(), _mock_request(), db_session)

    assert excinfo.value.status_code == 503
    assert "nicht verfügbar" in excinfo.value.detail
    send_mock.assert_not_called()
    assert await _get_entry(db_session, "bau@example.at") is None


async def test_confirm_disabled_is_503_and_row_untouched(
    db_session: AsyncSession, monkeypatch,
):
    """Auch ein GÜLTIGER Token löst bei Schalter AUS nichts aus —
    die Zeile bleibt ``pending``."""
    row, plaintext = await _seed_entry(db_session)
    monkeypatch.setattr(settings, "waitlist_enabled", False)

    with pytest.raises(HTTPException) as excinfo:
        await confirm_waitlist(
            WaitlistTokenRequest(token=plaintext), _mock_request(), db_session
        )

    assert excinfo.value.status_code == 503
    assert "nicht verfügbar" in excinfo.value.detail
    await db_session.refresh(row)
    assert row.status == "pending"
    assert row.confirmed_at is None


async def test_unsubscribe_disabled_is_503_and_row_untouched(
    db_session: AsyncSession, monkeypatch,
):
    row, plaintext = await _seed_entry(db_session)
    monkeypatch.setattr(settings, "waitlist_enabled", False)

    with pytest.raises(HTTPException) as excinfo:
        await unsubscribe_waitlist(
            WaitlistTokenRequest(token=plaintext), _mock_request(), db_session
        )

    assert excinfo.value.status_code == 503
    await db_session.refresh(row)
    assert row.status == "pending"
    assert row.unsubscribed_at is None


async def test_admin_listing_works_while_switch_is_off(
    db_session: AsyncSession, monkeypatch,
):
    """Der Admin-Auslese-Endpoint hängt absichtlich NICHT am
    Schalter — Bestandsdaten einsehen muss auch bei geschlossener
    Liste gehen (require_admin bleibt die Zugangs-Hürde)."""
    await _seed_entry(db_session, email="dabei@example.at", status="confirmed")
    monkeypatch.setattr(settings, "waitlist_enabled", False)

    admin = User(email="admin@baulv.at")
    result = await list_waitlist_entries(admin=admin, db=db_session)

    assert result["total"] == 1


# ---------------------------------------------------------------------------
# 13. Boot-Guard — Platzhalter-Wachhund (§ 14 UGB)
# ---------------------------------------------------------------------------


def test_boot_guard_blocks_enabled_waitlist_while_placeholder():
    """Der Wachhund selbst: solange die COMPANY_*-Werte Platzhalter
    sind, bricht ``WAITLIST_ENABLED=true`` den App-Start hart ab.
    Pinnt zugleich, dass das Flag aktuell wirklich noch steht — wer
    die Firmendaten füllt, stellt Flag UND diesen Zustand mit um."""
    assert email_footer._COMPANY_DATA_IS_PLACEHOLDER is True

    with pytest.raises(RuntimeError, match="Platzhalter"):
        assert_company_data_ready_for_waitlist(True)


def test_boot_guard_silent_while_waitlist_off():
    """Schalter AUS + Platzhalter ist der dokumentierte Zustand vor
    der Gründung — kein Raise, der Deploy läuft normal."""
    assert_company_data_ready_for_waitlist(False)


def test_boot_guard_silent_once_company_data_filled(monkeypatch):
    monkeypatch.setattr(email_footer, "_COMPANY_DATA_IS_PLACEHOLDER", False)

    assert_company_data_ready_for_waitlist(True)


def test_boot_guard_is_wired_into_app_startup():
    """Verdrahtungs-Pin (gleiche Technik wie
    test_pipeline_diagnostics): der lifespan-Hook in app/main.py muss
    den Guard aufrufen, sonst wäre er toter Code."""
    import inspect

    from app import main

    source = inspect.getsource(main.lifespan)
    assert "assert_company_data_ready_for_waitlist" in source
    assert "settings.waitlist_enabled" in source


# ---------------------------------------------------------------------------
# 14. Consent v1.1 — Wortlaut-Sync Backend <-> Landing-Checkbox
# ---------------------------------------------------------------------------


def test_consent_version_is_bumped_to_1_1():
    """v1.1 (2026-07-24): Zweck um Entwicklungs-Updates erweitert.
    Gepinnt, damit ein kuenftiger Wortlaut-Edit den Versions-Bump
    nicht vergisst (Spalte ``consent_text_version`` ist der
    Art.-7-Nachweis)."""
    assert WAITLIST_CONSENT_VERSION == "1.1"
    assert "Entwicklungsstand" in WAITLIST_CONSENT_TEXT
    assert "widerrufen" in WAITLIST_CONSENT_TEXT


def test_consent_text_matches_landing_page_checkbox():
    """Die Zwei-Stellen-Regel, maschinell: der Checkbox-Wortlaut in
    ``LandingPage.tsx`` muss zeichengleich mit der Backend-Konstante
    sein. Der Test parst die TSX-Konstante (String-Fragmente einer
    String-Verkettung) und vergleicht den zusammengesetzten Text."""
    repo_root = Path(__file__).resolve().parents[3]
    tsx_path = repo_root / "frontend" / "src" / "pages" / "LandingPage.tsx"
    tsx = tsx_path.read_text(encoding="utf-8")

    marker = "const WAITLIST_CONSENT_TEXT ="
    assert marker in tsx, "Konstante in LandingPage.tsx nicht gefunden"
    segment = tsx.split(marker, 1)[1].split(";", 1)[0]
    fragments = re.findall(r'"([^"]*)"', segment)
    assert "".join(fragments) == WAITLIST_CONSENT_TEXT


# ---------------------------------------------------------------------------
# 15. Admin-Uebersicht (v25.1) — Quellen, Verlauf, Pagination
# ---------------------------------------------------------------------------


async def test_admin_overview_sources_and_recent_counts(
    db_session: AsyncSession,
):
    now = datetime.now(timezone.utc)
    await _seed_entry(
        db_session, email="neu@example.at", status="confirmed",
        source="google", signup_at=now,
    )
    await _seed_entry(
        db_session, email="aelter@example.at", status="pending",
        source="google", signup_at=now - timedelta(days=10),
    )
    await _seed_entry(
        db_session, email="alt@example.at", status="unsubscribed",
        source=None, signup_at=now - timedelta(days=40),
    )
    admin = User(email="admin@baulv.at")

    result = await list_waitlist_entries(admin=admin, db=db_session)

    assert result["total"] == 3
    assert result["counts"] == {
        "pending": 1, "confirmed": 1, "unsubscribed": 1,
    }
    assert {"source": "google", "count": 2} in result["sources"]
    assert {"source": None, "count": 1} in result["sources"]
    assert result["signups_last_7d"] == 1
    assert result["signups_last_30d"] == 2


async def test_admin_overview_pagination(db_session: AsyncSession):
    now = datetime.now(timezone.utc)
    for i in range(3):
        await _seed_entry(
            db_session, email=f"p{i}@example.at", status="pending",
            signup_at=now - timedelta(days=i),
        )
    admin = User(email="admin@baulv.at")

    page = await list_waitlist_entries(
        admin=admin, db=db_session, limit=1, offset=1,
    )

    # Aggregat-Zahlen bleiben Gesamt-Zahlen, nur ``entries`` ist
    # paginiert: Seite 2 bei Groesse 1 = der zweitneueste Eintrag.
    assert page["total"] == 3
    assert len(page["entries"]) == 1
    assert page["entries"][0]["email"] == "p1@example.at"
    assert page["limit"] == 1 and page["offset"] == 1


# ---------------------------------------------------------------------------
# 16. Update-Versand (v25.1) — nur confirmed, einzeln, Trockenlauf
# ---------------------------------------------------------------------------


async def test_update_dry_run_counts_confirmed_and_sends_nothing(
    db_session: AsyncSession,
):
    await _seed_entry(db_session, email="a@example.at", status="confirmed")
    await _seed_entry(db_session, email="b@example.at", status="confirmed")
    await _seed_entry(db_session, email="c@example.at", status="pending")
    await _seed_entry(db_session, email="d@example.at", status="unsubscribed")
    admin = User(email="admin@baulv.at")

    with patch(_UPDATE_SEND_PATCH) as send_mock:
        result = await send_waitlist_update(
            WaitlistUpdateSendRequest(
                subject="BauLV-Update Juli", body="Es geht voran.",
                dry_run=True,
            ),
            admin=admin, db=db_session,
        )

    assert result["dry_run"] is True
    assert result["recipients"] == 2
    assert result["sent"] == 0 and result["failed"] == 0
    send_mock.assert_not_called()
    assert result["preview"]["subject"] == "BauLV-Update Juli"
    assert "Es geht voran." in result["preview"]["text"]
    # Die Vorschau zeigt den Abmelde-Link — er ist Pflichtteil jeder
    # Update-Mail.
    assert "/warteliste/abmelden?token=" in result["preview"]["text"]


async def test_update_send_only_confirmed_each_with_own_token(
    db_session: AsyncSession, monkeypatch,
):
    monkeypatch.setattr(waitlist_api, "_UPDATE_SEND_PACE_S", 0)
    await _seed_entry(db_session, email="a@example.at", status="confirmed")
    await _seed_entry(db_session, email="b@example.at", status="confirmed")
    await _seed_entry(db_session, email="c@example.at", status="pending")
    admin = User(email="admin@baulv.at")

    with patch(_UPDATE_SEND_PATCH, return_value=True) as send_mock:
        result = await send_waitlist_update(
            WaitlistUpdateSendRequest(subject="Update", body="Text."),
            admin=admin, db=db_session,
        )

    assert result == {
        "dry_run": False, "recipients": 2, "sent": 2, "failed": 0,
    }
    assert send_mock.call_count == 2
    sent_to = {c.kwargs["to_email"] for c in send_mock.call_args_list}
    assert sent_to == {"a@example.at", "b@example.at"}
    # Einzeln pro Empfaenger, jeder mit SEINEM deterministischen
    # HMAC-Abmelde-Token — kein BCC, kein geteilter Link.
    for call in send_mock.call_args_list:
        assert call.kwargs["unsubscribe_token"] == mint_unsubscribe_token(
            call.kwargs["to_email"]
        )


async def test_update_send_failure_does_not_abort_remaining(
    db_session: AsyncSession, monkeypatch,
):
    monkeypatch.setattr(waitlist_api, "_UPDATE_SEND_PACE_S", 0)
    await _seed_entry(db_session, email="a@example.at", status="confirmed")
    await _seed_entry(db_session, email="b@example.at", status="confirmed")
    admin = User(email="admin@baulv.at")

    with patch(_UPDATE_SEND_PATCH, side_effect=[False, True]) as send_mock:
        result = await send_waitlist_update(
            WaitlistUpdateSendRequest(subject="Update", body="Text."),
            admin=admin, db=db_session,
        )

    assert send_mock.call_count == 2
    assert result["sent"] == 1 and result["failed"] == 1


async def test_update_send_disabled_switch_is_503(
    db_session: AsyncSession, monkeypatch,
):
    monkeypatch.setattr(settings, "waitlist_enabled", False)
    admin = User(email="admin@baulv.at")

    with patch(_UPDATE_SEND_PATCH) as send_mock:
        with pytest.raises(HTTPException) as excinfo:
            await send_waitlist_update(
                WaitlistUpdateSendRequest(subject="Update", body="Text."),
                admin=admin, db=db_session,
            )

    assert excinfo.value.status_code == 503
    send_mock.assert_not_called()


async def test_update_send_conflict_while_lock_held(
    db_session: AsyncSession,
):
    admin = User(email="admin@baulv.at")

    async with waitlist_api._update_send_lock:
        with pytest.raises(HTTPException) as excinfo:
            await send_waitlist_update(
                WaitlistUpdateSendRequest(subject="Update", body="Text."),
                admin=admin, db=db_session,
            )

    assert excinfo.value.status_code == 409


async def test_update_send_empty_confirmed_list_is_clean_noop(
    db_session: AsyncSession,
):
    await _seed_entry(db_session, email="c@example.at", status="pending")
    admin = User(email="admin@baulv.at")

    with patch(_UPDATE_SEND_PATCH) as send_mock:
        result = await send_waitlist_update(
            WaitlistUpdateSendRequest(subject="Update", body="Text."),
            admin=admin, db=db_session,
        )

    assert result["recipients"] == 0
    assert result["sent"] == 0 and result["failed"] == 0
    send_mock.assert_not_called()


def test_update_mail_html_escapes_admin_body():
    """Der Admin-Body ist Plain-Text: HTML-Sonderzeichen duerfen im
    HTML-Teil nur escaped ankommen; Leerzeilen werden Absaetze."""
    text, html = render_waitlist_update_bodies(
        body_text="Hallo <b>Welt</b> & Co.\n\nZweiter Absatz.",
        unsubscribe_link="https://baulv.at/warteliste/abmelden?token=t123",
    )

    assert "<b>" not in html
    assert "&lt;b&gt;Welt&lt;/b&gt; &amp; Co." in html
    assert html.count('<p style="margin: 0 0 16px;">') >= 2
    assert "Zweiter Absatz." in text
    assert "https://baulv.at/warteliste/abmelden?token=t123" in text
