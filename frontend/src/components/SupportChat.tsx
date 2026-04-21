/**
 * Floating support-chat widget for the landing page.
 *
 * Design goals:
 *   - Stays out of the way until opened (bottom-right speech-bubble).
 *   - Persists the conversation in localStorage so reloads don't lose it.
 *   - Always has something to show when the API fails — never leaves
 *     the user staring at an indicator that's silently broken.
 *
 * Not mounted anywhere that requires auth; the backend endpoint is
 * public and rate-limited by IP.
 */
import { useEffect, useRef, useState, type FormEvent } from "react";
import { MessageCircle, X, Send, RotateCcw, Bot, User as UserIcon } from "lucide-react";
import { sendSupportMessage, type SupportMessage } from "../api/supportChat";

const STORAGE_KEY = "baulv_support_chat_v1";
const GREETING: SupportMessage = {
  role: "assistant",
  content:
    "Hallo! Ich bin der BauLV-Assistent. Wie kann ich Ihnen bei Fragen zum Produkt helfen?",
};
const FALLBACK_REPLY =
  "Der Chat ist momentan nicht verfügbar. Bitte versuchen Sie es später erneut.";

function loadHistory(): SupportMessage[] {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return [GREETING];
    const parsed = JSON.parse(raw);
    if (!Array.isArray(parsed) || parsed.length === 0) return [GREETING];
    // Defensive — only keep entries with the shape we expect.
    const cleaned = parsed.filter(
      (m): m is SupportMessage =>
        m &&
        typeof m === "object" &&
        (m.role === "user" || m.role === "assistant") &&
        typeof m.content === "string"
    );
    return cleaned.length > 0 ? cleaned : [GREETING];
  } catch {
    return [GREETING];
  }
}

function saveHistory(messages: SupportMessage[]) {
  try {
    // Cap stored history so the localStorage entry can't grow
    // unbounded across sessions. 40 matches the backend limit.
    const trimmed = messages.slice(-40);
    localStorage.setItem(STORAGE_KEY, JSON.stringify(trimmed));
  } catch {
    /* quota / private mode — drop silently */
  }
}

export function SupportChat() {
  const [open, setOpen] = useState(false);
  const [messages, setMessages] = useState<SupportMessage[]>(() => loadHistory());
  const [input, setInput] = useState("");
  const [sending, setSending] = useState(false);
  const scrollRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);

  // Persist on every change. Tiny payload — no need to debounce.
  useEffect(() => {
    saveHistory(messages);
  }, [messages]);

  // Auto-scroll to the latest message when the list grows or we
  // start/stop loading (so the typing indicator is visible).
  useEffect(() => {
    if (scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
    }
  }, [messages, sending, open]);

  // Focus the input when opening, so users can start typing right away.
  useEffect(() => {
    if (open) inputRef.current?.focus();
  }, [open]);

  const resetConversation = () => {
    setMessages([GREETING]);
    setInput("");
  };

  const onSubmit = async (e?: FormEvent) => {
    e?.preventDefault();
    const text = input.trim();
    if (!text || sending) return;

    const next: SupportMessage[] = [...messages, { role: "user", content: text }];
    setMessages(next);
    setInput("");
    setSending(true);

    try {
      // Drop the opening greeting — it is a client-side pleasantry,
      // not something the backend needs to see. Also cap the window
      // to the last 30 turns to control prompt size.
      const toSend = next.filter((m, i) => !(i === 0 && m === GREETING)).slice(-30);
      const reply = await sendSupportMessage(toSend);
      setMessages((m) => [...m, { role: "assistant", content: reply }]);
    } catch (err: unknown) {
      // Extract the server's German error message when available;
      // fall back to the generic "chat unavailable" line otherwise.
      let friendly = FALLBACK_REPLY;
      if (typeof err === "object" && err && "response" in err) {
        const resp = (err as { response?: { data?: { detail?: string } } }).response;
        if (resp?.data?.detail && typeof resp.data.detail === "string") {
          friendly = resp.data.detail;
        }
      }
      setMessages((m) => [...m, { role: "assistant", content: friendly }]);
    } finally {
      setSending(false);
    }
  };

  const onKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    // Enter = send, Shift+Enter = newline — matches every modern chat.
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      onSubmit();
    }
  };

  return (
    <>
      {/* Launcher button — hidden while the panel is open so it
          doesn't clutter the viewport. Uses z-[60] so it always sits
          above the PWA install prompt (z-50) and any other overlay. */}
      {!open && (
        <button
          type="button"
          aria-label="Support-Chat öffnen"
          onClick={() => setOpen(true)}
          className="fixed bottom-6 right-6 z-[60] flex h-14 w-14 items-center justify-center rounded-full bg-primary text-primary-foreground shadow-xl transition-transform hover:scale-110 focus:outline-none focus:ring-4 focus:ring-primary/30 animate-pulse-slow"
        >
          <MessageCircle className="h-6 w-6" />
        </button>
      )}

      {/* Panel */}
      {open && (
        <div
          className="fixed bottom-6 right-6 z-[60] flex w-[calc(100vw-3rem)] max-w-[400px] flex-col overflow-hidden rounded-2xl border border-border bg-card shadow-2xl"
          style={{ height: "min(600px, calc(100vh - 3rem))" }}
          role="dialog"
          aria-label="BauLV Support-Chat"
        >
          {/* Header */}
          <div className="flex items-center justify-between border-b bg-primary px-4 py-3 text-primary-foreground">
            <div className="flex items-center gap-2">
              <Bot className="h-5 w-5" />
              <div className="leading-tight">
                <div className="text-sm font-semibold">BauLV-Assistent</div>
                <div className="text-[11px] opacity-80">Wir antworten meist sofort</div>
              </div>
            </div>
            <div className="flex items-center gap-1">
              <button
                type="button"
                aria-label="Neue Unterhaltung starten"
                title="Neue Unterhaltung"
                onClick={resetConversation}
                className="rounded p-1.5 hover:bg-white/10"
              >
                <RotateCcw className="h-4 w-4" />
              </button>
              <button
                type="button"
                aria-label="Chat schließen"
                onClick={() => setOpen(false)}
                className="rounded p-1.5 hover:bg-white/10"
              >
                <X className="h-4 w-4" />
              </button>
            </div>
          </div>

          {/* Messages */}
          <div
            ref={scrollRef}
            className="flex-1 space-y-3 overflow-y-auto bg-secondary/30 px-3 py-3"
          >
            {messages.map((m, i) => (
              <MessageBubble key={i} message={m} />
            ))}
            {sending && <TypingBubble />}
          </div>

          {/* Input */}
          <form
            onSubmit={onSubmit}
            className="border-t bg-card p-2"
          >
            <div className="flex items-end gap-2">
              <textarea
                ref={inputRef}
                value={input}
                onChange={(e) => setInput(e.target.value)}
                onKeyDown={onKeyDown}
                placeholder="Ihre Frage zu BauLV..."
                rows={1}
                maxLength={2000}
                className="max-h-28 flex-1 resize-none rounded-lg border bg-background px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-primary/40"
                disabled={sending}
              />
              <button
                type="submit"
                disabled={sending || !input.trim()}
                aria-label="Nachricht senden"
                className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-primary text-primary-foreground transition-opacity hover:bg-primary/90 disabled:opacity-40"
              >
                <Send className="h-4 w-4" />
              </button>
            </div>
            <p className="mt-1 px-1 text-[10px] text-muted-foreground">
              Dieser Chat basiert auf Claude AI von Anthropic.
            </p>
          </form>
        </div>
      )}
    </>
  );
}

function MessageBubble({ message }: { message: SupportMessage }) {
  const isUser = message.role === "user";
  return (
    <div className={`flex items-start gap-2 ${isUser ? "flex-row-reverse" : ""}`}>
      <div
        className={`flex h-7 w-7 shrink-0 items-center justify-center rounded-full ${
          isUser ? "bg-primary text-primary-foreground" : "bg-white text-primary shadow-sm"
        }`}
      >
        {isUser ? <UserIcon className="h-3.5 w-3.5" /> : <Bot className="h-3.5 w-3.5" />}
      </div>
      <div
        className={`max-w-[80%] whitespace-pre-wrap rounded-2xl px-3 py-2 text-sm leading-relaxed ${
          isUser
            ? "rounded-tr-sm bg-primary text-primary-foreground"
            : "rounded-tl-sm bg-white text-foreground shadow-sm"
        }`}
      >
        {message.content}
      </div>
    </div>
  );
}

function TypingBubble() {
  return (
    <div className="flex items-start gap-2">
      <div className="flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-white text-primary shadow-sm">
        <Bot className="h-3.5 w-3.5" />
      </div>
      <div className="rounded-2xl rounded-tl-sm bg-white px-3 py-2 shadow-sm">
        <div className="flex gap-1">
          <span className="h-2 w-2 animate-bounce rounded-full bg-muted-foreground [animation-delay:0ms]" />
          <span className="h-2 w-2 animate-bounce rounded-full bg-muted-foreground [animation-delay:150ms]" />
          <span className="h-2 w-2 animate-bounce rounded-full bg-muted-foreground [animation-delay:300ms]" />
        </div>
      </div>
    </div>
  );
}
