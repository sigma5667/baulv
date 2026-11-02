"""Regression: „KI-Texte generieren" lieferte immer 500 (Audit 2026-11).

``generate_position_texts`` lud die Positionen mit
``selectinload(...).selectinload("positionen")`` — SQLAlchemy 2.x
akzeptiert keine Strings als Attributnamen und warf
``ArgumentError: Strings are not accepted for attribute names in
loader options``, bevor Claude überhaupt gefragt wurde. Kein Test
deckte den Pfad ab. Anthropic wird hier gemockt — kein echter Aufruf.
"""

from __future__ import annotations

import json
import uuid
from types import SimpleNamespace

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.lv import Leistungsgruppe, Position
from app.lv_generator import generator


class _FakeMessages:
    def __init__(self, reply: str):
        self._reply = reply
        self.calls: list[dict] = []

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(content=[SimpleNamespace(text=self._reply)])


class _FakeAsyncAnthropic:
    last: "_FakeAsyncAnthropic | None" = None

    def __init__(self, *args, **kwargs):
        self.messages = _FakeMessages(
            json.dumps(
                {
                    "positions": [
                        {"position_code": "01.01", "langtext": "Wände streichen, zweifach."}
                    ]
                }
            )
        )
        _FakeAsyncAnthropic.last = self


@pytest.mark.asyncio
async def test_generate_position_texts_loads_positions_and_writes_langtext(
    db_session: AsyncSession, seeded_lv, monkeypatch
):
    import anthropic

    monkeypatch.setattr(anthropic, "AsyncAnthropic", _FakeAsyncAnthropic)

    gruppe = Leistungsgruppe(
        id=uuid.uuid4(), lv_id=seeded_lv["lv_id"], nummer="01", bezeichnung="Malerarbeiten"
    )
    db_session.add(gruppe)
    await db_session.flush()
    pos = Position(
        id=uuid.uuid4(),
        gruppe_id=gruppe.id,
        positions_nummer="01.01",
        kurztext="Wandflächen streichen",
        einheit="m2",
        menge=48,
    )
    db_session.add(pos)
    await db_session.commit()

    updated = await generator.generate_position_texts(seeded_lv["lv_id"], db_session)

    assert updated == 1
    sent = _FakeAsyncAnthropic.last.messages.calls[0]["messages"][0]["content"]
    assert "Wandflächen streichen" in sent
    reloaded = (
        await db_session.execute(select(Position).where(Position.id == pos.id))
    ).scalar_one()
    assert reloaded.langtext == "Wände streichen, zweifach."
