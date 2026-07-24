"""Zentraler E-Mail-Footer: §14-UGB-Firmenblock + Abmelde-Link.

EINE Stelle für die Pflichtangaben in geschäftlichen E-Mails
(§ 14 UGB: Firma, Rechtsform, Sitz, Firmenbuchnummer,
Firmenbuchgericht). Die Firmendaten existieren noch nicht — alle
Werte sind klar markierte Platzhalter. Sobald die Daten feststehen,
werden NUR die ``COMPANY_*``-Konstanten hier gefüllt; jede Mail, die
``footer_text``/``footer_html`` nutzt, trägt sie ab dann automatisch.

Hinweis Umsetzung, keine Rechtsberatung: welcher Pflichtinhalt final
hineingehört, vor dem ersten echten Versand prüfen lassen.

Verwendung
==========

Jede ausgehende Mail hängt den Footer an — Marketing-Mails MIT
Abmelde-Link (``unsubscribe_url`` übergeben), rein transaktionale
Mails ohne (``None``). Die Warteliste-Bestätigungsmail übergibt den
Link immer: wer auf der Liste steht, muss sich aus jeder Mail heraus
abmelden können.
"""

from __future__ import annotations

# --- §14-UGB-Firmenblock — PLATZHALTER, an EINER Stelle füllbar. ---
# TODO(Firmengründung): echte Werte eintragen, Platzhalter-Marker
# entfernen. ``_COMPANY_DATA_IS_PLACEHOLDER`` ist der Wachhund: der
# Boot-Guard unten verweigert den App-Start mit eingeschalteter
# Warteliste, solange das Flag steht — wer die Werte füllt, stellt
# das Flag mit um (die Tests pinnen beide Seiten des Guards).
_COMPANY_DATA_IS_PLACEHOLDER: bool = True

COMPANY_NAME: str = "[Firma — folgt]"
COMPANY_LEGAL_SEAT: str = "[Sitz — folgt]"
COMPANY_REGISTER_NO: str = "[FN — folgt]"
COMPANY_REGISTER_COURT: str = "[Firmenbuchgericht — folgt]"
COMPANY_CONTACT_EMAIL: str = "kontakt@baulv.at"


def assert_company_data_ready_for_waitlist(waitlist_enabled: bool) -> None:
    """Boot-Guard (v25): Warteliste EIN + Platzhalter-Firmendaten
    → lauter ``RuntimeError``, der App-Start bricht ab.

    Aufgerufen aus dem ``lifespan``-Hook in ``app/main.py``, BEVOR
    die App Requests annimmt. Absicht: ``WAITLIST_ENABLED=true`` zu
    setzen, ohne vorher die §-14-UGB-Pflichtangaben zu füllen, darf
    kein stiller Betriebszustand sein — jede Warteliste-Mail trüge
    sonst "[Firma — folgt]" im Footer. Ein Crash beim Deploy ist
    sichtbar; eine falsche Mail beim Empfänger ist es nicht.

    Mit ausgeschaltetem Schalter ist der Guard bewusst still: die
    Platzhalter sind der dokumentierte Zustand VOR der Gründung.
    """
    if waitlist_enabled and _COMPANY_DATA_IS_PLACEHOLDER:
        raise RuntimeError(
            "WAITLIST_ENABLED=true, aber die §14-UGB-Firmendaten in "
            "app/services/email_footer.py sind noch Platzhalter "
            "('[Firma — folgt]'). Erst COMPANY_* füllen und "
            "_COMPANY_DATA_IS_PLACEHOLDER auf False stellen, dann "
            "die Warteliste einschalten."
        )


def _company_lines() -> list[str]:
    """Die Footer-Zeilen als Liste — eine Quelle für Text und HTML."""
    return [
        "BauLV - KI-gestützte Bau-Ausschreibungssoftware",
        COMPANY_NAME,
        f"Sitz: {COMPANY_LEGAL_SEAT} · {COMPANY_REGISTER_NO} · "
        f"{COMPANY_REGISTER_COURT}",
        f"Kontakt: {COMPANY_CONTACT_EMAIL} · https://baulv.at",
    ]


def footer_text(unsubscribe_url: str | None = None) -> str:
    """Plain-Text-Footer, mit ``--``-Trenner wie in den bestehenden
    Mails. Bei ``unsubscribe_url`` kommt der Abmelde-Hinweis VOR den
    Firmenblock — er ist der Teil, den Empfänger suchen."""
    lines: list[str] = ["", "--"]
    if unsubscribe_url:
        lines += [
            "Keine E-Mails mehr erhalten? Hier abmelden:",
            unsubscribe_url,
            "",
        ]
    lines += _company_lines()
    return "\n".join(lines)


def footer_html(unsubscribe_url: str | None = None) -> str:
    """HTML-Footer-Fragment (kein eigenes Dokument) — Inline-Styles
    passend zu den bestehenden Mail-Templates in
    ``app/services/email.py``."""
    unsubscribe_block = ""
    if unsubscribe_url:
        unsubscribe_block = (
            '<p style="margin: 0 0 12px; font-size: 12px; color: #9ca3af;">'
            "Keine E-Mails mehr erhalten? "
            f'<a href="{unsubscribe_url}" '
            'style="color: #6b7280; text-decoration: underline;">'
            "Hier abmelden</a>."
            "</p>"
        )
    company = "<br>".join(_company_lines())
    return (
        '<hr style="border: none; border-top: 1px solid #e5e7eb; '
        'margin: 32px 0 16px;">'
        f"{unsubscribe_block}"
        '<p style="margin: 0; font-size: 12px; color: #9ca3af;">'
        f"{company}"
        "</p>"
    )
