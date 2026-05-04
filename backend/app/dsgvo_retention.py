"""Canonical retention periods for DSGVO Art. 5 Abs. 1 lit. e
("Speicherbegrenzung" / storage limitation).

DSGVO requires personal data to be kept "no longer than necessary
for the purposes for which it is processed". For audit logs, the
practical sweet spot in Austrian B2B SaaS is **24 months** —
long enough to investigate complaints retroactively, short enough
that a regulator's "are you keeping the bare minimum?" question
gets a clean answer.

Bumping a retention period
==========================

Changing one of these constants is a deliberate two-step:

  1. Update the constant here. Tests in
     ``tests/test_api/test_audit_cleanup.py`` pin the values, so
     a refactor can't roll them back silently.
  2. Update the user-visible hint in the SPA's audit-log viewer
     (``ApiKeysPage.tsx`` and the profile audit table) so the
     "wir bewahren Logs N Monate auf"-text matches reality.

What is NOT subject to retention
================================

``consent_snapshots`` rows are explicitly excluded from auto-
deletion. DSGVO Art. 7 Abs. 1 ("der Verantwortliche muss die
Einwilligung nachweisen können") creates an *opposing* obligation
to Art. 5(1)(e) — the controller must be able to demonstrate
consent for as long as the consent itself is relevant, even after
the user is deleted. The constant is set to ``None`` here as a
documentation marker; the cleanup service simply doesn't touch
the table.
"""

from __future__ import annotations

# Canonical audit log (login, register, password change, account
# delete, data export, plan deletion, privacy update, …). Standard
# 24-month window. ``730 days`` rather than ``2 * 365 days`` because
# the comparison is against absolute timestamps, not calendar months,
# and 730 is what Postgres' ``INTERVAL '730 days'`` will evaluate to.
AUDIT_LOG_RETENTION_DAYS: int = 730

# MCP tool-dispatch log (per-PAT tool call history). Same 24-month
# window — the data shape is different (tool name + sanitised args)
# but the regulatory rationale is identical.
MCP_AUDIT_LOG_RETENTION_DAYS: int = 730

# Consent snapshots are NOT auto-deleted — see module docstring.
# Set as ``None`` so a future caller that inspects this module
# programmatically can tell "no retention" apart from "value not
# yet decided".
CONSENT_SNAPSHOT_RETENTION_DAYS: int | None = None
