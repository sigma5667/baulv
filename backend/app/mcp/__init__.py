"""MCP (Model Context Protocol) server for headless agents.

External clients — Claude Desktop, n8n, ChatGPT custom connectors —
connect to ``/mcp/sse`` (SSE) and present a Bearer credential. The
credential can be either:

* an interactive JWT (the same token the SPA uses), or
* a PAT minted via ``POST /api/auth/me/api-keys`` (recommended for
  long-running agents).

Both paths resolve to a ``User`` and the agent then operates with that
user's full privileges. There are no scopes in 3a — that's a 3b/3c
concern once the surface stabilises.

The mounted Starlette app exposes:

* ``GET  /mcp/sse``       — SSE handshake; auth validated, contextvar
                            set, then the MCP server runs in this task.
* ``POST /mcp/messages/`` — JSON-RPC messages from the client; auth
                            validated, then routed to the SSE task's
                            stream by ``session_id``.

Tools live in ``app.mcp.server`` and read the authenticated user from
a contextvar set by the SSE handler. Each tool opens its own
``AsyncSession`` so connection lifetimes track tool calls, not the
length of the SSE connection.
"""

from app.mcp.transport import build_mcp_app

__all__ = ["build_mcp_app"]
