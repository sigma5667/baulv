"""Landing-Bot (öffentlicher Chat-Button): Prompt-Regeln + Antwort-Aufbereitung.

Überarbeitung 2026-11-25. Der Landing-Bot (support_chat, ohne Login) und
der KI-Berater in der App (chat/assistant.py) haben getrennte Prompts —
dieser Test pinnt den Landing-Prompt und lässt den Berater-Prompt in Ruhe.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from app.api.support_chat import AI_DISCLOSURE, SYSTEM_PROMPT, finalize_reply

PROMPTS = Path(__file__).resolve().parents[2] / "app" / "chat" / "prompts"


# --- Antwort-Aufbereitung --------------------------------------------------

def test_first_reply_always_starts_with_ai_disclosure():
    out = finalize_reply("Das ist nicht mein Thema. Ich bin ein KI-Assistent.", first_reply=True)
    assert out.startswith(AI_DISCLOSURE)


def test_disclosure_not_doubled_when_model_already_starts_with_it():
    out = finalize_reply("Ich bin ein KI-Assistent. BauLV liest Grundrisse.", first_reply=True)
    assert out.count("KI-Assistent") == 1


def test_later_replies_are_not_prefixed():
    assert finalize_reply("BauLV liest Grundrisse.", first_reply=False) == "BauLV liest Grundrisse."


@pytest.mark.parametrize("raw,expected", [
    ("**Wichtig:** bitte prüfen", "Wichtig: bitte prüfen"),
    ("## Export\nPDF oder Excel", "Export\nPDF oder Excel"),
    ("`baulv.at/datenschutz`", "baulv.at/datenschutz"),
])
def test_markdown_is_stripped_for_plain_text_widget(raw, expected):
    assert finalize_reply(raw, first_reply=False) == expected


# --- Prompt-Regeln -----------------------------------------------------------

def test_prompt_has_no_prices_or_plan_names():
    assert not re.search(r"(?i)€|\beur\b|\d+\s*euro|basis|enterprise|\bpro-plan\b|/monat", SYSTEM_PROMPT)


def test_prompt_contains_required_statements():
    for needle in (
        "Ich bin ein KI-Assistent.",
        "BauLV ist in der Testphase, der Test ist kostenlos. Preise stehen noch nicht fest.",
        "Warteliste",
        "baulv.at/datenschutz",
        "baulv.at/impressum",
        "Das kann ich nicht sicher sagen.",
        "2,50 m",
        "Region EU (europe-west4)",  # seit cef9947 so in /datenschutz
    ):
        assert needle in SYSTEM_PROMPT, needle


def test_prompt_never_claims_norm_conformity_or_unbuilt_features():
    assert "ÖNORM-konform\" oder Normnummern" in SYSTEM_PROMPT  # nur als Verbot
    for gone in ("KI-Fachberater", "Positionsvorschläge", "Positionsgenerator", "Angebotsvergleich", "API-Zugang"):
        assert gone not in SYSTEM_PROMPT, gone


def test_in_app_advisor_keeps_its_own_prompt():
    berater = (PROMPTS / "construction_assistant.txt").read_text(encoding="utf-8")
    assert berater.strip() and berater != SYSTEM_PROMPT


def test_create_call_uses_only_kwargs_known_to_the_locked_sdk():
    """Produktion installiert anthropic aus requirements.lock; das lokale
    venv kann älter sein. ``temperature`` existiert in der gesperrten
    Version nicht mehr (TypeError → 503, 2026-11-25). Der Aufruf darf
    nur Parameter nutzen, die überall gelten."""
    src = (Path(__file__).resolve().parents[2] / "app" / "api" / "support_chat.py").read_text(encoding="utf-8")
    call = src.split("await client.messages.create(", 1)[1]
    call = re.split(r"^\s*\)\s*$", call, maxsplit=1, flags=re.MULTILINE)[0]  # bis zur schließenden Zeile
    kwargs = set(re.findall(r"^\s*(\w+)=", call, re.MULTILINE))
    assert kwargs == {"model", "max_tokens", "system", "messages"}, kwargs
