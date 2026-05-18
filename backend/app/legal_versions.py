"""Canonical version pins for our user-facing legal documents.

The DSGVO Art. 7 ("conditions for consent") evidence requirement
is the reason these constants exist as code rather than free-form
strings: every consent action (registration, privacy refresh,
terms refresh, marketing opt-in change) writes a snapshot
referencing exactly *which* version the user saw, so we can
reconstruct "what did Maria agree to on 2026-04-27?" later from
the consent-snapshot table without trusting frontend memory.

Bumping a version
=================

Bumping is a deliberate two-step:

1. Update the relevant ``*_VERSION`` and ``*_DATE`` constant in
   this module. The tests (``tests/test_api/test_consent.py``)
   pin the exact strings so a refactor can't accidentally roll
   them back.

2. Update the corresponding page text in
   ``frontend/src/pages/DatenschutzPage.tsx`` /
   ``AGBPage.tsx`` to match — the user must see the new content,
   not just a new version number.

Once deployed, the ``GET /api/auth/me`` response surfaces the
mismatch between user-accepted and server-current versions, and
the SPA's ``ConsentRefreshModal`` triggers on next login.
"""

from __future__ import annotations

# Datenschutzerklärung (Privacy Policy)
# v1.2 (2026-05-18) — section 6 "Speicherdauer" rewritten as an
# explicit per-category breakdown. The previous text claimed
# "Log-Daten in der Regel nach 30 Tagen gelöscht" which contradicted
# AUDIT_LOG_RETENTION_DAYS = 730 (24 months) in app/dsgvo_retention.py
# and was a hard Art. 13(2)(a) DSGVO transparency violation. The
# fix names every retention window the code actually implements
# (audit log, MCP log, consent snapshots, server logs) with their
# Art. 6 legal bases. Existing users get the ConsentRefreshModal
# on next login because privacy_version moves 1.1 → 1.2.
# v1.1 (2026-05-05) — section "Anonymisierte Nutzungsdaten" added
# alongside the v23.8 analytics opt-in.
PRIVACY_POLICY_VERSION: str = "1.2"
PRIVACY_POLICY_DATE: str = "2026-05-18"

# Allgemeine Geschäftsbedingungen (Terms of Service)
TERMS_VERSION: str = "1.0"
TERMS_DATE: str = "2026-04-27"
