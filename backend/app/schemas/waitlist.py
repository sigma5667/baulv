"""Pydantic-Schemas der öffentlichen Warteliste (v25)."""

from __future__ import annotations

from pydantic import BaseModel, EmailStr, Field


class WaitlistSignupRequest(BaseModel):
    # EmailStr — konsistent mit Register/Login/Passwort-Reset.
    email: EmailStr
    company_name: str = Field(min_length=2, max_length=200)
    name: str | None = Field(default=None, max_length=200)
    # Muss ``true`` sein — der Endpoint weist ``false`` mit 422 ab.
    # Bewusst KEIN Default: die Checkbox ist im Frontend nicht
    # vorangehakt, und das Backend erzwingt die aktive Entscheidung
    # unabhängig davon.
    consent: bool
    # Kampagnen-Herkunft aus ``?ref=`` — Endpoint filtert auf
    # ``[a-z0-9_-]{1,64}``, alles andere wird verworfen (kein 422:
    # ein kaputter ref-Parameter darf die Anmeldung nicht verhindern).
    source: str | None = Field(default=None, max_length=200)


class WaitlistTokenRequest(BaseModel):
    token: str = Field(min_length=1, max_length=1024)
