import { useState } from "react";
import { MailWarning } from "lucide-react";
import { resendVerificationEmail } from "../api/auth";
import { useAuth } from "../hooks/useAuth";
import { useToast } from "./Toast";

/**
 * Audit 2026-11 — Hinweis für Konten mit unbestätigter E-Mail-Adresse.
 * Das Backend sperrt bis dahin die KI-Funktionen; der Banner erklärt
 * das und bietet den erneuten Versand an.
 */
export function EmailVerifyBanner() {
  const { user } = useAuth();
  const toast = useToast();
  const [sending, setSending] = useState(false);

  if (!user || user.email_verified !== false) return null;

  const resend = async () => {
    setSending(true);
    try {
      await resendVerificationEmail();
      toast.success(`Bestätigungs-Mail an ${user.email} verschickt.`);
    } catch (err: any) {
      toast.error(
        err?.response?.status === 429
          ? "Bitte warten Sie ein paar Minuten, bevor Sie eine weitere Mail anfordern."
          : "Die Mail konnte nicht verschickt werden. Bitte später erneut versuchen.",
      );
    } finally {
      setSending(false);
    }
  };

  return (
    <div className="border-b border-yellow-300 bg-yellow-50 text-yellow-900">
      <div className="flex flex-wrap items-center gap-3 px-6 py-3 text-sm">
        <MailWarning className="h-4 w-4 shrink-0" aria-hidden="true" />
        <span className="flex-1">
          Bitte bestätigen Sie Ihre E-Mail-Adresse — die KI-Funktionen
          sind bis dahin gesperrt.
        </span>
        <button
          type="button"
          onClick={resend}
          disabled={sending}
          className="rounded-md border border-yellow-400 bg-white px-3 py-1 text-xs font-medium hover:bg-yellow-100 disabled:opacity-50"
        >
          {sending ? "Wird gesendet..." : "Bestätigungs-Mail erneut senden"}
        </button>
      </div>
    </div>
  );
}
