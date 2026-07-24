/**
 * Warteliste-Abmelde-Seite (v25).
 *
 * Liest den Abmelde-Token aus `?token=...` (kommt aus dem Footer
 * jeder Warteliste-Mail; funktioniert auch mit dem Bestätigungs-
 * Token) und POSTet ihn auf Button-Klick an `/waitlist/unsubscribe`
 * — ohne Login, der Token ist die Legitimation. Kein Auto-Fire beim
 * Laden, damit ein Link-Prefetch des Mail-Programms niemanden
 * ungewollt abmeldet.
 *
 * Route liegt — wie die Legal-Seiten — bewusst AUSSERHALB des
 * Beta-Gates: Abmelden muss immer und ohne Beta-Code möglich sein.
 */
import { useMemo, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { Building2, MailX } from "lucide-react";
import { unsubscribeWaitlist } from "../api/waitlist";

export function WartelisteAbmeldenPage() {
  const [searchParams] = useSearchParams();
  const token = useMemo(
    () => searchParams.get("token") ?? "",
    [searchParams],
  );

  const [loading, setLoading] = useState(false);
  const [doneMessage, setDoneMessage] = useState("");
  const [error, setError] = useState("");

  const handleUnsubscribe = async () => {
    setError("");
    setLoading(true);
    try {
      const message = await unsubscribeWaitlist(token);
      setDoneMessage(message);
    } catch (err: any) {
      setError(
        typeof err?.response?.data?.detail === "string"
          ? err.response.data.detail
          : "Der Abmelde-Link ist ungültig. Bitte verwenden Sie den Link aus einer unserer E-Mails.",
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
            Von der Warteliste abmelden
          </p>
        </div>

        {!token ? (
          <>
            <div className="rounded-md bg-destructive/10 px-4 py-3 text-sm text-destructive">
              Der Link ist unvollständig oder ungültig. Bitte
              verwenden Sie den Abmelde-Link aus einer unserer
              E-Mails.
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
              Falls Sie es sich anders überlegen, können Sie sich
              jederzeit wieder eintragen.
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
              Klicken Sie auf den Button, um sich von der
              BauLV-Warteliste abzumelden. Sie erhalten danach keine
              weiteren E-Mails von uns.
            </p>
            <button
              type="button"
              onClick={handleUnsubscribe}
              disabled={loading}
              className="flex w-full items-center justify-center gap-2 rounded-md bg-primary px-4 py-2.5 text-sm font-medium text-primary-foreground hover:bg-primary/90 disabled:opacity-50"
            >
              <MailX className="h-4 w-4" />
              {loading ? "Wird abgemeldet..." : "Jetzt abmelden"}
            </button>
          </div>
        )}
      </div>
    </div>
  );
}
