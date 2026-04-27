"""Append-only audit log for MCP tool dispatches.

Every successful or failed call into the MCP tool dispatcher writes
exactly one row here. Read paths:

* The user can browse their own history at
  ``GET /api/auth/me/api-keys/{key_id}/audit`` — paginated, scoped
  to a single key (or "all of mine" without the key filter).
* Future operator dashboards can aggregate across users for capacity
  planning, abuse detection, and tool-usage analytics.

Why a separate table from ``audit_log_entries``
===============================================

The two surfaces look superficially similar but their access patterns
diverge enough that mixing them would harm both:

* **Volume.** ``audit_log_entries`` records ~handful of events per user
  per session (login, settings change, password reset). MCP audit
  records every tool call — an n8n loop can hammer ``mcp.lv.list`` ten
  times a minute. The MCP table will be 100-1000× larger and benefits
  from independent indexing / vacuuming / retention policies.
* **Shape.** ``audit_log_entries.meta`` is a free-form JSONB blob keyed
  by event_type. MCP audit has a fixed columnar shape (tool_name,
  arguments, result, latency_ms) that the frontend renders as a table —
  promoting these to first-class columns lets us index ``tool_name`` /
  ``result`` later without reshaping JSONB.
* **Identity.** MCP audit tracks ``api_key_id`` as well as ``user_id``
  so the user can answer "what did *this* PAT do" without joining
  through the activity log. ``audit_log_entries`` doesn't have that
  link.

Both ``user_id`` and ``api_key_id`` use ON DELETE SET NULL so that
account/key deletion does not erase the historic record — same DSGVO
Art. 32 reasoning that motivates ``AuditLogEntry``.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


# Result discriminator strings. Kept in sync with the dispatcher writer
# in ``app.mcp.server``. Promoted to constants so a typo in one place
# can't quietly produce orphaned values nobody filters on.
RESULT_OK = "ok"
RESULT_ERROR = "error"
RESULT_RATE_LIMITED = "rate_limited"


class McpAuditLogEntry(Base):
    __tablename__ = "mcp_audit_log_entries"

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    # Both nullable + SET NULL: see module docstring.
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    api_key_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("api_keys.id", ondelete="SET NULL"),
        nullable=True,
    )
    # MCP tool identifier — e.g. ``mcp.lv.list`` or
    # ``mcp.position.update``. Length cap matches the longest currently
    # registered tool name with comfortable headroom for new ones.
    tool_name: Mapped[str] = mapped_column(String(64), nullable=False)
    # Sanitised argument blob. Large fields (file contents, full PDFs)
    # are clipped by the writer before we get here so a single malformed
    # call can't blow up the row size. JSONB so the frontend can render
    # structured deltas instead of opaque text.
    arguments: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB, nullable=True
    )
    # Outcome tag — see ``RESULT_*`` constants.
    result: Mapped[str] = mapped_column(String(16), nullable=False)
    # Stack-trace head / error string when result != "ok". NULL on
    # success. Kept as TEXT (no length cap) since Python tracebacks
    # can be long but always stop fitting in 1KB.
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Wall-clock latency captured around the dispatcher invocation.
    # Reported to the user in the audit viewer to spot slow tools on
    # their own data without us shipping a separate metrics surface.
    latency_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.utcnow, index=True
    )
