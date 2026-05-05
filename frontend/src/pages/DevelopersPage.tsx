/**
 * /developers — public technical landing page for the BauLV MCP API
 * (v23.7).
 *
 * Sister page to ``/api-pricing``. Where api-pricing is marketing-
 * forward (hero + tier cards + use-cases + FAQ), this page is for
 * developers who already know they want to integrate and need
 * concrete technical information: cURL example, tool inventory,
 * connector setup for the major MCP clients (Claude Desktop, n8n,
 * ChatGPT custom connectors, plain HTTP), and pointers to the
 * discovery endpoints.
 *
 * Public route, no auth required. Mounted in App.tsx.
 *
 * Style notes
 * -----------
 *
 * Code blocks use a ``DarkCodeBlock`` helper that renders a
 * Monokai-ish dark surface (slate-900 / amber accents). We don't
 * pull in a syntax highlighter library — the snippets are short
 * and the visual contrast alone is enough for readability. If we
 * later add Prism or Shiki, this is the spot to upgrade.
 */
import { useState } from "react";
import { Link } from "react-router-dom";
import {
  Building2,
  Code2,
  Terminal,
  Bot,
  Workflow,
  ArrowRight,
  ExternalLink,
  Copy,
  Check,
  FileJson,
  KeyRound,
  Shield,
} from "lucide-react";
import { Footer } from "../components/layout/Footer";

interface ToolRow {
  name: string;
  category: "read" | "write";
  /** Short German description; matches the values surfaced by the
   * MCP server's ``tools/list`` response. */
  description: string;
}

const TOOLS: ToolRow[] = [
  // Read tools — 8
  {
    name: "list_projects",
    category: "read",
    description: "Alle Projekte des authentifizierten Users.",
  },
  {
    name: "get_project",
    category: "read",
    description: "Stammdaten eines einzelnen Projekts.",
  },
  {
    name: "get_project_structure",
    category: "read",
    description:
      "Vollständiger Gebäudebaum (Buildings → Floors → Units → Rooms inkl. Wand- / Deckenflächen).",
  },
  {
    name: "list_rooms",
    category: "read",
    description: "Flache Raumliste eines Projekts mit Geometrie.",
  },
  {
    name: "list_lvs",
    category: "read",
    description: "Alle Leistungsverzeichnisse eines Projekts.",
  },
  {
    name: "get_lv",
    category: "read",
    description: "Komplettes LV inklusive Gruppen und Positionen.",
  },
  {
    name: "get_position_with_proof",
    category: "read",
    description:
      "Einzelne Position mit allen Berechnungsnachweisen (Raum, Formel, Berechnungs-Faktor, Abzüge).",
  },
  {
    name: "list_templates",
    category: "read",
    description:
      "Verfügbare LV-Vorlagen (System + eigene), optional gefiltert nach Kategorie / Gewerk.",
  },
  // Write tools — 7
  {
    name: "create_project",
    category: "write",
    description: "Neues Projekt anlegen (respektiert Plan-Limits).",
  },
  {
    name: "update_project",
    category: "write",
    description: "Projekt-Metadaten patchen (Adresse, Status, …).",
  },
  {
    name: "create_lv",
    category: "write",
    description: "Leeres LV innerhalb eines Projekts anlegen.",
  },
  {
    name: "create_lv_from_template",
    category: "write",
    description: "Standard-Flow: Vorlage in ein Projekt kopieren.",
  },
  {
    name: "update_lv",
    category: "write",
    description: "LV-Metadaten patchen (Name, Status, Vorbemerkungen).",
  },
  {
    name: "update_position",
    category: "write",
    description:
      "Einzelne Position bearbeiten (Kurztext, Langtext, Menge, Einheitspreis, Sperr-Flag).",
  },
  {
    name: "create_template_from_lv",
    category: "write",
    description:
      "Bestehendes LV als wiederverwendbare Vorlage speichern.",
  },
];

const CURL_EXAMPLE = `# 1. SSE-Verbindung öffnen
curl -N -H "Authorization: Bearer pat_xxxxxxxxxxxxxxxxxxxxxxxxxxxx" \\
  -H "Accept: text/event-stream" \\
  https://baulv.at/mcp/sse

# 2. Tool-Liste über messages-Channel
curl -X POST https://baulv.at/mcp/messages/ \\
  -H "Authorization: Bearer pat_xxxxxxxxxxxxxxxxxxxxxxxxxxxx" \\
  -H "Content-Type: application/json" \\
  -d '{
    "jsonrpc": "2.0",
    "id": 1,
    "method": "tools/list"
  }'

# 3. Tool aufrufen — z.B. eigene Projekte listen
curl -X POST https://baulv.at/mcp/messages/ \\
  -H "Authorization: Bearer pat_xxxxxxxxxxxxxxxxxxxxxxxxxxxx" \\
  -H "Content-Type: application/json" \\
  -d '{
    "jsonrpc": "2.0",
    "id": 2,
    "method": "tools/call",
    "params": { "name": "list_projects", "arguments": {} }
  }'`;

const CLAUDE_DESKTOP_CONFIG = `{
  "mcpServers": {
    "baulv": {
      "url": "https://baulv.at/mcp/sse",
      "transport": "sse",
      "headers": {
        "Authorization": "Bearer pat_xxxxxxxxxxxxxxxxxxxxxxxxxxxx"
      }
    }
  }
}`;

const N8N_HTTP_NODE = `Method:        POST
URL:           https://baulv.at/mcp/messages/
Authentication: Header Auth
   Header Name:  Authorization
   Header Value: Bearer pat_xxxxxxxxxxxxxxxxxxxxxxxxxxxx
Send Body:     JSON
Body:
{
  "jsonrpc": "2.0",
  "id": "{{ $json.requestId }}",
  "method": "tools/call",
  "params": {
    "name": "create_lv_from_template",
    "arguments": {
      "project_id": "{{ $json.projectId }}",
      "template_id": "{{ $json.templateId }}"
    }
  }
}`;

export function DevelopersPage() {
  return (
    <div className="min-h-screen bg-white">
      <PublicNavbar />

      {/* Hero */}
      <section className="border-b bg-gradient-to-br from-slate-900 to-slate-800 text-white">
        <div className="mx-auto max-w-5xl px-6 py-16 md:py-20">
          <div className="mb-3 inline-flex items-center gap-2 rounded-full border border-white/20 bg-white/5 px-4 py-1.5 text-sm">
            <Code2 className="h-4 w-4 text-amber-400" />
            Entwickler-Doku
          </div>
          <h1 className="text-4xl font-extrabold tracking-tight md:text-5xl">
            Build mit der{" "}
            <span className="bg-gradient-to-r from-amber-400 to-orange-300 bg-clip-text text-transparent">
              BauLV API
            </span>
          </h1>
          <p className="mt-5 max-w-2xl text-lg text-slate-300">
            MCP-Standard, 15 Tools, Personal Access Tokens. EU-gehostet,
            DSGVO-konform, jederzeit kündbar.
          </p>
          <div className="mt-8 flex flex-wrap gap-3">
            <Link
              to="/app/api-keys"
              className="flex items-center gap-2 rounded-lg bg-amber-400 px-5 py-2.5 text-sm font-medium text-slate-900 hover:bg-amber-300"
            >
              <KeyRound className="h-4 w-4" />
              API-Key erstellen
            </Link>
            <Link
              to="/api-pricing"
              className="flex items-center gap-2 rounded-lg border border-white/20 bg-white/5 px-5 py-2.5 text-sm font-medium hover:bg-white/10"
            >
              Tarife ansehen
              <ArrowRight className="h-4 w-4" />
            </Link>
          </div>
        </div>
      </section>

      {/* Quick-Start cURL */}
      <section className="py-16">
        <div className="mx-auto max-w-5xl px-6">
          <div className="mb-8">
            <h2 className="text-2xl font-bold">Quick-Start mit cURL</h2>
            <p className="mt-2 text-sm text-muted-foreground">
              Drei Calls — verbinden, Tools auflisten, ersten Tool aufrufen.
            </p>
          </div>
          <DarkCodeBlock language="bash" code={CURL_EXAMPLE} />
          <p className="mt-4 text-xs text-muted-foreground">
            Tausche{" "}
            <code className="rounded bg-muted px-1 py-0.5 font-mono">
              pat_xxxx
            </code>{" "}
            durch deinen echten Token aus{" "}
            <Link
              to="/app/api-keys"
              className="font-medium text-primary hover:underline"
            >
              /app/api-keys
            </Link>
            . Der Plaintext wird nur einmalig nach der Erzeugung gezeigt —
            danach speichert die DB nur noch den SHA-256-Hash.
          </p>
        </div>
      </section>

      {/* Tools table */}
      <section className="border-y bg-muted/20 py-16">
        <div className="mx-auto max-w-5xl px-6">
          <div className="mb-8">
            <h2 className="text-2xl font-bold">15 Tools im Überblick</h2>
            <p className="mt-2 text-sm text-muted-foreground">
              Vollständiges JSON-Schema je Tool über{" "}
              <code className="rounded bg-muted px-1 py-0.5 font-mono">
                tools/list
              </code>
              . Untenstehende Liste ist der menschenlesbare Index.
            </p>
          </div>
          <ToolsTable tools={TOOLS} />
        </div>
      </section>

      {/* Integration guides */}
      <section className="py-16">
        <div className="mx-auto max-w-5xl px-6">
          <div className="mb-10">
            <h2 className="text-2xl font-bold">Integration-Guides</h2>
            <p className="mt-2 text-sm text-muted-foreground">
              Konkrete Konfiguration für die häufigsten MCP-Clients.
            </p>
          </div>
          <div className="space-y-10">
            <IntegrationGuide
              icon={Bot}
              title="Claude Desktop"
              description={[
                "Öffne ``%APPDATA%\\Claude\\claude_desktop_config.json`` (Windows) bzw. ``~/Library/Application Support/Claude/claude_desktop_config.json`` (macOS).",
                "Füge BauLV als MCP-Server hinzu — Claude Desktop verbindet sich beim nächsten Start automatisch:",
              ]}
              codeLanguage="json"
              code={CLAUDE_DESKTOP_CONFIG}
              tail={
                <>
                  Nach dem Neustart erscheinen alle 15 Tools im Tools-Menü
                  von Claude Desktop und können in jeder Konversation
                  aufgerufen werden.
                </>
              }
            />

            <IntegrationGuide
              icon={Workflow}
              title="n8n / Make / Zapier"
              description={[
                "Die MCP-Endpoints sprechen JSON-RPC 2.0 über plain HTTP — kein spezieller MCP-Connector nötig. Im n8n HTTP-Request-Node:",
              ]}
              codeLanguage="text"
              code={N8N_HTTP_NODE}
              tail={
                <>
                  Für Make / Zapier ist die Konfiguration identisch — POST
                  mit Bearer-Token und JSON-RPC-Payload. Alle 15 Tools sind
                  über{" "}
                  <code className="rounded bg-muted px-1 py-0.5 font-mono">
                    tools/call
                  </code>{" "}
                  ansprechbar.
                </>
              }
            />

            <IntegrationGuide
              icon={Terminal}
              title="ChatGPT Custom Connector"
              description={[
                "ChatGPT-Custom-Connectors sprechen MCP nativ. Im Custom-Connector-Editor:",
                "1. Connector-Typ: SSE-MCP-Server",
                "2. Server-URL: ``https://baulv.at/mcp/sse``",
                "3. Auth-Header: ``Authorization: Bearer pat_…``",
                "4. Discovery-Probe akzeptieren — ChatGPT zieht automatisch alle 15 Tool-Schemas.",
              ]}
              tail={
                <>
                  Tool-Beschreibungen kommen auf Deutsch — ChatGPT versteht
                  das problemlos und ruft die Tools im Konversationsfluss
                  auf, sobald der User eine relevante Frage stellt
                  ("zeig mir mein Projekt 'Wohnhaus 42'").
                </>
              }
            />

            <IntegrationGuide
              icon={Code2}
              title="Eigene Skripte (Python / Node)"
              description={[
                "Python: das offizielle ``mcp``-SDK von Anthropic kann direkt SSE-Server ansprechen. Beispiel mit ``mcp.client.sse.sse_client``:",
              ]}
              codeLanguage="python"
              code={`from mcp.client.sse import sse_client

async with sse_client(
    "https://baulv.at/mcp/sse",
    headers={"Authorization": "Bearer pat_xxxx"},
) as (read, write):
    # Initialize, list tools, call tools — siehe MCP-SDK-Doku.
    ...`}
              tail={
                <>
                  Node / TypeScript: ``@modelcontextprotocol/sdk`` mit dem
                  SSE-Transport gleicher Form. Beide SDKs handhaben
                  Reconnect, Re-Subscribe und Backoff für dich.
                </>
              }
            />
          </div>
        </div>
      </section>

      {/* Discovery & Standards */}
      <section className="border-t bg-muted/20 py-16">
        <div className="mx-auto max-w-5xl px-6">
          <div className="mb-8">
            <h2 className="text-2xl font-bold">Discovery & Standards</h2>
            <p className="mt-2 text-sm text-muted-foreground">
              Maschinenlesbare Endpoints für automatische Discovery.
            </p>
          </div>
          <div className="grid gap-3 md:grid-cols-2">
            <DiscoveryLink
              icon={FileJson}
              label="MCP-Discovery"
              href="/.well-known/mcp.json"
              hint="Vollständige Tool-Liste + Auth-Setup als JSON."
            />
            <DiscoveryLink
              icon={FileJson}
              label="LLM-Manifest"
              href="/llms.txt"
              hint="Markdown-Übersicht für Crawler & RAG-Pipelines."
            />
            <DiscoveryLink
              icon={ExternalLink}
              label="Model Context Protocol"
              href="https://modelcontextprotocol.io"
              hint="Spezifikation des MCP-Standards."
              external
            />
            <DiscoveryLink
              icon={Shield}
              label="DSGVO & Datenschutz"
              href="/datenschutz"
              hint="Datenverarbeitungs-Hinweis für API-Konsumenten."
            />
          </div>
        </div>
      </section>

      <Footer />
    </div>
  );
}

// ---------------------------------------------------------------------------
// Sub-components
// ---------------------------------------------------------------------------

function PublicNavbar() {
  return (
    <nav className="sticky top-0 z-40 border-b bg-white/95 backdrop-blur">
      <div className="mx-auto flex h-16 max-w-6xl items-center justify-between px-6">
        <Link
          to="/"
          className="flex items-center gap-2 font-bold text-primary"
        >
          <Building2 className="h-7 w-7" />
          <span className="text-xl">BauLV</span>
        </Link>
        <div className="hidden items-center gap-6 md:flex">
          <Link
            to="/"
            className="text-sm text-muted-foreground hover:text-foreground"
          >
            Plattform
          </Link>
          <Link
            to="/api-pricing"
            className="text-sm text-muted-foreground hover:text-foreground"
          >
            API-Tarife
          </Link>
          <Link
            to="/developers"
            className="text-sm font-medium text-foreground"
          >
            Entwickler
          </Link>
          <Link
            to="/login"
            className="text-sm font-medium text-primary hover:text-primary/80"
          >
            Anmelden
          </Link>
        </div>
      </div>
    </nav>
  );
}

function DarkCodeBlock({
  code,
  language,
}: {
  code: string;
  language: string;
}) {
  const [copied, setCopied] = useState(false);

  const handleCopy = async () => {
    try {
      await navigator.clipboard.writeText(code);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      // Clipboard API can fail on insecure contexts / older browsers.
      // Silent fail is acceptable — the code is still selectable.
    }
  };

  return (
    <div className="relative overflow-hidden rounded-lg border border-slate-700 bg-slate-900 text-slate-100 shadow-sm">
      <div className="flex items-center justify-between border-b border-slate-700 bg-slate-800/50 px-4 py-2 text-xs">
        <span className="font-mono uppercase tracking-wide text-slate-400">
          {language}
        </span>
        <button
          type="button"
          onClick={handleCopy}
          aria-label="Code kopieren"
          className="flex items-center gap-1.5 rounded px-2 py-1 text-slate-300 transition-colors hover:bg-slate-700 hover:text-white"
        >
          {copied ? (
            <>
              <Check className="h-3 w-3 text-emerald-400" />
              <span className="text-emerald-400">Kopiert</span>
            </>
          ) : (
            <>
              <Copy className="h-3 w-3" />
              <span>Kopieren</span>
            </>
          )}
        </button>
      </div>
      <pre className="overflow-x-auto px-4 py-3 text-xs leading-relaxed">
        <code className="font-mono">{code}</code>
      </pre>
    </div>
  );
}

function ToolsTable({ tools }: { tools: ToolRow[] }) {
  return (
    <div className="overflow-x-auto rounded-lg border bg-card">
      <table className="w-full text-sm">
        <thead className="bg-muted/50 text-xs uppercase tracking-wide">
          <tr>
            <th className="px-4 py-3 text-left font-medium">Tool</th>
            <th className="px-4 py-3 text-left font-medium">Kategorie</th>
            <th className="px-4 py-3 text-left font-medium">Beschreibung</th>
          </tr>
        </thead>
        <tbody className="divide-y">
          {tools.map((t) => (
            <tr key={t.name} className="hover:bg-muted/30">
              <td className="whitespace-nowrap px-4 py-2.5 font-mono text-xs">
                {t.name}
              </td>
              <td className="whitespace-nowrap px-4 py-2.5">
                <span
                  className={
                    t.category === "read"
                      ? "rounded-full bg-blue-100 px-2 py-0.5 text-xs font-medium text-blue-800"
                      : "rounded-full bg-amber-100 px-2 py-0.5 text-xs font-medium text-amber-800"
                  }
                >
                  {t.category === "read" ? "Lesen" : "Schreiben"}
                </span>
              </td>
              <td className="px-4 py-2.5 text-muted-foreground">
                {t.description}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function IntegrationGuide({
  icon: Icon,
  title,
  description,
  codeLanguage,
  code,
  tail,
}: {
  icon: typeof Bot;
  title: string;
  description: string[];
  codeLanguage?: string;
  code?: string;
  tail?: React.ReactNode;
}) {
  return (
    <div className="rounded-xl border bg-card p-6">
      <div className="mb-4 flex items-center gap-3">
        <div className="rounded-lg bg-primary/10 p-2.5">
          <Icon className="h-5 w-5 text-primary" />
        </div>
        <h3 className="text-lg font-semibold">{title}</h3>
      </div>
      <div className="space-y-3 text-sm text-muted-foreground">
        {description.map((line) => (
          <p key={line}>{line}</p>
        ))}
      </div>
      {code && codeLanguage && (
        <div className="mt-4">
          <DarkCodeBlock language={codeLanguage} code={code} />
        </div>
      )}
      {tail && (
        <p className="mt-4 text-sm text-muted-foreground">{tail}</p>
      )}
    </div>
  );
}

function DiscoveryLink({
  icon: Icon,
  label,
  href,
  hint,
  external,
}: {
  icon: typeof FileJson;
  label: string;
  href: string;
  hint: string;
  external?: boolean;
}) {
  const cls =
    "flex items-start gap-3 rounded-lg border bg-card p-4 transition-shadow hover:shadow-sm";
  const inner = (
    <>
      <div className="rounded-md bg-primary/10 p-2">
        <Icon className="h-4 w-4 text-primary" />
      </div>
      <div className="flex-1">
        <div className="flex items-center gap-1.5 font-medium">
          {label}
          {external && <ExternalLink className="h-3 w-3 text-muted-foreground" />}
        </div>
        <p className="mt-0.5 text-xs text-muted-foreground">{hint}</p>
        <code className="mt-1 block break-all font-mono text-xs text-primary">
          {href}
        </code>
      </div>
    </>
  );

  if (external) {
    return (
      <a href={href} target="_blank" rel="noopener noreferrer" className={cls}>
        {inner}
      </a>
    );
  }
  // Static-asset links (.well-known/mcp.json, llms.txt) are served by
  // the SPA's static file handler — plain ``<a>`` so the browser
  // performs a full navigation rather than asking React Router to
  // match a non-existent client-side route. Legal pages
  // (/datenschutz etc.) ARE client-side routes, but a hard
  // navigation to them is still correct (no loss of state, public
  // pages have no scroll-context to preserve).
  return (
    <a href={href} className={cls}>
      {inner}
    </a>
  );
}
