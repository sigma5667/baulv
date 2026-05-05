"""v23.7 — neutralize ÖNORM references in seeded LV-template texts

Revision ID: 021
Revises: 020
Create Date: 2026-05-04

Background
----------

Migration 012 seeded a small library of system LV templates with
position long-texts that referenced specific Austrian norms by
number ("Klasse 3 nach ÖNORM EN 13300", "gemäß ÖNORM EN 13381-8",
…). v23.7 repositions BauLV as a calculation engine without any
claim to be a normative reference; the user-facing surface must
therefore not name specific standards.

Migration history is immutable, so we don't edit 012 in place. We
instead walk the JSONB ``template_data`` blob on every system
template row and rewrite the affected substrings in place. Deployed
databases pick up the neutralised phrasing on the next
``alembic upgrade``; fresh installs run 012 → 021 in sequence and
land at the same neutral state.

Why a dict-walk and not ``REGEXP_REPLACE`` on the JSONB text
-----------------------------------------------------------

Two reasons. (1) Test harness uses SQLite, which doesn't speak
JSONB at all — the conftest shim renders JSONB as TEXT and the
data is stored as a JSON-encoded string. Working with the parsed
Python dict is the only path that runs on both engines without
forking the migration. (2) String replacement in JSON-encoded text
risks corrupting escapes if a pattern straddles a quoted boundary;
parsing first eliminates that class of bug.

Idempotent
----------

Each rewrite is a token-level Python ``str.replace``. Running this
migration twice is a no-op because the second pass finds no
matching tokens. Safe under repeated upgrade attempts.
"""

from __future__ import annotations

import json
from typing import Any, Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "021"
down_revision: Union[str, None] = "020"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# Token-level rewrites. Each entry is (old_substring, new_substring).
# Order matters: longer, more-specific patterns first so they don't
# get partially matched by shorter ones (e.g. "ÖNORM EN 13381-8"
# must rewrite before any bare "ÖNORM" rule).
_REWRITES: tuple[tuple[str, str], ...] = (
    (
        "Klasse 3 nach ÖNORM EN 13300",
        "Klasse 3 nach branchenüblicher Klassifizierung",
    ),
    (
        "Fluchtweg- und Notausgangskennzeichnungen nach ÖNORM Z 1000",
        "Fluchtweg- und Notausgangskennzeichnungen gemäß einschlägigen Sicherheitsvorschriften",
    ),
    (
        "für Feuerwiderstandsklasse R30 gemäß ÖNORM EN 13381-8",
        "für Feuerwiderstandsklasse R30 gemäß einschlägigen Brandschutzvorschriften",
    ),
    (
        "F30 gemäß ÖNORM B 3800",
        "F30 gemäß einschlägigen Brandschutzvorschriften",
    ),
    (
        "2K-Bodenmarkierungsfarbe, farblich nach ÖNORM",
        "2K-Bodenmarkierungsfarbe, branchenüblich farblich kodiert",
    ),
    (
        "Reiner Silikatanstrich nach ÖNORM",
        "Reiner Silikatanstrich",
    ),
)


def _apply_rewrites(value: str, mapping: tuple[tuple[str, str], ...]) -> str:
    """Apply each (old, new) replacement in order. Pure-string ops."""
    out = value
    for old, new in mapping:
        out = out.replace(old, new)
    return out


def _walk_template_data(data: Any, mapping: tuple[tuple[str, str], ...]) -> Any:
    """Walk the ``template_data`` JSON tree and rewrite ``langtext``.

    Only the ``langtext`` field carries norm-number references in
    the seed data — kurztext stays neutral. Defensive: if the shape
    ever changes (additional fields, nested groups), the recursion
    still finds every ``langtext`` key without us having to maintain
    a hard-coded path.
    """
    if isinstance(data, dict):
        return {
            k: (
                _apply_rewrites(v, mapping)
                if k == "langtext" and isinstance(v, str)
                else _walk_template_data(v, mapping)
            )
            for k, v in data.items()
        }
    if isinstance(data, list):
        return [_walk_template_data(item, mapping) for item in data]
    return data


def _rewrite_seed_data(forward: bool) -> None:
    """Apply (or reverse) the rewrites on every system-template row.

    ``forward=True`` runs the upgrade direction; ``False`` runs the
    downgrade. The downgrade swaps each (old, new) pair and applies
    in the same order — order doesn't matter for the downgrade
    because the rewritten strings (`branchenüblich`, `einschlägige`)
    are non-overlapping with each other.
    """
    bind = op.get_bind()
    mapping = (
        _REWRITES if forward else tuple((new, old) for old, new in _REWRITES)
    )

    rows = bind.execute(
        sa.text(
            "SELECT id, template_data FROM lv_templates WHERE is_system = TRUE"
        )
    ).fetchall()

    for row_id, raw in rows:
        # Postgres returns a parsed dict via the JSONB driver; SQLite
        # round-trips the JSONB shim as a JSON-encoded string. Handle
        # both transparently so the migration runs in tests.
        if isinstance(raw, str):
            data = json.loads(raw)
            was_string = True
        else:
            data = raw
            was_string = False

        rewritten = _walk_template_data(data, mapping)

        # Skip the write when nothing actually changed — keeps the
        # idempotent re-run path noise-free.
        if rewritten == data:
            continue

        new_value = json.dumps(rewritten, ensure_ascii=False) if was_string else rewritten

        bind.execute(
            sa.text(
                "UPDATE lv_templates SET template_data = :data WHERE id = :id"
            ).bindparams(
                sa.bindparam(
                    "data",
                    value=new_value,
                    type_=sa.Text() if was_string else sa.JSON(),
                ),
                sa.bindparam("id", value=row_id),
            )
        )


def upgrade() -> None:
    _rewrite_seed_data(forward=True)


def downgrade() -> None:
    _rewrite_seed_data(forward=False)
