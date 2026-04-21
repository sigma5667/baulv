/**
 * In-app AI advisor — ChatGPT-style chat with sidebar.
 *
 * Pro/Enterprise only. Basis users get an inline upgrade prompt.
 * Conversations are persisted server-side (ChatSession + ChatMessage
 * tables) so history survives reload and device switches.
 */
import { useEffect, useMemo, useRef, useState, type FormEvent } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import ReactMarkdown from "react-markdown";
import {
  Send,
  Plus,
  Bot,
  User as UserIcon,
  Trash2,
  Pencil,
  Check,
  X,
  MessagesSquare,
  Lock,
  ArrowRight,
} from "lucide-react";
import { Link } from "react-router-dom";
import {
  createChatSession,
  deleteChatSession,
  fetchMessages,
  listChatSessions,
  renameChatSession,
  sendMessage,
  type ChatSession,
  type ChatMessage,
} from "../api/chat";
import { useAuth } from "../hooks/useAuth";

const LAST_SESSION_KEY = "baulv_chat_last_session";

export function ChatPage() {
  const { hasFeature } = useAuth();
  const canUseChat = hasFeature("ai_chat");

  if (!canUseChat) {
    return <UpgradeGate />;
  }

  return <ChatWorkspace />;
}

// ---------------------------------------------------------------------------
// Upgrade gate for Basis users
// ---------------------------------------------------------------------------

function UpgradeGate() {
  return (
    <div className="flex h-full items-center justify-center p-8">
      <div className="max-w-md rounded-2xl border bg-card p-8 text-center shadow-sm">
        <div className="mx-auto mb-4 flex h-12 w-12 items-center justify-center rounded-full bg-primary/10">
          <Lock className="h-5 w-5 text-primary" />
        </div>
        <h2 className="mb-2 text-xl font-semibold">KI-Fachberater</h2>
        <p className="mb-6 text-sm text-muted-foreground">
          Der KI-Chatassistent für LV, Plananalyse und österreichische
          Baupraxis ist im <strong>Pro</strong>-Plan enthalten. Upgraden Sie
          Ihr Abonnement, um diese Funktion freizuschalten.
        </p>
        <Link
          to="/app/subscription"
          className="inline-flex items-center gap-2 rounded-md bg-primary px-5 py-2.5 text-sm font-medium text-primary-foreground hover:bg-primary/90"
        >
          Pläne ansehen
          <ArrowRight className="h-4 w-4" />
        </Link>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Main workspace
// ---------------------------------------------------------------------------

function ChatWorkspace() {
  const qc = useQueryClient();

  // Selected session is persisted to localStorage so a reload brings
  // the user back to the conversation they were just in.
  const [activeId, setActiveId] = useState<string | null>(() =>
    localStorage.getItem(LAST_SESSION_KEY)
  );

  const sessionsQuery = useQuery({
    queryKey: ["chat-sessions"],
    queryFn: () => listChatSessions(),
  });

  // If the saved session no longer exists (deleted elsewhere, user
  // logged out/in as a different user), clear it. Auto-select the
  // most recent session when none is active and sessions exist.
  useEffect(() => {
    if (!sessionsQuery.data) return;
    const ids = new Set(sessionsQuery.data.map((s) => s.id));
    if (activeId && !ids.has(activeId)) {
      setActiveId(null);
      localStorage.removeItem(LAST_SESSION_KEY);
      return;
    }
    if (!activeId && sessionsQuery.data.length > 0) {
      const first = sessionsQuery.data[0].id;
      setActiveId(first);
      localStorage.setItem(LAST_SESSION_KEY, first);
    }
  }, [sessionsQuery.data, activeId]);

  const onSelect = (id: string) => {
    setActiveId(id);
    localStorage.setItem(LAST_SESSION_KEY, id);
  };

  const createMutation = useMutation({
    mutationFn: () => createChatSession(undefined, "Neue Unterhaltung"),
    onSuccess: (session) => {
      qc.invalidateQueries({ queryKey: ["chat-sessions"] });
      onSelect(session.id);
    },
  });

  // AppShell main has overflow-auto and renders a Footer below
  // children. We subtract a generous ~6rem to keep the composer
  // visible above the Footer without relying on outer scroll.
  return (
    <div className="flex h-[calc(100vh-6rem)] bg-background">
      <Sidebar
        sessions={sessionsQuery.data ?? []}
        isLoading={sessionsQuery.isLoading}
        activeId={activeId}
        onSelect={onSelect}
        onNew={() => createMutation.mutate()}
        creating={createMutation.isPending}
      />
      <main className="flex min-w-0 flex-1 flex-col">
        {activeId ? (
          <Conversation sessionId={activeId} />
        ) : (
          <EmptyState
            onNew={() => createMutation.mutate()}
            creating={createMutation.isPending}
          />
        )}
      </main>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Sidebar
// ---------------------------------------------------------------------------

function Sidebar({
  sessions,
  isLoading,
  activeId,
  onSelect,
  onNew,
  creating,
}: {
  sessions: ChatSession[];
  isLoading: boolean;
  activeId: string | null;
  onSelect: (id: string) => void;
  onNew: () => void;
  creating: boolean;
}) {
  return (
    <aside className="flex w-64 shrink-0 flex-col border-r bg-card">
      <div className="p-3">
        <button
          type="button"
          onClick={onNew}
          disabled={creating}
          className="flex w-full items-center justify-center gap-2 rounded-md bg-primary px-3 py-2 text-sm font-medium text-primary-foreground hover:bg-primary/90 disabled:opacity-60"
        >
          <Plus className="h-4 w-4" />
          Neue Unterhaltung
        </button>
      </div>
      <div className="flex-1 overflow-y-auto px-2 pb-3">
        <div className="px-2 pb-2 text-[11px] font-medium uppercase tracking-wide text-muted-foreground">
          Unterhaltungen
        </div>
        {isLoading ? (
          <div className="px-2 text-xs text-muted-foreground">Wird geladen…</div>
        ) : sessions.length === 0 ? (
          <div className="px-2 text-xs text-muted-foreground">
            Noch keine Unterhaltungen.
          </div>
        ) : (
          <ul className="space-y-1">
            {sessions.map((s) => (
              <SidebarItem
                key={s.id}
                session={s}
                active={s.id === activeId}
                onSelect={() => onSelect(s.id)}
              />
            ))}
          </ul>
        )}
      </div>
      <div className="border-t p-3 text-[10px] leading-snug text-muted-foreground">
        Dieser Chat basiert auf Claude AI von Anthropic.
      </div>
    </aside>
  );
}

function SidebarItem({
  session,
  active,
  onSelect,
}: {
  session: ChatSession;
  active: boolean;
  onSelect: () => void;
}) {
  const qc = useQueryClient();
  const [editing, setEditing] = useState(false);
  const [title, setTitle] = useState(session.title ?? "Neue Unterhaltung");

  useEffect(() => {
    setTitle(session.title ?? "Neue Unterhaltung");
  }, [session.title]);

  const renameMutation = useMutation({
    mutationFn: (t: string) => renameChatSession(session.id, t),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["chat-sessions"] });
      setEditing(false);
    },
  });

  const deleteMutation = useMutation({
    mutationFn: () => deleteChatSession(session.id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["chat-sessions"] });
    },
  });

  const onConfirmRename = () => {
    const t = title.trim();
    if (!t) return;
    renameMutation.mutate(t);
  };

  if (editing) {
    return (
      <li>
        <div className="flex items-center gap-1 rounded-md bg-accent px-2 py-1.5">
          <input
            autoFocus
            value={title}
            onChange={(e) => setTitle(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") onConfirmRename();
              if (e.key === "Escape") setEditing(false);
            }}
            className="flex-1 rounded bg-background px-1.5 py-0.5 text-sm outline-none ring-1 ring-border focus:ring-primary/50"
            maxLength={80}
          />
          <button
            type="button"
            onClick={onConfirmRename}
            className="rounded p-1 text-green-700 hover:bg-green-100"
            aria-label="Titel speichern"
          >
            <Check className="h-3.5 w-3.5" />
          </button>
          <button
            type="button"
            onClick={() => setEditing(false)}
            className="rounded p-1 text-muted-foreground hover:bg-muted"
            aria-label="Bearbeiten abbrechen"
          >
            <X className="h-3.5 w-3.5" />
          </button>
        </div>
      </li>
    );
  }

  return (
    <li>
      <div
        className={`group flex items-center gap-1 rounded-md px-2 py-1.5 text-sm ${
          active ? "bg-primary/10 text-primary" : "hover:bg-accent"
        }`}
      >
        <button
          type="button"
          onClick={onSelect}
          className="flex min-w-0 flex-1 items-center gap-2 text-left"
        >
          <MessagesSquare className="h-3.5 w-3.5 shrink-0" />
          <span className="truncate">
            {session.title?.trim() || "Neue Unterhaltung"}
          </span>
        </button>
        <button
          type="button"
          onClick={(e) => {
            e.stopPropagation();
            setEditing(true);
          }}
          className="rounded p-1 text-muted-foreground opacity-0 hover:bg-background group-hover:opacity-100"
          aria-label="Unterhaltung umbenennen"
          title="Umbenennen"
        >
          <Pencil className="h-3 w-3" />
        </button>
        <button
          type="button"
          onClick={(e) => {
            e.stopPropagation();
            if (confirm("Diese Unterhaltung wirklich löschen?")) {
              deleteMutation.mutate();
            }
          }}
          className="rounded p-1 text-muted-foreground opacity-0 hover:bg-destructive/10 hover:text-destructive group-hover:opacity-100"
          aria-label="Unterhaltung löschen"
          title="Löschen"
        >
          <Trash2 className="h-3 w-3" />
        </button>
      </div>
    </li>
  );
}

// ---------------------------------------------------------------------------
// Empty state
// ---------------------------------------------------------------------------

function EmptyState({
  onNew,
  creating,
}: {
  onNew: () => void;
  creating: boolean;
}) {
  return (
    <div className="flex flex-1 items-center justify-center p-8">
      <div className="max-w-md text-center">
        <div className="mx-auto mb-4 flex h-14 w-14 items-center justify-center rounded-2xl bg-primary/10">
          <Bot className="h-7 w-7 text-primary" />
        </div>
        <h2 className="mb-2 text-2xl font-semibold">KI-Fachberater</h2>
        <p className="mb-6 text-sm text-muted-foreground">
          Stellen Sie Fragen zu Leistungsverzeichnissen, Positionstexten,
          Mengenermittlung und österreichischer Baupraxis.
        </p>
        <button
          type="button"
          onClick={onNew}
          disabled={creating}
          className="inline-flex items-center gap-2 rounded-md bg-primary px-5 py-2.5 text-sm font-medium text-primary-foreground hover:bg-primary/90 disabled:opacity-60"
        >
          <Plus className="h-4 w-4" />
          Neue Unterhaltung starten
        </button>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Conversation (messages list + composer)
// ---------------------------------------------------------------------------

function Conversation({ sessionId }: { sessionId: string }) {
  const qc = useQueryClient();
  const [input, setInput] = useState("");
  const [pendingUser, setPendingUser] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const scrollRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);

  const messagesQuery = useQuery({
    queryKey: ["chat-messages", sessionId],
    queryFn: () => fetchMessages(sessionId),
  });

  const sendMutation = useMutation({
    mutationFn: (content: string) => sendMessage(sessionId, content),
    onSuccess: () => {
      setPendingUser(null);
      setError(null);
      qc.invalidateQueries({ queryKey: ["chat-messages", sessionId] });
    },
    onError: (err: unknown) => {
      setPendingUser(null);
      let msg =
        "Der KI-Berater ist derzeit nicht erreichbar. Bitte versuchen Sie es in ein paar Minuten erneut.";
      if (typeof err === "object" && err && "response" in err) {
        const d = (err as { response?: { data?: { detail?: string } } }).response
          ?.data?.detail;
        if (typeof d === "string") msg = d;
      }
      setError(msg);
    },
  });

  // Keep the view pinned to the latest message — but only when the
  // user is already near the bottom, so a manual scroll-up to reread
  // an earlier turn doesn't get yanked away when a new token arrives.
  const visibleMessages = useMemo<ChatMessage[]>(() => {
    const base = messagesQuery.data ?? [];
    if (!pendingUser) return base;
    return [
      ...base,
      {
        id: "__optimistic__",
        session_id: sessionId,
        role: "user",
        content: pendingUser,
        created_at: new Date().toISOString(),
      },
    ];
  }, [messagesQuery.data, pendingUser, sessionId]);

  useEffect(() => {
    const el = scrollRef.current;
    if (!el) return;
    // Always scroll on session change or when the list grows.
    el.scrollTop = el.scrollHeight;
  }, [visibleMessages.length, sendMutation.isPending, sessionId]);

  // Reset composer state when the user switches sessions.
  useEffect(() => {
    setInput("");
    setPendingUser(null);
    setError(null);
    inputRef.current?.focus();
  }, [sessionId]);

  const onSubmit = (e?: FormEvent) => {
    e?.preventDefault();
    const text = input.trim();
    if (!text || sendMutation.isPending) return;
    setInput("");
    setError(null);
    setPendingUser(text);
    sendMutation.mutate(text);
  };

  const onKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      onSubmit();
    }
  };

  return (
    <>
      <div
        ref={scrollRef}
        className="flex-1 overflow-y-auto px-4 py-6 sm:px-8"
      >
        <div className="mx-auto max-w-3xl space-y-6">
          {messagesQuery.isLoading ? (
            <div className="py-10 text-center text-sm text-muted-foreground">
              Wird geladen…
            </div>
          ) : visibleMessages.length === 0 ? (
            <div className="py-10 text-center text-sm text-muted-foreground">
              Stellen Sie Ihre erste Frage, um zu starten.
            </div>
          ) : (
            visibleMessages.map((m) => <MessageRow key={m.id} message={m} />)
          )}
          {sendMutation.isPending && <TypingRow />}
          {error && !sendMutation.isPending && (
            <div className="rounded-lg border border-destructive/30 bg-destructive/5 px-4 py-3 text-sm text-destructive">
              {error}
            </div>
          )}
        </div>
      </div>

      <form
        onSubmit={onSubmit}
        className="border-t bg-card p-3 sm:p-4"
      >
        <div className="mx-auto flex max-w-3xl items-end gap-2">
          <textarea
            ref={inputRef}
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={onKeyDown}
            rows={1}
            placeholder="Fragen Sie den KI-Fachberater..."
            maxLength={4000}
            disabled={sendMutation.isPending}
            className="max-h-40 flex-1 resize-none rounded-xl border bg-background px-4 py-3 text-sm focus:outline-none focus:ring-2 focus:ring-primary/40"
          />
          <button
            type="submit"
            disabled={sendMutation.isPending || !input.trim()}
            className="flex h-11 w-11 shrink-0 items-center justify-center rounded-xl bg-primary text-primary-foreground transition-opacity hover:bg-primary/90 disabled:opacity-40"
            aria-label="Nachricht senden"
          >
            <Send className="h-4 w-4" />
          </button>
        </div>
        <p className="mx-auto mt-2 max-w-3xl text-center text-[11px] text-muted-foreground">
          Dieser Chat basiert auf Claude AI von Anthropic. Antworten können Fehler enthalten.
        </p>
      </form>
    </>
  );
}

function MessageRow({ message }: { message: ChatMessage }) {
  const isUser = message.role === "user";
  return (
    <div className={`flex items-start gap-3 ${isUser ? "flex-row-reverse" : ""}`}>
      <div
        className={`flex h-8 w-8 shrink-0 items-center justify-center rounded-full ${
          isUser
            ? "bg-primary text-primary-foreground"
            : "bg-secondary text-primary"
        }`}
      >
        {isUser ? <UserIcon className="h-4 w-4" /> : <Bot className="h-4 w-4" />}
      </div>
      <div
        className={`max-w-[82%] rounded-2xl px-4 py-3 text-sm leading-relaxed ${
          isUser
            ? "rounded-tr-sm bg-primary text-primary-foreground"
            : "rounded-tl-sm bg-card text-foreground shadow-sm ring-1 ring-border"
        }`}
      >
        {isUser ? (
          <div className="whitespace-pre-wrap">{message.content}</div>
        ) : (
          <MarkdownBody content={message.content} />
        )}
      </div>
    </div>
  );
}

/**
 * Markdown renderer for assistant messages. Styles are scoped to this
 * block so they don't leak to user bubbles. We intentionally don't
 * enable GFM (tables, task lists) — the chat answers rarely need them
 * and skipping the dep keeps the bundle slim.
 */
function MarkdownBody({ content }: { content: string }) {
  return (
    <div className="prose-chat">
      <ReactMarkdown
        components={{
          p: ({ children }) => <p className="mb-2 last:mb-0">{children}</p>,
          ul: ({ children }) => (
            <ul className="mb-2 list-disc space-y-1 pl-5 last:mb-0">{children}</ul>
          ),
          ol: ({ children }) => (
            <ol className="mb-2 list-decimal space-y-1 pl-5 last:mb-0">{children}</ol>
          ),
          li: ({ children }) => <li className="leading-relaxed">{children}</li>,
          h1: ({ children }) => (
            <h1 className="mb-2 mt-1 text-base font-bold">{children}</h1>
          ),
          h2: ({ children }) => (
            <h2 className="mb-2 mt-1 text-sm font-bold">{children}</h2>
          ),
          h3: ({ children }) => (
            <h3 className="mb-1 mt-1 text-sm font-semibold">{children}</h3>
          ),
          strong: ({ children }) => (
            <strong className="font-semibold">{children}</strong>
          ),
          em: ({ children }) => <em className="italic">{children}</em>,
          code: ({ children, className }) => {
            const isBlock = (className ?? "").startsWith("language-");
            if (isBlock) {
              return (
                <code className="block overflow-x-auto rounded-md bg-muted px-3 py-2 font-mono text-xs">
                  {children}
                </code>
              );
            }
            return (
              <code className="rounded bg-muted px-1 py-0.5 font-mono text-xs">
                {children}
              </code>
            );
          },
          pre: ({ children }) => (
            <pre className="mb-2 overflow-x-auto rounded-md bg-muted p-2 last:mb-0">
              {children}
            </pre>
          ),
          a: ({ children, href }) => (
            <a
              href={href}
              className="text-primary underline underline-offset-2 hover:opacity-80"
              target="_blank"
              rel="noopener noreferrer"
            >
              {children}
            </a>
          ),
        }}
      >
        {content}
      </ReactMarkdown>
    </div>
  );
}

function TypingRow() {
  return (
    <div className="flex items-start gap-3">
      <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-secondary text-primary">
        <Bot className="h-4 w-4" />
      </div>
      <div className="rounded-2xl rounded-tl-sm bg-card px-4 py-3 shadow-sm ring-1 ring-border">
        <div className="flex gap-1.5">
          <span className="h-2 w-2 animate-bounce rounded-full bg-muted-foreground [animation-delay:0ms]" />
          <span className="h-2 w-2 animate-bounce rounded-full bg-muted-foreground [animation-delay:150ms]" />
          <span className="h-2 w-2 animate-bounce rounded-full bg-muted-foreground [animation-delay:300ms]" />
        </div>
      </div>
    </div>
  );
}
