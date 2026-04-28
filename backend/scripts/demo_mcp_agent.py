"""End-to-end demo of the BauLV MCP integration.

Drives Claude (via the official Anthropic SDK) against our ``/mcp/sse``
endpoint to show what a customer sees when they hook Claude Desktop /
n8n / a custom automation up to BauLV. The script is also useful as a
post-deploy smoke test: a clean run validates auth, the SSE
handshake, the tool catalogue, and at least one round-trip tool call.

Two flavours
============

By default the script runs a **read-only** flow that asks Claude for a
status report on the user's first project. This is safe to run as
often as you like — no rows are written.

With ``--write`` the script runs the **mutation** flow: it creates a
demo project ("MCP Demo <timestamp>"), looks up a Maler-Vorlage and
copies it into the new project as an LV. The demo project stays in
your account afterwards (we deliberately do not expose deletes via
MCP); clean it up in the web UI when you're done.

Usage
=====

Run from inside the ``backend/`` directory, with the backend venv
activated (``mcp`` and ``anthropic`` are already in
``pyproject.toml`` — no extra install needed)::

    export BAULV_PAT=pat_xxxxxxxxxxxxxxxxxxxxxxxxxxxx
    export ANTHROPIC_API_KEY=sk-ant-xxxxxxxxxxxxxxxxxxxxxxxxxxxx
    # optional: export BAULV_BASE_URL=https://baulv-production.up.railway.app

    python scripts/demo_mcp_agent.py              # read-only
    python scripts/demo_mcp_agent.py --write      # writing demo
    python scripts/demo_mcp_agent.py --prompt 'Liste meine Projekte.'

Why this lives in-tree
======================

Sales / investor demos want a "from prompt to populated LV in 30
seconds" video. That story lives or dies on whether the integration
holds together end-to-end, so we keep the demo runnable from the same
repo it tests — that way it can never silently rot away from the
real codebase.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from typing import Any

import anthropic
from mcp.client.session import ClientSession
from mcp.client.sse import sse_client


# claude-opus-4-7 is the current Anthropic flagship — adaptive thinking
# only, no temperature / top_p / top_k. See ``CLAUDE.md`` notes if this
# string ever needs updating; do NOT append a date suffix.
MODEL = "claude-opus-4-7"

# Tool-use loops can spike output tokens hard if Claude decides to
# narrate every step. 8K is a comfortable per-turn cap that still
# allows long final summaries.
MAX_TOKENS = 8192


# ---------------------------------------------------------------------------
# Tiny ANSI helpers — purely cosmetic, the script works fine without them
# ---------------------------------------------------------------------------


class _C:
    BLUE = "\033[34m"
    CYAN = "\033[36m"
    GRAY = "\033[90m"
    GREEN = "\033[32m"
    YELLOW = "\033[33m"
    RED = "\033[31m"
    BOLD = "\033[1m"
    END = "\033[0m"


def _section(title: str) -> None:
    print(f"\n{_C.BOLD}{_C.BLUE}── {title} ──{_C.END}")


def _info(text: str) -> None:
    print(f"{_C.CYAN}{text}{_C.END}")


def _gray(text: str) -> None:
    print(f"{_C.GRAY}{text}{_C.END}")


def _good(text: str) -> None:
    print(f"{_C.GREEN}{text}{_C.END}")


def _warn(text: str) -> None:
    print(f"{_C.YELLOW}{text}{_C.END}")


def _err(text: str) -> None:
    print(f"{_C.RED}{text}{_C.END}", file=sys.stderr)


def _truncate(text: str, max_chars: int = 280) -> str:
    """Shorten long tool results for the on-screen preview.

    The full result still goes back into Claude's context — this only
    trims what we *print* to the operator's terminal so the demo
    output stays readable.
    """
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + f"… [{len(text) - max_chars} weitere Zeichen]"


# ---------------------------------------------------------------------------
# Default prompts — German, customer-facing
# ---------------------------------------------------------------------------


READ_PROMPT = """Gib mir bitte einen kurzen deutschsprachigen Status-Bericht zu meinem
ersten BauLV-Projekt. Liste dazu zuerst meine Projekte, wähle das
zuletzt aktualisierte aus, hol dir die Stammdaten und die
LV-Übersicht und fasse alles in maximal fünf Sätzen zusammen.

Wenn ich gerade kein Projekt angelegt habe, sag das einfach klar."""


def _write_prompt() -> str:
    """Build the writing-flow prompt with a fresh timestamp.

    Lazy so the timestamp matches the run, not the import — relevant
    when a CI process imports the module long before invoking it.
    """
    stamp = time.strftime("%Y-%m-%d %H:%M")
    return f"""Lege bitte ein neues Test-Projekt mit dem Namen
"MCP Demo {stamp}" an. Suche danach unter den verfügbaren
LV-Vorlagen eine für das Gewerk Malerarbeiten (System-Vorlage
bevorzugt) und erstelle aus dieser Vorlage ein LV im neuen Projekt
unter dem Namen "Malerarbeiten".

Gib am Ende einen kurzen deutschsprachigen Bericht heraus, was du
angelegt hast: Projekt-ID, LV-ID und Anzahl der kopierten
Positionen."""


# ---------------------------------------------------------------------------
# MCP ↔ Anthropic glue
# ---------------------------------------------------------------------------


def _mcp_tool_to_anthropic(tool: Any) -> dict:
    """Convert one ``mcp.types.Tool`` entry to Anthropic's tool format.

    The two specs differ in two places worth noting:

    * MCP uses ``inputSchema`` (camelCase) while Anthropic uses
      ``input_schema`` (snake_case). Trivial rename.
    * MCP allows ``description=None``; Anthropic requires a string.
      We fall back to an empty string — Claude still has the schema
      to lean on, and our MCP catalogue carries descriptions anyway.
    """
    return {
        "name": tool.name,
        "description": tool.description or "",
        "input_schema": tool.inputSchema,
    }


# ---------------------------------------------------------------------------
# Main runner
# ---------------------------------------------------------------------------


async def run_demo(*, base_url: str, pat: str, prompt: str) -> int:
    sse_url = f"{base_url.rstrip('/')}/mcp/sse"
    headers = {"Authorization": f"Bearer {pat}"}

    _section(f"Verbinde zu {sse_url}")
    try:
        # ``sse_client`` opens the long-lived SSE GET; ``ClientSession``
        # layers JSON-RPC framing on top via the messages POST channel.
        async with sse_client(sse_url, headers=headers) as (read, write):
            async with ClientSession(read, write) as session:
                t0 = time.monotonic()
                await session.initialize()
                tools_response = await session.list_tools()
                mcp_tools = tools_response.tools
                init_ms = int((time.monotonic() - t0) * 1000)
                _good(
                    f"verbunden in {init_ms} ms — "
                    f"{len(mcp_tools)} Tools verfügbar"
                )
                for t in mcp_tools:
                    _gray(f"  • {t.name}")

                return await _drive_claude(
                    session=session,
                    mcp_tools=mcp_tools,
                    prompt=prompt,
                )
    except Exception as exc:  # noqa: BLE001 — we want a clean exit code
        _err(
            "Verbindung zum MCP-Server fehlgeschlagen. "
            f"Ursache: {exc}"
        )
        _err(
            "Häufige Ursachen: ungültiger oder abgelaufener Token, "
            "falscher BAULV_BASE_URL, Server nicht erreichbar."
        )
        return 1


async def _drive_claude(
    *,
    session: ClientSession,
    mcp_tools: list[Any],
    prompt: str,
) -> int:
    """Run the tool-use loop with Claude as the orchestrator.

    Standard Anthropic pattern: ``messages.stream`` with
    ``thinking={"type": "adaptive"}`` (mandatory on Opus 4.7), wait
    for the final message, branch on ``stop_reason``:

    * ``end_turn`` — Claude is finished, print and exit.
    * ``tool_use`` — execute every tool_use block via MCP, append the
      results to the messages list, loop.

    Crucially we re-append ``response.content`` *as-is* into the next
    turn. That preserves any ``thinking`` blocks the model emitted —
    the API rejects subsequent calls if those go missing while
    adaptive thinking is on.
    """
    _section("Claude führt den Auftrag aus")
    _info(prompt)

    anthropic_tools = [_mcp_tool_to_anthropic(t) for t in mcp_tools]
    client = anthropic.AsyncAnthropic()

    messages: list[dict[str, Any]] = [{"role": "user", "content": prompt}]
    iteration = 0
    total_input = 0
    total_output = 0
    start = time.monotonic()

    # Hard upper bound on the loop. Claude almost never needs more
    # than a handful of tool calls for our flows; if it spirals past
    # this it's a sign of a prompt/tool-schema bug, not an honest
    # workload — better to abort than to burn tokens.
    MAX_ITERATIONS = 20

    while iteration < MAX_ITERATIONS:
        iteration += 1
        try:
            async with client.messages.stream(
                model=MODEL,
                max_tokens=MAX_TOKENS,
                thinking={"type": "adaptive"},
                tools=anthropic_tools,
                messages=messages,
            ) as stream:
                response = await stream.get_final_message()
        except anthropic.APIError as exc:
            _err(f"Anthropic-API-Fehler in Iteration {iteration}: {exc}")
            return 1

        total_input += response.usage.input_tokens
        total_output += response.usage.output_tokens

        if response.stop_reason == "end_turn":
            _section("Antwort des Agents")
            for block in response.content:
                if block.type == "text":
                    print(block.text)
            break

        if response.stop_reason != "tool_use":
            _warn(
                f"Unerwarteter stop_reason: {response.stop_reason} — "
                "Schleife wird beendet."
            )
            break

        # The assistant turn must be appended verbatim so that any
        # ``thinking`` blocks survive — see docstring above.
        messages.append({"role": "assistant", "content": response.content})

        tool_results: list[dict[str, Any]] = []
        for block in response.content:
            if block.type != "tool_use":
                continue
            tool_results.append(
                await _execute_one_tool(session, block, iteration)
            )

        messages.append({"role": "user", "content": tool_results})
    else:
        _warn(
            f"Iterations-Limit ({MAX_ITERATIONS}) erreicht — "
            "Schleife abgebrochen. Das ist meistens ein Hinweis auf "
            "einen Prompt- oder Tool-Schema-Bug."
        )
        return 1

    elapsed = time.monotonic() - start
    _section("Zusammenfassung")
    _good(
        f"Iterationen: {iteration}  •  "
        f"Tokens: {total_input} in / {total_output} out  •  "
        f"Dauer: {elapsed:.1f} s"
    )
    return 0


async def _execute_one_tool(
    session: ClientSession,
    block: Any,
    iteration: int,
) -> dict[str, Any]:
    """Dispatch one ``tool_use`` block through the MCP session.

    Wraps every failure mode (rate-limit, validation error, ownership
    404) into an ``is_error: true`` tool result. That lets Claude see
    the German message we ship from the dispatcher and decide whether
    to retry, ask the user, or give up — instead of the whole stream
    crashing on the first hiccup.
    """
    name = block.name
    args = block.input or {}
    short_id = block.id[-8:]
    _section(f"Tool-Call #{iteration}.{short_id} — {name}")
    _gray(f"args: {json.dumps(args, ensure_ascii=False)}")

    try:
        result = await session.call_tool(name, args)
    except Exception as exc:  # noqa: BLE001
        msg = f"Fehler beim Tool-Call: {exc}"
        _err(msg)
        return {
            "type": "tool_result",
            "tool_use_id": block.id,
            "content": msg,
            "is_error": True,
        }

    # MCP tool results carry a list of content blocks; for our tools
    # everything is TextContent, so concatenating is safe.
    text = "".join(
        c.text for c in result.content if getattr(c, "type", None) == "text"
    )
    _gray(f"result: {_truncate(text)}")
    return {
        "type": "tool_result",
        "tool_use_id": block.id,
        "content": text,
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "End-to-end demo des BauLV-MCP-Servers. Treibt Claude "
            "(Anthropic-SDK) gegen unseren /mcp/sse-Endpoint."
        ),
    )
    parser.add_argument(
        "--base-url",
        default=os.environ.get("BAULV_BASE_URL", "https://baulv.at"),
        help="BauLV-Basis-URL (default: $BAULV_BASE_URL oder https://baulv.at)",
    )
    parser.add_argument(
        "--write",
        action="store_true",
        help=(
            "Schalte den Schreib-Demo-Flow frei. "
            "Legt ein Test-Projekt + ein LV aus Maler-Vorlage an. "
            "Achtung: Hinterlässt Daten in deinem Account."
        ),
    )
    parser.add_argument(
        "--prompt",
        help=(
            "Eigener deutscher Prompt; "
            "überschreibt --write/--read-Default."
        ),
    )
    args = parser.parse_args()

    pat = os.environ.get("BAULV_PAT")
    if not pat:
        _err(
            "BAULV_PAT ist nicht gesetzt. Token unter "
            "https://baulv.at/app/api-keys erstellen und als "
            "Umgebungsvariable exportieren."
        )
        return 2

    if not os.environ.get("ANTHROPIC_API_KEY"):
        _err(
            "ANTHROPIC_API_KEY ist nicht gesetzt. Eigenen Anthropic-Key "
            "unter https://console.anthropic.com/settings/keys holen."
        )
        return 2

    if args.prompt:
        prompt = args.prompt
    elif args.write:
        prompt = _write_prompt()
    else:
        prompt = READ_PROMPT

    try:
        return asyncio.run(
            run_demo(base_url=args.base_url, pat=pat, prompt=prompt)
        )
    except KeyboardInterrupt:
        _warn("\nAbgebrochen.")
        return 130


if __name__ == "__main__":
    sys.exit(main())
