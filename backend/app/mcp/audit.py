"""Audit-log writer for MCP tool dispatches.

Called from ``app.mcp.server.call_tool`` once per dispatch — exactly
one row per (tool_name, request) tuple, recording outcome, latency,
and a sanitised copy of the arguments. Written on its own short-lived
session so a failure here can't poison the tool-call's session.

Why a separate session
======================

The tool handler may have already committed (mutations) or never
opened a transaction (read tools). We don't want our audit-write to
participate in the handler's transaction lifecycle:

* For mutations, the handler commits the business write *before*
  returning — bundling the audit insert into that transaction would
  mean an audit-write failure rolls back the tool's effect, which is
  the wrong tradeoff (we want the trail to be best-effort, not
  load-bearing).
* For reads, there is no transaction in flight, so reusing it doesn't
  even make sense.

Each audit-write opens its own session, inserts, commits, closes.
Failures are caught and logged at WARNING — they never propagate up
into the dispatcher. The user's tool call result is unchanged.

Argument sanitization
=====================

We persist a JSONB copy of the arguments dict so the frontend viewer
can render structured calls. To bound row size we clip:

* String values > 500 chars → truncated with ``…[N more chars]``
* List/dict values > ``MAX_NESTED_BYTES`` after JSON-encoding → replaced
  with ``"<too large>"``

This is enough to keep one tool call from blowing up audit storage if
an agent ever passes a full PDF blob in. We do **not** redact secrets
here — there is no "secret" arg type in our tool schemas (passwords
go via JWT/PAT, never as tool args).
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any
from uuid import UUID

from app.db.models.mcp_audit import (
    RESULT_ERROR,
    RESULT_OK,
    RESULT_RATE_LIMITED,
    McpAuditLogEntry,
)
from app.db.session import async_session_factory


logger = logging.getLogger(__name__)


# Per-string clip — most tool args are short identifiers or labels;
# anything past this is a payload that doesn't need to live in the
# audit row.
_MAX_STRING_CHARS = 500

# Per-nested-value clip after JSON-encoding. 4 KB is generous for a
# normal tool arg and tight enough to stop a runaway upload.
_MAX_NESTED_BYTES = 4096


def _clip_value(value: Any) -> Any:
    """Return ``value`` clipped to fit comfortably in a JSONB column."""
    if isinstance(value, str):
        if len(value) > _MAX_STRING_CHARS:
            return (
                value[:_MAX_STRING_CHARS]
                + f"…[{len(value) - _MAX_STRING_CHARS} more chars]"
            )
        return value
    if isinstance(value, (list, dict)):
        try:
            encoded = json.dumps(value, default=str)
        except (TypeError, ValueError):
            return "<unserialisable>"
        if len(encoded) > _MAX_NESTED_BYTES:
            return "<too large>"
        return value
    return value


def sanitize_arguments(arguments: dict | None) -> dict | None:
    """Make a JSONB-safe copy of the tool arguments.

    Returns ``None`` if input is ``None`` or empty (no point storing
    ``{}``). The output is always JSON-serialisable.
    """
    if not arguments:
        return None
    return {key: _clip_value(value) for key, value in arguments.items()}


def now_monotonic_ms() -> float:
    """High-resolution monotonic clock in milliseconds.

    Used as the start anchor for latency measurement. We use
    ``time.monotonic`` (not ``time.time``) so an NTP jump during a
    long-running tool call doesn't produce a negative latency.
    """
    return time.monotonic() * 1000.0


async def write_audit_entry(
    *,
    user_id: UUID | None,
    api_key_id: UUID | None,
    tool_name: str,
    arguments: dict | None,
    result: str,
    error_message: str | None,
    latency_ms: int,
) -> None:
    """Insert one ``McpAuditLogEntry`` row.

    Best-effort: any failure is logged at WARNING and swallowed. The
    caller's tool result is unaffected. Opens its own session so this
    is safe to call after the tool's own session has been committed
    or rolled back.
    """
    # Sanity-clip ``error_message`` — Python tracebacks can be long,
    # but multi-megabyte ones are pathological. 8 KB is plenty for
    # one stack to be diagnosable.
    clipped_error = (
        error_message[:8192] if error_message is not None else None
    )

    try:
        async with async_session_factory() as db:
            db.add(
                McpAuditLogEntry(
                    user_id=user_id,
                    api_key_id=api_key_id,
                    tool_name=tool_name[:64],  # column is varchar(64)
                    arguments=sanitize_arguments(arguments),
                    result=result,
                    error_message=clipped_error,
                    latency_ms=int(latency_ms),
                )
            )
            await db.commit()
    except Exception as exc:  # pragma: no cover - best-effort path
        logger.warning(
            "mcp.audit_write_failed tool=%s user=%s err=%s",
            tool_name,
            user_id,
            exc,
        )


# Re-export the result tags so call sites import from one place.
__all__ = [
    "RESULT_OK",
    "RESULT_ERROR",
    "RESULT_RATE_LIMITED",
    "now_monotonic_ms",
    "sanitize_arguments",
    "write_audit_entry",
]
