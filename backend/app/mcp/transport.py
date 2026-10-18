"""ASGI transport that puts the MCP ``Server`` on the wire.

We expose two endpoints under the ``/mcp`` mount point:

* ``GET /mcp/sse``       — long-lived SSE handshake. The client opens
                           this once per session; the server uses it
                           as the outbound channel.
* ``POST /mcp/messages/`` — JSON-RPC envelope that the client uses to
                           send tool calls / requests *into* the server.
                           ``SseServerTransport`` routes the body to
                           the matching SSE task by ``session_id``.

Both endpoints sit behind the same Bearer auth check. The GET handler
*also* binds the user_id into ``current_user_id_var`` for the duration
of the SSE session — that's the variable tool handlers in
``app.mcp.server`` read back to know which tenant they're acting for.
The POST handler doesn't need the contextvar (the tool actually runs
in the GET task), but it still needs auth so anonymous traffic can't
hijack a stream — and both handlers put the principal on
``scope["user"]`` so the SDK only routes a POST into a session opened
by the *same* credential (see ``_bind_principal_to_scope``).

Why a Starlette sub-app and not direct FastAPI routes
=====================================================

``SseServerTransport`` ships its own POST handler (``handle_post_message``)
as a raw ASGI callable. Wrapping it as a FastAPI route would
double-parse the body and break the JSON-RPC framing. Mounting a
Starlette app sidesteps that and is the pattern the upstream MCP
examples follow.
"""

from __future__ import annotations

import logging
from typing import Awaitable, Callable

from mcp.server.auth.middleware.bearer_auth import AuthenticatedUser
from mcp.server.auth.provider import AccessToken
from mcp.server.sse import SseServerTransport
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Mount, Route
from starlette.types import Receive, Scope, Send

from app.db.session import async_session_factory
from app.mcp.principal import (
    Principal,
    current_api_key_id_var,
    current_user_id_var,
    resolve_principal,
)
from app.mcp.server import server


logger = logging.getLogger(__name__)


# Absolute path the SSE transport advertises to the client as the POST
# target. Must match where we mount the inner messages app under the
# main FastAPI tree (see ``main.py``: ``app.mount("/mcp", ...)``). If
# you change the mount prefix, change this too — the client will POST
# to whatever path the SSE handshake hands it.
_MESSAGES_PATH = "/mcp/messages/"


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------


def _extract_bearer(scope: Scope) -> str | None:
    """Pull a Bearer token off the ASGI request headers.

    We work directly off ``scope["headers"]`` rather than wrapping in
    a ``Request`` because this runs in two places — the SSE GET
    handler and the POST middleware — and the scope-level access is
    the common denominator.
    """
    for key, value in scope.get("headers", []):
        if key.lower() == b"authorization":
            try:
                decoded = value.decode("latin-1")
            except UnicodeDecodeError:
                return None
            parts = decoded.split(None, 1)
            if len(parts) == 2 and parts[0].lower() == "bearer":
                return parts[1].strip()
            return None
    return None


async def _authenticate_scope(scope: Scope):
    """Resolve the request's Bearer token to a ``Principal`` or ``None``.

    Opens its own session — auth runs before any tool handler, so we
    don't have a request-scoped session to reuse. The session is
    closed when the ``async with`` exits; resolving may touch
    ``last_used_at``, so we commit before returning so the touch is
    durable even if the surrounding SSE task lives for hours.
    """
    token = _extract_bearer(scope)
    if not token:
        return None
    async with async_session_factory() as db:
        principal = await resolve_principal(token, db)
        # ``verify_pat_with_key`` flushes ``last_used_at`` but doesn't
        # commit — we own the transaction here, so close it cleanly.
        if principal is not None:
            await db.commit()
        return principal


def _bind_principal_to_scope(scope: Scope, principal: Principal) -> None:
    """Expose the principal to the SDK as ``scope["user"]``.

    Security-Fix (Review 2026-10): ``SseServerTransport`` records the
    ``authorization_context`` of whoever opened a session and rejects
    every POST whose credential differs (404, as if the session did
    not exist). That check only runs when ``scope["user"]`` is an
    ``AuthenticatedUser`` — without it, owner and caller are both
    ``None`` and *any* valid BauLV credential could drive someone
    else's open session (e.g. after the victim revoked a leaked PAT).

    ``client_id`` names the concrete credential (one PAT, or the JWT
    path), ``subject`` the user. A revoked/expired PAT already fails
    ``_authenticate_scope`` with 401 before we get here, so an open
    session dies with its key.
    """
    if principal.api_key is not None:
        client_id = f"pat:{principal.api_key.id}"
    else:
        client_id = "jwt"
    scope["user"] = AuthenticatedUser(
        AccessToken(
            # The raw bearer value is deliberately NOT kept here — the
            # SDK only compares client_id / issuer / subject.
            token="",
            client_id=client_id,
            scopes=[],
            subject=str(principal.user.id),
        )
    )


_UNAUTHORIZED_BODY = b'{"detail":"Nicht authentifiziert"}'
_UNAUTHORIZED_HEADERS = [
    (b"content-type", b"application/json; charset=utf-8"),
    # Tell the client what scheme to use on retry. Any value is fine
    # here — we accept both JWT and PAT under "Bearer".
    (b"www-authenticate", b'Bearer realm="baulv-mcp"'),
]


async def _send_401(send: Send) -> None:
    await send(
        {
            "type": "http.response.start",
            "status": 401,
            "headers": _UNAUTHORIZED_HEADERS,
        }
    )
    await send({"type": "http.response.body", "body": _UNAUTHORIZED_BODY})


# ---------------------------------------------------------------------------
# SSE GET handler
# ---------------------------------------------------------------------------


def _make_sse_handler(
    sse: SseServerTransport,
) -> Callable[[Request], Awaitable[Response]]:
    """Build the ``GET /sse`` endpoint bound to a specific transport.

    The closure captures ``sse`` so we can mount multiple instances in
    tests if we ever need to. In production there's exactly one.
    """

    async def handle_sse(request: Request) -> Response:
        principal = await _authenticate_scope(request.scope)
        if principal is None:
            logger.info(
                "mcp.sse_unauthorized client=%s",
                request.client.host if request.client else "?",
            )
            return JSONResponse(
                {"detail": "Nicht authentifiziert"},
                status_code=401,
                headers={"WWW-Authenticate": 'Bearer realm="baulv-mcp"'},
            )

        _bind_principal_to_scope(request.scope, principal)
        user = principal.user
        api_key_id = principal.api_key.id if principal.api_key else None

        # Bind the user *and* api_key for the lifetime of this SSE
        # task. Every tool call dispatched from the inner ``server.run``
        # loop reads ``user_id`` back via ``get_current_user_id()`` and
        # ``api_key_id`` via ``get_current_api_key_id()`` (the latter
        # for rate-limiting + audit-log keying — None on JWT auth).
        user_token = current_user_id_var.set(user.id)
        api_key_token = current_api_key_id_var.set(api_key_id)
        logger.info(
            "mcp.sse_connected user_id=%s api_key_id=%s client=%s",
            user.id,
            api_key_id,
            request.client.host if request.client else "?",
        )
        try:
            # The MCP SDK's SSE transport returns a tuple
            # (read_stream, write_stream). ``server.run`` consumes
            # the read stream until the client disconnects.
            async with sse.connect_sse(
                request.scope,
                request.receive,
                # ``Request._send`` is the documented way to obtain
                # the underlying ASGI send callable in Starlette;
                # the public ``Request`` API has no equivalent because
                # response sending normally goes through the
                # ``Response`` abstraction. ``connect_sse`` writes
                # raw SSE frames so it needs the bare callable.
                request._send,
            ) as (read_stream, write_stream):
                await server.run(
                    read_stream,
                    write_stream,
                    server.create_initialization_options(),
                )
        except Exception:
            # Any unhandled error inside the SSE loop should be
            # logged but not re-raised — the connection is half-
            # closed by the time we get here, so re-raising would
            # just produce an opaque traceback in the user's logs.
            logger.exception(
                "mcp.sse_run_failed user_id=%s", user.id
            )
        finally:
            # Reset in reverse order. ``contextvars`` doesn't actually
            # care about ordering, but mirroring the set/reset stack
            # makes the lifetime explicit if anyone reads this code.
            current_api_key_id_var.reset(api_key_token)
            current_user_id_var.reset(user_token)
            logger.info("mcp.sse_disconnected user_id=%s", user.id)

        # Starlette expects the route to return a Response; the
        # SSE body has already been streamed out at this point so
        # an empty response is correct (the underlying ASGI send
        # has been used for the actual wire output).
        return Response(status_code=200)

    return handle_sse


# ---------------------------------------------------------------------------
# POST /messages/ — auth wrapper around the SDK's ASGI handler
# ---------------------------------------------------------------------------


def _wrap_messages_with_auth(
    inner: Callable[[Scope, Receive, Send], Awaitable[None]],
) -> Callable[[Scope, Receive, Send], Awaitable[None]]:
    """Gate ``SseServerTransport.handle_post_message`` behind Bearer auth.

    The SDK's POST handler is a bare ASGI callable, so we can't use
    FastAPI dependencies here. We resolve the principal, 401 on
    failure, and forward to the SDK on success.

    The contextvar is intentionally *not* set on the POST path: the
    tool actually runs in the SSE GET task, which already has its
    own binding. Setting it here would either be a no-op (different
    task) or a confusing override during a multi-tenant swap.
    """

    async def app(scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            # Pass-through for any non-HTTP traffic (lifespan etc.).
            await inner(scope, receive, send)
            return

        principal = await _authenticate_scope(scope)
        if principal is None:
            await _send_401(send)
            return

        # Session-ownership: the SDK compares this against the
        # credential that opened the SSE stream (see
        # ``_bind_principal_to_scope``).
        _bind_principal_to_scope(scope, principal)
        await inner(scope, receive, send)

    return app


# ---------------------------------------------------------------------------
# Public factory
# ---------------------------------------------------------------------------


def build_mcp_app() -> Starlette:
    """Build the ASGI app to mount under ``/mcp``.

    Returns a fresh Starlette instance each call. We keep the MCP
    ``Server`` singleton at the module level (its handler tables are
    process-global), but the *transport* is cheap to recreate, and a
    fresh ``SseServerTransport`` per app simplifies test isolation.
    """
    sse = SseServerTransport(_MESSAGES_PATH)

    return Starlette(
        routes=[
            Route(
                "/sse",
                endpoint=_make_sse_handler(sse),
                methods=["GET"],
            ),
            Mount(
                "/messages/",
                app=_wrap_messages_with_auth(sse.handle_post_message),
            ),
        ]
    )
