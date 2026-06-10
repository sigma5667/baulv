"""v24.4.9 — DSGVO-Lösch-Hygiene Tests.

Zwei Punkte aus dem v24.4.9-Audit-Bericht:

1. **Email-Hash im Audit-Log statt Klartext.** Beim Account-Delete
   landet die Email als ``email_hash`` (SHA-256 mit ``analytics_salt``)
   in ``audit_log.meta`` — nicht im Klartext. Sonst überlebte
   personenbezogenes Datum die DSGVO-Art-17-Löschung über den
   ``ON DELETE SET NULL``-FK der Audit-Tabelle.

2. **Logo-Verzeichnis mitlöschen.** ``_delete_user_plan_files`` räumt
   jetzt auch ``upload_path/logos/<user_id>/`` ab. Bisher blieb der
   Logo-Ordner als verwaister File-System-Eintrag stehen, obwohl der
   User Löschung verlangte.

Wir testen beide Pfade direkt gegen die echten Helfer (kein API-Layer,
keine Stripe-Mocks), in der ``db_session``-Fixture aus ``conftest.py``.
"""

from __future__ import annotations

import shutil
import uuid
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import _email_audit_hash, delete_my_account
from app.auth import hash_password
from app.config import settings
from app.db.models.audit import AuditLogEntry
from app.db.models.project import Project
from app.db.models.user import User
from app.schemas.user import AccountDeletionRequest
from app.services.audit import EVENT_ACCOUNT_DELETED
from app.services.dsgvo import _delete_user_plan_files


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _StubRequest:
    """Stand-in for FastAPI Request — provides .headers + .client only,
    same shape ``services/audit._client_ip`` expects."""

    def __init__(self) -> None:
        self.headers: dict[str, str] = {}
        self.client = None


async def _seed_user(
    db: AsyncSession, *, password_plain: str = "verystrongpass1234"
) -> User:
    user = User(
        id=uuid.uuid4(),
        email=f"delete-test-{uuid.uuid4()}@example.com",
        password_hash=hash_password(password_plain),
        full_name="Delete Tester",
        company_name="Delete GmbH",
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user


# ---------------------------------------------------------------------------
# Fix 1 — Email als Hash im Audit-Meta beim Account-Delete
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_delete_account_writes_email_hash_not_plaintext(
    db_session: AsyncSession,
):
    """Nach POST /me/delete enthält der Audit-Eintrag genau ein
    ``email_hash``-Feld, KEIN Klartext-``email``-Feld; der Hash matched
    das deterministische Pseudonymisierungs-Muster."""
    password = "verystrongpass1234"
    user = await _seed_user(db_session, password_plain=password)
    user_email_before_delete = user.email

    payload = AccountDeletionRequest(password=password, confirmation="LÖSCHEN")

    await delete_my_account(
        payload, _StubRequest(), user=user, db=db_session
    )
    await db_session.commit()

    # Audit-Row mit dem Delete-Event finden.
    result = await db_session.execute(
        select(AuditLogEntry).where(
            AuditLogEntry.event_type == EVENT_ACCOUNT_DELETED
        )
    )
    audit_rows = result.scalars().all()
    assert len(audit_rows) == 1, (
        f"Expected exactly one EVENT_ACCOUNT_DELETED audit row, got "
        f"{len(audit_rows)}"
    )

    audit = audit_rows[0]
    meta = audit.meta or {}

    # Kernpunkt: Klartext-Email darf NICHT mehr in meta sein, Hash
    # MUSS drin sein.
    assert "email" not in meta, (
        f"Klartext-Email darf nach v24.4.9 nicht mehr im Audit-Meta "
        f"sitzen; gefunden: {meta!r}"
    )
    assert "email_hash" in meta, f"email_hash fehlt im Audit-Meta: {meta!r}"

    # Reproducibility: der Hash muss zum bekannten Helper passen, sonst
    # bricht forensische Korrelation (selbe Email → selber Hash).
    expected_hash = _email_audit_hash(user_email_before_delete)
    assert meta["email_hash"] == expected_hash, (
        "Hash matched nicht _email_audit_hash(user.email) — "
        "Salt-Drift oder Format-Drift?"
    )

    # Sanity: SHA-256-hex ist genau 64 chars.
    assert len(meta["email_hash"]) == 64
    assert all(c in "0123456789abcdef" for c in meta["email_hash"])

    # Hinweis: ob ``audit.user_id`` nach dem User-Delete auf NULL läuft,
    # hängt am SQL-Level ``ON DELETE SET NULL``-FK + von aktivierten
    # Foreign-Keys im jeweiligen Dialekt (in unserer SQLite-Test-DB
    # ist das nicht garantiert eingeschaltet). Das hier zu prüfen
    # würde Test-Setup-Details statt der eigentlichen Hash-Wahrheit
    # testen — daher bewusst weggelassen. Production-Postgres hat
    # FKs immer aktiv; das eigentlich-relevante v24.4.9-Versprechen
    # ("Klartext-Email überlebt nicht") ist mit dem ``"email" not in
    # meta``-Check oben schon gesichert.


# ---------------------------------------------------------------------------
# Fix 2 — Logo-Dir mitlöschen
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_delete_user_plan_files_removes_logo_and_project_dirs(
    db_session: AsyncSession, tmp_path: Path, monkeypatch,
):
    """``_delete_user_plan_files`` muss BEIDE Verzeichnis-Typen
    entfernen: pro-Projekt-Ordner UND den Logo-Ordner des Users.
    Pre-v24.4.9 wurde Letzterer übersehen."""
    # Sandboxed upload_path — wir wollen nicht in echte uploads/
    # schreiben.
    monkeypatch.setattr(settings, "upload_dir", str(tmp_path))

    user = await _seed_user(db_session)

    # Projekte anlegen plus Plan-Verzeichnisse auf Disk.
    project_a = Project(
        id=uuid.uuid4(), user_id=user.id, name="Projekt A"
    )
    project_b = Project(
        id=uuid.uuid4(), user_id=user.id, name="Projekt B"
    )
    db_session.add_all([project_a, project_b])
    await db_session.commit()

    plan_dir_a = tmp_path / str(project_a.id)
    plan_dir_b = tmp_path / str(project_b.id)
    plan_dir_a.mkdir()
    plan_dir_b.mkdir()
    (plan_dir_a / "plan.pdf").write_bytes(b"%PDF-1.4 fake A")
    (plan_dir_b / "plan.pdf").write_bytes(b"%PDF-1.4 fake B")

    # Logo-Verzeichnis pro Layout in auth.py:_logo_dir_for.
    logo_dir = tmp_path / "logos" / str(user.id)
    logo_dir.mkdir(parents=True)
    (logo_dir / "company.png").write_bytes(b"\x89PNG\r\n\x1a\nfake")

    # Sanity vor dem Cleanup.
    assert plan_dir_a.exists()
    assert plan_dir_b.exists()
    assert logo_dir.exists()
    assert (logo_dir / "company.png").exists()

    # Cleanup.
    await _delete_user_plan_files(user, db_session)

    # Alle drei Verzeichnisse müssen weg sein.
    assert not plan_dir_a.exists(), "Plan-Dir A wurde nicht entfernt"
    assert not plan_dir_b.exists(), "Plan-Dir B wurde nicht entfernt"
    assert not logo_dir.exists(), (
        "Logo-Dir wurde NICHT entfernt — das ist der v24.4.9-Fix-Bug"
    )

    # Andere Inhalte unter upload_path (z.B. ein gleichnamiges
    # logos/-Verzeichnis eines anderen Users) bleiben unberührt:
    other_user_logo = tmp_path / "logos" / str(uuid.uuid4())
    other_user_logo.mkdir(parents=True)
    (other_user_logo / "company.png").write_bytes(b"other user")
    # Re-Cleanup-Call (idempotent) darf den anderen Ordner nicht
    # anfassen.
    await _delete_user_plan_files(user, db_session)
    assert other_user_logo.exists()
    assert (other_user_logo / "company.png").exists()


@pytest.mark.asyncio
async def test_delete_user_plan_files_is_idempotent_without_logo(
    db_session: AsyncSession, tmp_path: Path, monkeypatch,
):
    """User ohne Logo-Upload → ``_delete_user_plan_files`` läuft
    fehlerfrei durch, weil der ``if logo_dir.exists()``-Guard greift."""
    monkeypatch.setattr(settings, "upload_dir", str(tmp_path))

    user = await _seed_user(db_session)
    # Bewusst KEIN logos/<user.id>/-Verzeichnis anlegen.

    # Sollte einfach durchlaufen, ohne FileNotFoundError oder OSError.
    await _delete_user_plan_files(user, db_session)


# ---------------------------------------------------------------------------
# Regression-Lock — forgot-password schreibt email_hash, nie Klartext
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_password_reset_no_account_writes_email_hash(
    db_session: AsyncSession,
):
    """Der ``no_account``-Branch von POST /password-reset schreibt
    ``email_hash`` (reproduzierbar via ``_email_audit_hash``), niemals
    Klartext-``email`` — verhindert stilles Zurückkippen des
    v24.4.9-Fixes bei künftigen Refactors."""
    from app.api.auth import request_password_reset
    from app.schemas.user import PasswordResetRequest
    from app.services.audit import EVENT_PASSWORD_RESET_REQUESTED

    probe_email = f"never-registered-{uuid.uuid4()}@example.com"

    response = await request_password_reset(
        PasswordResetRequest(email=probe_email),
        _StubRequest(),
        db=db_session,
    )
    await db_session.commit()

    # Anti-Enumeration-Kontrakt: generische 200-Message.
    assert "message" in response

    result = await db_session.execute(
        select(AuditLogEntry).where(
            AuditLogEntry.event_type == EVENT_PASSWORD_RESET_REQUESTED
        )
    )
    rows = result.scalars().all()
    assert len(rows) == 1
    meta = rows[0].meta or {}

    assert "email" not in meta, (
        f"Klartext-Email im Reset-Audit gefunden: {meta!r} — "
        f"v24.4.9-Regression!"
    )
    assert meta.get("email_hash") == _email_audit_hash(probe_email)
    assert meta.get("result") == "no_account"
    assert rows[0].user_id is None
