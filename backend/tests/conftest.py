"""Shared test fixtures.

The DB tests in ``test_calculation_engine/test_calculate_lv_upsert.py``
need a real ``AsyncSession`` against a real schema — they're verifying
SQLAlchemy-level behaviour (cascades, eager loads, identity), not pure
calculator math, so a mocked session would defeat the point.

Strategy: spin up an in-memory SQLite DB via ``aiosqlite`` per test.
SQLite is fine for this — the upsert logic doesn't use any
PostgreSQL-specific features. The two friction points are:

* Several models reach for ``sqlalchemy.dialects.postgresql.{JSONB,
  INET, UUID}`` directly. SQLite has no native equivalents, so we
  register ``@compiles`` shims that emit SQLite-compatible types
  (TEXT for JSON/INET, CHAR(36) for UUID) **only** when the dialect
  is SQLite. Postgres deployments are unaffected.
* ``Project.id``-style columns use ``Mapped[uuid.UUID]`` with the
  generic ``Uuid`` type, which already works on SQLite — we don't
  need to touch those.

The ``db_session`` fixture creates the schema fresh for every test,
hands out an ``AsyncSession``, then drops everything. That's slow
relative to a transaction-rollback approach but correct under all
cascades and avoids the "did the previous test leave state behind"
debugging trap on a five-test suite.

The ``stub_calculator`` fixture replaces ``TradeRegistry.get`` for the
duration of the test so we can hand the engine a deterministic list
of ``PositionQuantity`` objects — the goal is to test the upsert
machinery, not the Malerarbeiten math.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator, Callable
from decimal import Decimal

import pytest
import pytest_asyncio
from sqlalchemy.dialects.postgresql import INET, JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.ext.compiler import compiles

from app.calculation_engine.registry import TradeRegistry
from app.calculation_engine.types import PositionQuantity
from app.db.base import Base
# Importing every model module ensures their tables are registered on
# ``Base.metadata`` before ``create_all`` runs. Without these imports
# tables backed by relationships (e.g. ``users`` referenced by
# ``projects.user_id``) would be missing from the SQLite schema.
import app.db.models  # noqa: F401
from app.db.models.lv import Leistungsgruppe, Leistungsverzeichnis  # noqa: F401
from app.db.models.project import (  # noqa: F401
    Building,
    Floor,
    Project,
    Room,
    Unit,
)
from app.db.models.user import User  # noqa: F401


# ---------------------------------------------------------------------------
# SQLite compatibility shims for Postgres-specific column types.
# ---------------------------------------------------------------------------
#
# These ``@compiles`` decorators only fire when SQLAlchemy is generating
# DDL for the SQLite dialect, so production Postgres rendering is
# untouched. The shims are deliberately permissive — JSONB becomes
# TEXT (we never query it via JSON path operators in tests), INET
# becomes TEXT, and the explicit ``postgresql.UUID`` type becomes
# CHAR(36) so values can be stringified for storage and round-tripped
# back into ``uuid.UUID`` on read by SQLAlchemy's ``Uuid`` adapter.


@compiles(JSONB, "sqlite")
def _compile_jsonb_sqlite(type_, compiler, **kw):
    return "TEXT"


@compiles(INET, "sqlite")
def _compile_inet_sqlite(type_, compiler, **kw):
    return "TEXT"


@compiles(PG_UUID, "sqlite")
def _compile_pg_uuid_sqlite(type_, compiler, **kw):
    return "CHAR(36)"


# ---------------------------------------------------------------------------
# Async DB fixture
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def db_session() -> AsyncIterator[AsyncSession]:
    """Yield a session against a fresh in-memory SQLite database.

    Per-test schema teardown is overkill for a transaction-bound suite,
    but it gives us absolute isolation for tests that exercise CASCADE
    DELETE behaviour — ``aiosqlite`` reuses connections, so leftover
    rows from a prior test would otherwise be visible.
    """
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    Session = async_sessionmaker(engine, expire_on_commit=False)
    async with Session() as session:
        yield session

    await engine.dispose()


# ---------------------------------------------------------------------------
# Test-data factory: seed an LV with a single room ready for calculate_lv.
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def seeded_lv(db_session: AsyncSession):
    """Create a User → Project → Building → Floor → Unit → Room chain
    plus an empty ``Leistungsverzeichnis`` and return a small dict of
    the IDs the tests need.

    ``calculate_lv`` requires at least one room (otherwise it raises
    ``ValueError`` from the no-rooms guard), so the chain has to go all
    the way down to ``Room``. We don't bother with openings — the stub
    calculator doesn't read them.
    """
    user = User(
        id=uuid.uuid4(),
        email=f"test-{uuid.uuid4()}@example.com",
        password_hash="x",
        full_name="Test User",
    )
    db_session.add(user)
    await db_session.flush()

    project = Project(id=uuid.uuid4(), user_id=user.id, name="Test")
    db_session.add(project)
    await db_session.flush()

    building = Building(id=uuid.uuid4(), project_id=project.id, name="Haus 1")
    db_session.add(building)
    await db_session.flush()

    floor = Floor(id=uuid.uuid4(), building_id=building.id, name="EG")
    db_session.add(floor)
    await db_session.flush()

    unit = Unit(id=uuid.uuid4(), floor_id=floor.id, name="Top 1")
    db_session.add(unit)
    await db_session.flush()

    room = Room(
        id=uuid.uuid4(),
        unit_id=unit.id,
        name="Wohnzimmer",
        area_m2=Decimal("20.0"),
        perimeter_m=Decimal("18.0"),
        height_m=Decimal("2.7"),
    )
    db_session.add(room)
    await db_session.flush()

    lv = Leistungsverzeichnis(
        id=uuid.uuid4(),
        project_id=project.id,
        name="Test LV",
        trade="malerarbeiten",  # arbitrary — overridden via stub_calculator
    )
    db_session.add(lv)
    await db_session.commit()

    return {
        "user_id": user.id,
        "project_id": project.id,
        "room_id": room.id,
        "lv_id": lv.id,
    }


# ---------------------------------------------------------------------------
# Calculator stubbing: hand the engine deterministic results.
# ---------------------------------------------------------------------------
#
# The engine calls ``TradeRegistry.get(lv.trade).calculate(rooms)`` and
# we want full control of that ``calculate`` return value test by test.
# Monkey-patching ``TradeRegistry.get`` is the cleanest way: the engine
# code is untouched, and the patch is automatically reverted at the end
# of the test by pytest's ``monkeypatch`` fixture.


class _StubCalculator:
    def __init__(self, results: list[PositionQuantity]):
        self._results = results

    def calculate(self, rooms):
        # Engine ignores the calculator's view of rooms in favour of the
        # ``measurement_lines`` baked into each PositionQuantity. We
        # don't need to re-derive anything from ``rooms``.
        return self._results


@pytest.fixture
def stub_calculator(monkeypatch) -> Callable[[list[PositionQuantity]], None]:
    """Return a function that, when called with a list of
    ``PositionQuantity`` objects, makes the next ``calculate_lv`` run
    return exactly that list.
    """

    def _install(results: list[PositionQuantity]) -> None:
        stub = _StubCalculator(results)
        monkeypatch.setattr(
            TradeRegistry,
            "get",
            classmethod(lambda cls, trade_name: stub),
        )

    return _install
