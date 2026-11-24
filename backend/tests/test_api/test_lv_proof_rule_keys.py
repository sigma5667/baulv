"""The LV API must put ``rule_factor`` / ``rule_ref`` / ``rule_paragraph``
on the wire, never the legacy ``onorm_*`` column names.

Live bug 2026-11-24: the schema used ``Field(alias="onorm_factor")``.
FastAPI serialises responses with ``by_alias=True``, so the JSON carried
``onorm_factor`` and the LV editor crashed on ``rule_factor.toFixed``
as soon as a position with Nachweis rows was expanded.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.schemas.lv import BerechnungsnachweisResponse


def _orm_row(**over):
    base = dict(
        id=uuid.uuid4(), position_id=uuid.uuid4(), room_id=uuid.uuid4(),
        raw_quantity=57.5, formula_description="Umfang × Höhe",
        formula_expression="23.000 × 2.500", onorm_factor=1.0,
        onorm_rule_ref="wandbeschichtung", onorm_paragraph="§3.2 Wandbeschichtung",
        deductions=[], net_quantity=57.5, unit="m2", notes=None,
    )
    base.update(over)
    return SimpleNamespace(**base)


def test_response_json_uses_rule_keys():
    app = FastAPI()

    @app.get("/x", response_model=BerechnungsnachweisResponse)
    def x():
        return _orm_row(onorm_factor=1.12)

    body = TestClient(app).get("/x").json()
    assert body["rule_factor"] == 1.12
    assert body["rule_ref"] == "wandbeschichtung"
    assert body["rule_paragraph"] == "§3.2 Wandbeschichtung"
    assert not any(k.startswith("onorm") for k in body)


def test_public_names_are_still_accepted_as_input():
    data = {k: v for k, v in vars(_orm_row()).items() if not k.startswith("onorm")}
    m = BerechnungsnachweisResponse.model_validate({**data, "rule_factor": 1.5})
    assert m.rule_factor == 1.5
