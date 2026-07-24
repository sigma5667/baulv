/**
 * Warteliste-Bestätigungs-Seite (v25).
 *
 * Liest den Double-Opt-In-Token aus `?token=...` und POSTet ihn erst
 * auf Button-Klick an `/waitlist/confirm`. Bewusst KEIN Auto-Confirm
 * beim Laden: Firmen-Mail-Scanner rufen jeden Link in einer E-Mail
 * ab — würde der Seitenaufruf schon bestätigen, wäre das Double-Opt-
 * In wertlos. Der eine zusätzliche Klick ist der Preis für einen
 * belastbaren Einwilligungs-Nachweis.
 *
 * Fehlerbild: das Backend kollabiert jeden Fehlzustand (unbekannt /
 * abgelaufen / schon eingelöst) in dasselbe generische 400. Die
 * Seite zeigt die Meldung unverändert und bietet den Rückweg zum
 * Warteliste-Formular an.
 *
 * Route liegt — wie die Legal-Seiten — bewusst AUSSERHALB des
 * Beta-Gates: der Link kommt aus einer E-Mail und muss ohne
 * Beta-Code funktionieren.
 */
import { useMemo, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { Building2, MailCheck } from "lucide-react";
import { confirmWaitlist } from "../api/waitlist";

export function WartelisteBestaetigenPage() {
  const [searchParams] = useSearchParams();
  const token = useMemo(
    () => searchParams.get("token") ?? "",
    [searchParams],
  );

  const [loading, setLoading] = useState(false);
  const [doneMessage, setDoneMessage] = useState("");
  const [error, setError] = useState("");

  const handleConfirm = async () => {
    setError("");
    setLoading(true);
    try {
      const message = await confirmWaitlist(token);
      setDoneMessage(message);
    } catch (err: any) {
      setError(
        typeof err?.response?.data?.detail === "string"
          ? err.response.data.detail
          : "Der Link ist ungültig oder abgelaufen. Bitte tragen Sie sich erneut in die Warteliste ein.",
      );
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="flex min-h-screen items-center justify-center bg-gradient-to-br from-blue-50 to-slate-100 px-4 py-8">
      <div className="w-full max-w-md rounded-xl border bg-white p-6 shadow-lg sm:p-8">
        <div className="mb-6 flex flex-col items-center">
          <Link to="/" className="flex items-center gap-2 text-primary mb-2">
            <Building2 className="h-8 w-8" />
            <span className="text-2xl font-bold">BauLV</span>
          </Link>
          <p className="text-sm text-muted-foreground">
            Warteliste-Anmeldung bestätigen
          </p>
        </div>

        {!token ? (
          <>
            <div className="rounded-md bg-destructive/10 px-4 py-3 text-sm text-destructive">
              Der Link ist unvollständig oder ungültig. Bitte tragen
              Sie sich erneut in die Warteliste ein.
            </div>
            <Link
              to="/"
              className="mt-6 inline-block text-sm font-medium text-primary hover:underline"
            >
              Zur Startseite
            </Link>
          </>
        ) : doneMessage ? (
          <div className="space-y-4">
            <div className="rounded-md bg-green-50 px-4 py-3 text-sm text-green-800">
              {doneMessage}
            </div>
            <p className="text-sm text-muted-foreground">
              Sie können dieses Fenster jetzt schließen.
            </p>
            <Link
              to="/"
              className="inline-block text-sm font-medium text-primary hover:underline"
            >
              Zur Startseite
            </Link>
          </div>
        ) : (
          <div className="space-y-4">
            {error && (
              <div className="rounded-md bg-destructive/10 px-4 py-3 text-sm text-destructive">
                {error}
              </div>
            )}
            <p className="text-sm text-muted-foreground">
              Klicken Sie auf den Button, um Ihre Anmeldung zur
              BauLV-Warteliste zu bestätigen. Erst mit dieser
              Bestätigung dürfen wir Ihnen E-Mails schicken.
            </p>
            <button
              type="button"
              onClick={handleConfirm}
              disabled={loading}
              className="flex w-full items-center justify-center gap-2 rounded-md bg-primary px-4 py-2.5 text-sm font-medium text-primary-foreground hover:bg-primary/90 disabled:opacity-50"
            >
              <MailCheck className="h-4 w-4" />
              {loading ? "Wird bestätigt..." : "Anmeldung bestätigen"}
            </button>
          </div>
        )}
      </div>
    </div>
  );
}
