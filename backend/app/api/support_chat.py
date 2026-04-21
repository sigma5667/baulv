"""Public support chat endpoint for the landing page.

Unauthenticated, rate-limited by client IP. The assistant answers
product questions about BauLV in plain German using a static system
prompt — no database, no sessions, no project context. Callers send
the full message history with every request; we never persist.

Rate limiting is in-memory sliding-window per IP. Good enough for
single-instance deploys (Railway's single-process worker model fits).
If we ever horizontally scale, swap for Redis — the contract of
``check_rate_limit`` stays the same.

Debugging:
  Every request logs a ``support_chat.start`` line with the IP, whether
  the API key is configured, and how many messages are in the payload.
  On success we log ``support_chat.claude_ok`` with usage stats. On
  failure we log the EXACT exception from the Anthropic SDK so Railway
  logs show the root cause (wrong model ID, invalid key, rate limit,
  network error, etc.) — the user-facing German error is intentionally
  generic.
"""

from __future__ import annotations

import logging
import time
from collections import defaultdict, deque
from pathlib import Path
from threading import Lock
from typing import Literal

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from app.config import settings

logger = logging.getLogger(__name__)

router = APIRouter()

SYSTEM_PROMPT = (
    Path(__file__).parent.parent / "chat" / "prompts" / "support_assistant.txt"
).read_text(encoding="utf-8")

# Model used for the support chat. Kept as a module-level constant
# so a swap is a one-line diff and it's visible in every log line.
# If Anthropic rejects this ID with a 404, Railway logs will show
# ``anthropic_api_error status=404`` pointing at it directly.
SUPPORT_CHAT_MODEL = "claude-sonnet-4-5"

GENERIC_UNAVAILABLE = (
    "Der Chat ist momentan nicht verfügbar. Bitte versuchen Sie es später erneut."
)

# ---------------------------------------------------------------------------
# Rate limiter
# ---------------------------------------------------------------------------

RATE_LIMIT_MAX = 20          # messages
RATE_LIMIT_WINDOW = 60 * 60  # seconds — 1 hour

_ip_requests: dict[str, deque[float]] = defaultdict(deque)
_ip_lock = Lock()


def _client_ip(request: Request) -> str:
    """Best-effort client IP. Prefers X-Forwarded-For (Railway sets it)."""
    fwd = request.headers.get("x-forwarded-for")
    if fwd:
        # First entry is the original client; rest are proxies.
        return fwd.split(",")[0].strip()
    if request.client:
        return request.client.host
    return "unknown"


def check_rate_limit(ip: str) -> tuple[bool, int]:
    """Return (allowed, retry_after_seconds). Sliding 1h window per IP."""
    now = time.time()
    cutoff = now - RATE_LIMIT_WINDOW
    with _ip_lock:
        q = _ip_requests[ip]
        while q and q[0] < cutoff:
            q.popleft()
        if len(q) >= RATE_LIMIT_MAX:
            retry_after = int(q[0] + RATE_LIMIT_WINDOW - now) + 1
            return False, max(retry_after, 1)
        q.append(now)
        return True, 0


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class SupportMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=4000)


class SupportChatRequest(BaseModel):
    # Last message is the new user question; prior messages provide
    # conversation context. Cap to avoid unbounded prompt growth —
    # 40 entries (~20 turns) is already more than a support chat needs.
    messages: list[SupportMessage] = Field(min_length=1, max_length=40)


class SupportChatResponse(BaseModel):
    reply: str


# ---------------------------------------------------------------------------
# Endpoint
# ---------------------------------------------------------------------------


@router.post("/support-chat", response_model=SupportChatResponse)
async def support_chat(payload: SupportChatRequest, request: Request) -> SupportChatResponse:
    """Public, rate-limited support chat.

    Never raises to the caller with internal detail — on every backend
    or Claude failure we return a plain-German generic fallback so the
    widget always has something to show. The ROOT CAUSE is always in
    Railway logs, keyed by the ``support_chat.*`` log names.
    """
    ip = _client_ip(request)
    allowed, retry_after = check_rate_limit(ip)
    if not allowed:
        minutes = max(retry_after // 60, 1)
        raise HTTPException(
            status_code=429,
            detail=f"Zu viele Anfragen. Bitte versuchen Sie es in {minutes} Minute(n) erneut.",
            headers={"Retry-After": str(retry_after)},
        )

    # --- Diagnostics block 1: what did the request look like? -------------
    api_key = settings.anthropic_api_key or ""
    key_present = bool(api_key.strip())
    # Log key presence + length only (never the key itself), so Railway
    # logs can distinguish "unset" from "set but wrong".
    logger.info(
        "support_chat.start ip=%s key_present=%s key_len=%d msgs=%d model=%s",
        ip,
        key_present,
        len(api_key),
        len(payload.messages),
        SUPPORT_CHAT_MODEL,
    )

    if not key_present:
        logger.error(
            "support_chat.no_api_key — ANTHROPIC_API_KEY is not set on this deploy"
        )
        raise HTTPException(status_code=503, detail=GENERIC_UNAVAILABLE)

    if payload.messages[-1].role != "user":
        raise HTTPException(
            status_code=400, detail="Letzte Nachricht muss vom Benutzer stammen."
        )

    # Defensive: basic format check so the "wrong shape" case is visible
    # in logs rather than surfacing as a generic 503.
    if not api_key.startswith("sk-ant-"):
        logger.warning(
            "support_chat.suspicious_key_prefix — expected 'sk-ant-' prefix, got %r (first 8 chars)",
            api_key[:8],
        )

    try:
        import anthropic

        client = anthropic.AsyncAnthropic(api_key=api_key)

        # Prompt-cache the system prompt — it never changes between
        # requests, so we pay full cost once per 5-minute TTL and read
        # from cache after that (~90 % cheaper). Required shape for
        # cache_control is a list of text blocks, not a bare string.
        system_blocks = [
            {
                "type": "text",
                "text": SYSTEM_PROMPT,
                "cache_control": {"type": "ephemeral"},
            }
        ]
        claude_messages = [
            {"role": m.role, "content": m.content} for m in payload.messages
        ]

        logger.info(
            "support_chat.calling_claude model=%s system_prompt_chars=%d first_user_msg_chars=%d",
            SUPPORT_CHAT_MODEL,
            len(SYSTEM_PROMPT),
            len(payload.messages[-1].content),
        )

        started = time.time()
        response = await client.messages.create(
            model=SUPPORT_CHAT_MODEL,
            max_tokens=2000,
            system=system_blocks,
            messages=claude_messages,
        )
        elapsed_ms = int((time.time() - started) * 1000)

        usage = getattr(response, "usage", None)
        logger.info(
            "support_chat.claude_ok stop_reason=%s elapsed_ms=%d "
            "input_tokens=%s output_tokens=%s cache_read=%s cache_write=%s",
            getattr(response, "stop_reason", None),
            elapsed_ms,
            getattr(usage, "input_tokens", None),
            getattr(usage, "output_tokens", None),
            getattr(usage, "cache_read_input_tokens", None),
            getattr(usage, "cache_creation_input_tokens", None),
        )

        reply = response.content[0].text if response.content else ""
        if not reply.strip():
            logger.error(
                "support_chat.empty_reply stop_reason=%s content=%r",
                getattr(response, "stop_reason", None),
                response.content,
            )
            raise HTTPException(status_code=503, detail=GENERIC_UNAVAILABLE)
        return SupportChatResponse(reply=reply)

    except HTTPException:
        raise
    except Exception as e:  # noqa: BLE001 — deliberately broad
        # Log the EXACT exception from the SDK so Railway shows the
        # root cause. We differentiate known Anthropic error classes
        # with getattr so older SDK versions that don't expose these
        # still fall through to the generic branch.
        anthropic_err_names = {
            "APIStatusError",
            "APIConnectionError",
            "APITimeoutError",
            "AuthenticationError",
            "PermissionDeniedError",
            "NotFoundError",
            "BadRequestError",
            "RateLimitError",
            "InternalServerError",
        }
        err_class = type(e).__name__
        status_code = getattr(e, "status_code", None)
        body = getattr(e, "body", None)
        message = getattr(e, "message", None) or str(e)

        if err_class in anthropic_err_names:
            logger.error(
                "support_chat.anthropic_error class=%s status=%s message=%s body=%r",
                err_class,
                status_code,
                message,
                body,
            )
        else:
            # logger.exception includes a full traceback — we want it
            # for anything unexpected (e.g. JSON encoding bug, import
            # error, network stack issue).
            logger.exception(
                "support_chat.unhandled_error class=%s message=%s",
                err_class,
                message,
            )
        raise HTTPException(status_code=503, detail=GENERIC_UNAVAILABLE)
