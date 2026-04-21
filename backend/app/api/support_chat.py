"""Public support chat endpoint for the landing page.

Unauthenticated, rate-limited by client IP. The assistant answers
product questions about BauLV in plain German using a static system
prompt — no database, no sessions, no project context. Callers send
the full message history with every request; we never persist.

Rate limiting is in-memory sliding-window per IP. Good enough for
single-instance deploys (Railway's single-process worker model fits).
If we ever horizontally scale, swap for Redis — the contract of
``check_rate_limit`` stays the same.
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

    Never raises to the caller — on every backend/Claude failure we
    return a plain-German fallback so the widget always has something
    to show.
    """
    ip = _client_ip(request)
    allowed, retry_after = check_rate_limit(ip)
    if not allowed:
        minutes = max(retry_after // 60, 1)
        raise HTTPException(
            status_code=429,
            detail=(
                f"Zu viele Anfragen. Bitte versuchen Sie es in {minutes} "
                f"Minute(n) erneut oder schreiben Sie uns an [EMAIL_PLATZHALTER]."
            ),
            headers={"Retry-After": str(retry_after)},
        )

    if not settings.anthropic_api_key:
        logger.error("support_chat called but ANTHROPIC_API_KEY is not configured")
        raise HTTPException(
            status_code=503,
            detail=(
                "Der Chat ist momentan nicht verfügbar. Bitte schreiben "
                "Sie uns an [EMAIL_PLATZHALTER]."
            ),
        )

    # Last message must be from the user — otherwise the caller is
    # confused about the turn structure.
    if payload.messages[-1].role != "user":
        raise HTTPException(status_code=400, detail="Letzte Nachricht muss vom Benutzer stammen.")

    try:
        import anthropic

        client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)

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

        response = await client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=2000,
            system=system_blocks,
            messages=claude_messages,
        )
        reply = response.content[0].text if response.content else ""
        if not reply.strip():
            raise ValueError("empty reply")
        return SupportChatResponse(reply=reply)

    except HTTPException:
        raise
    except Exception as e:  # noqa: BLE001 — we want to catch everything
        logger.exception("support_chat failed: %s", e)
        raise HTTPException(
            status_code=503,
            detail=(
                "Der Chat ist momentan nicht verfügbar. Bitte schreiben "
                "Sie uns an [EMAIL_PLATZHALTER]."
            ),
        )
