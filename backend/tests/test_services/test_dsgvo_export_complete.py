"""DSGVO-Export vollständig (Audit L10).

Pinnt, dass ``build_export_zip``:
  * ``daten.json`` mit schema_version 3 und allen neuen Abschnitten
    liefert (Einwilligungen, Warteliste, API-Schlüssel, LV-Vorlagen,
    MCP-Protokoll, Nutzungsstatistik),
  * keine Geheimnisse enthält (key_hash, confirm_token_hash),
  * nur eigene Vorlagen exportiert (keine System-/fremden Vorlagen),
  * Plan-Dateien und Logo als Dateien ins ZIP legt,
  * fehlende Dateien als ``file_in_export: null`` markiert,
  * nie Dateien außerhalb des Upload-Verzeichnisses einpackt.
"""

from __future__ import annotations

import io
import json
import uuid
import zipfile
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.models.analytics import UsageAnalyticsEvent
from app.db.models.api_key import ApiKey
from app.db.models.consent import ConsentSnapshot
from app.db.models.lv_template import LVTemplate
from app.db.models.mcp_audit import McpAuditLogEntry
from app.db.models.plan import Plan
from app.db.models.project import Project
from app.db.models.user import User
from app.db.models.waitlist_entry import WaitlistEntry
from app.services.analytics import hash_user_id
from app.services.dsgvo import build_export_zip


@pytest.fixture
def upload_root(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "upload_dir", str(tmp_path / "uploads"))
    return settings.upload_path


async def _seed(db: AsyncSession, upload_root, tmp_path):
    now = datetime.now(timezone.utc)
    user = User(
        id=uuid.uuid4(),
        email="export@example.at",
        password_hash="x",
        full_name="Export Test",
    )
    other = User(id=uuid.uuid4(), email="other@example.at", password_hash="x", full_name="Other")
    db.add_all([user, other])
    await db.flush()

    project = Project(id=uuid.uuid4(), user_id=user.id, name="P1")
    db.add(project)
    await db.flush()

    pdir = upload_root / str(project.id)
    pdir.mkdir(parents=True)
    present = pdir / "a.pdf"
    present.write_bytes(b"%PDF-1.4 vorhanden")
    outside = tmp_path / "geheim.txt"
    outside.write_text("darf nie ins ZIP")

    db.add_all(
        [
            Plan(id=uuid.uuid4(), project_id=project.id, filename="EG Grundriss.pdf",
                 file_path=str(present), file_size_bytes=18),
            Plan(id=uuid.uuid4(), project_id=project.id, filename="weg.pdf",
                 file_path=str(pdir / "weg.pdf"), file_size_bytes=1),
            Plan(id=uuid.uuid4(), project_id=project.id, filename="boese.pdf",
                 file_path=str(outside), file_size_bytes=1),
        ]
    )

    logo_dir = upload_root / "logos" / str(user.id)
    logo_dir.mkdir(parents=True)
    logo = logo_dir / "x_logo.png"
    logo.write_bytes(b"\x89PNG logo")
    user.logo_path = str(logo)

    db.add_all(
        [
            ConsentSnapshot(user_id=user.id, event_type="registration",
                            privacy_version="1.2", terms_version="1.1",
                            marketing_optin=False),
            WaitlistEntry(email="export@example.at", company_name="Export GmbH", status="confirmed",
                          confirm_token_hash="a" * 64,
                          token_expires_at=now + timedelta(days=7),
                          consent_text_version="1"),
            ApiKey(user_id=user.id, name="n8n", key_prefix="blv_abc",
                   key_hash="GEHEIMERHASH"),
            LVTemplate(name="Meine Vorlage", category="wohnung",
                       gewerk="malerarbeiten", is_system=False,
                       created_by_user_id=user.id, template_data={"g": []}),
            LVTemplate(name="System", category="wohnung", gewerk="malerarbeiten",
                       is_system=True, template_data={}),
            LVTemplate(name="Fremd", category="wohnung", gewerk="malerarbeiten",
                       is_system=False, created_by_user_id=other.id,
                       template_data={}),
            McpAuditLogEntry(user_id=user.id, tool_name="list_projects",
                             arguments={}, result="ok", latency_ms=5),
            UsageAnalyticsEvent(event_type="project_created", event_data={},
                                anonymous_user_id=hash_user_id(user.id)),
        ]
    )
    await db.commit()
    await db.refresh(user)
    return user


@pytest.mark.asyncio
async def test_export_zip_is_complete(db_session: AsyncSession, upload_root, tmp_path):
    user = await _seed(db_session, upload_root, tmp_path)

    body, files = await build_export_zip(user, db_session)
    zf = zipfile.ZipFile(io.BytesIO(body))
    names = set(zf.namelist())
    data = json.loads(zf.read("daten.json"))

    assert data["schema_version"] == 3
    assert len(data["consent_snapshots"]) == 1
    assert data["waitlist_entries"][0]["status"] == "confirmed"
    assert data["api_keys"][0]["key_prefix"] == "blv_abc"
    assert [t["name"] for t in data["lv_templates"]] == ["Meine Vorlage"]
    assert data["mcp_audit_log"][0]["tool_name"] == "list_projects"
    assert len(data["usage_analytics"]) == 1

    raw = json.dumps(data)
    assert "GEHEIMERHASH" not in raw
    assert "a" * 64 not in raw
    assert "password_hash" not in raw

    plans = {p["filename"]: p for p in data["projects"][0]["plans"]}
    present_name = plans["EG Grundriss.pdf"]["file_in_export"]
    assert present_name in names
    assert zf.read(present_name) == b"%PDF-1.4 vorhanden"
    assert plans["weg.pdf"]["file_in_export"] is None
    assert plans["boese.pdf"]["file_in_export"] is None
    assert not any("geheim" in n for n in names)

    assert data["logo_in_export"] == "dateien/logo.png"
    assert zf.read("dateien/logo.png") == b"\x89PNG logo"
    assert files == 2
