import { useState } from "react";
import { useParams, Link } from "react-router-dom";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import {
  ArrowLeft,
  Calculator,
  FileSpreadsheet,
  Sparkles,
  ChevronDown,
  ChevronRight,
  Download,
  Loader2,
  Plus,
  BookOpen,
  Check,
  AlertTriangle,
  X,
} from "lucide-react";
import api from "../api/client";
import { fetchProjectLVs, fetchLV, createLV, calculateLV, generateTexts, exportLV, updateONormSelection } from "../api/lv";
import type { LVCreate, Leistungsgruppe, Position, Berechnungsnachweis, ONormSelectionItem } from "../types/lv";
import type { ONormDokument } from "../types/onorm";

const TRADES = [
  { value: "malerarbeiten", label: "Malerarbeiten", onorm: "B 2230-1" },
];

function getErrorMessage(err: unknown): string {
  if (err && typeof err === "object" && "response" in err) {
    const resp = (err as any).response;
    const detail: string | undefined = resp?.data?.detail;
    // Special-case: backend signals "no rooms" via a ValueError → 400.
    if (detail && /keine\s+r[äa]ume/i.test(detail)) {
      return "Bitte zuerst Plananalyse durchführen — es wurden noch keine Räume für dieses Projekt erfasst.";
    }
    if (detail) return detail;
    if (resp?.status === 403) return "Diese Funktion erfordert ein Upgrade Ihres Plans.";
    if (resp?.status === 401) return "Bitte melden Sie sich erneut an.";
    if (resp?.status === 404) return "Ressource nicht gefunden (404).";
    if (resp?.status >= 500) return `Serverfehler (${resp.status}). Bitte versuchen Sie es später erneut.`;
  }
  if (err && typeof err === "object" && "message" in err) {
    const msg = (err as any).message;
    if (typeof msg === "string" && msg.length > 0) return `Netzwerkfehler: ${msg}`;
  }
  return "Ein unerwarteter Fehler ist aufgetreten.";
}

export function LVEditorPage() {
  const { id: projectId, lvId } = useParams<{ id: string; lvId?: string }>();
  const queryClient = useQueryClient();
  const [showCreate, setShowCreate] = useState(false);
  const [showOnormPicker, setShowOnormPicker] = useState(false);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);
  const [successMsg, setSuccessMsg] = useState<string | null>(null);

  const { data: lvs = [] } = useQuery({
    queryKey: ["lvs", projectId],
    queryFn: () => fetchProjectLVs(projectId!),
    enabled: !!projectId,
  });

  const activeLvId = lvId || lvs[0]?.id;

  const { data: activeLV } = useQuery({
    queryKey: ["lv", activeLvId],
    queryFn: () => fetchLV(activeLvId!),
    enabled: !!activeLvId,
  });

  const createMutation = useMutation({
    mutationFn: (data: LVCreate) => createLV(projectId!, data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["lvs", projectId] });
      setShowCreate(false);
    },
  });

  const calcMutation = useMutation({
    mutationFn: async () => {
      if (!activeLvId) {
        throw new Error("Kein LV ausgewählt.");
      }
      return calculateLV(activeLvId);
    },
    onSuccess: (data) => {
      queryClient.invalidateQueries({ queryKey: ["lv", activeLvId] });
      setErrorMsg(null);
      setSuccessMsg(
        `Berechnung abgeschlossen: ${data.positions_calculated} Positionen berechnet.`
      );
    },
    onError: (err) => {
      // Always log so failures are debuggable in the browser console.
      // eslint-disable-next-line no-console
      console.error("[LV] calculate failed:", err);
      setSuccessMsg(null);
      setErrorMsg(getErrorMessage(err));
    },
  });

  const handleCalculate = () => {
    setErrorMsg(null);
    setSuccessMsg(null);
    if (!activeLvId) {
      setErrorMsg("Kein LV ausgewählt — bitte erst ein LV anlegen oder auswählen.");
      return;
    }
    calcMutation.mutate(undefined, {
      onError: (err) => {
        // Belt-and-braces: useMutation onError above already handles this,
        // but we keep a per-call hook so the call site can never fail silently.
        // eslint-disable-next-line no-console
        console.error("[LV] calculate mutate onError:", err);
      },
    });
  };

  const textMutation = useMutation({
    mutationFn: () => generateTexts(activeLvId!),
    onSuccess: (data) => {
      queryClient.invalidateQueries({ queryKey: ["lv", activeLvId] });
      setErrorMsg(null);
      setSuccessMsg(`AI-Texte generiert: ${data.positions_updated} Positionen aktualisiert.`);
    },
    onError: (err) => {
      setSuccessMsg(null);
      setErrorMsg(getErrorMessage(err));
    },
  });

  const onormSelectionMutation = useMutation({
    mutationFn: (ids: string[]) => updateONormSelection(activeLvId!, ids),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["lv", activeLvId] });
      setShowOnormPicker(false);
    },
  });

  const handleExport = async () => {
    if (!activeLvId) return;
    try {
      setErrorMsg(null);
      const blob = await exportLV(activeLvId);
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `LV_${activeLV?.trade ?? "export"}.xlsx`;
      a.click();
      URL.revokeObjectURL(url);
    } catch (err) {
      setErrorMsg(getErrorMessage(err));
    }
  };

  return (
    <div className="flex h-full flex-col">
      {/* Header */}
      <div className="border-b px-6 py-3">
        <Link
          to={`/app/projects/${projectId}`}
          className="mb-2 inline-flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground"
        >
          <ArrowLeft className="h-4 w-4" />
          Zum Projekt
        </Link>
        <div className="flex items-center justify-between">
          <h1 className="text-xl font-bold">Leistungsverzeichnis</h1>
          <div className="flex items-center gap-2">
            <button
              onClick={() => setShowCreate(true)}
              className="flex items-center gap-1.5 rounded-md border px-3 py-1.5 text-sm hover:bg-accent"
            >
              <Plus className="h-3.5 w-3.5" />
              Neues LV
            </button>
            {activeLvId && (
              <>
                <button
                  onClick={() => setShowOnormPicker(true)}
                  className="flex items-center gap-1.5 rounded-md border px-3 py-1.5 text-sm hover:bg-accent"
                >
                  <BookOpen className="h-3.5 w-3.5" />
                  ÖNORMs
                </button>
                <button
                  type="button"
                  onClick={handleCalculate}
                  disabled={calcMutation.isPending}
                  aria-busy={calcMutation.isPending}
                  className="flex items-center gap-1.5 rounded-md bg-primary px-3 py-1.5 text-sm font-medium text-primary-foreground hover:bg-primary/90 disabled:cursor-not-allowed disabled:opacity-60"
                >
                  {calcMutation.isPending ? (
                    <Loader2 className="h-3.5 w-3.5 animate-spin" />
                  ) : (
                    <Calculator className="h-3.5 w-3.5" />
                  )}
                  {calcMutation.isPending ? "Berechne…" : "Berechnen"}
                </button>
                <button
                  onClick={() => textMutation.mutate()}
                  disabled={textMutation.isPending}
                  className="flex items-center gap-1.5 rounded-md border px-3 py-1.5 text-sm hover:bg-accent disabled:opacity-50"
                >
                  {textMutation.isPending ? (
                    <Loader2 className="h-3.5 w-3.5 animate-spin" />
                  ) : (
                    <Sparkles className="h-3.5 w-3.5" />
                  )}
                  AI-Texte
                </button>
                <button
                  onClick={handleExport}
                  className="flex items-center gap-1.5 rounded-md border px-3 py-1.5 text-sm hover:bg-accent"
                >
                  <Download className="h-3.5 w-3.5" />
                  Excel Export
                </button>
              </>
            )}
          </div>
        </div>
      </div>

      {/* Create LV form */}
      {showCreate && (
        <div className="border-b bg-muted/30 px-6 py-4">
          <CreateLVForm
            onSubmit={(data) => createMutation.mutate(data)}
            onCancel={() => setShowCreate(false)}
            isLoading={createMutation.isPending}
          />
        </div>
      )}

      {/* ÖNORM picker overlay */}
      {showOnormPicker && activeLV && (
        <div className="border-b bg-muted/30 px-6 py-4">
          <ONormPicker
            currentSelection={activeLV.selected_onorms}
            onSave={(ids) => onormSelectionMutation.mutate(ids)}
            onCancel={() => setShowOnormPicker(false)}
            isLoading={onormSelectionMutation.isPending}
          />
        </div>
      )}

      {/* LV tabs */}
      {lvs.length > 1 && (
        <div className="flex gap-1 border-b px-6 pt-2">
          {lvs.map((lv) => (
            <Link
              key={lv.id}
              to={`/app/projects/${projectId}/lv/${lv.id}`}
              className={`rounded-t-md border border-b-0 px-3 py-1.5 text-sm ${
                lv.id === activeLvId
                  ? "bg-background font-medium"
                  : "bg-muted/50 text-muted-foreground hover:text-foreground"
              }`}
            >
              {lv.trade}
            </Link>
          ))}
        </div>
      )}

      {/* Error / success banners */}
      {errorMsg && (
        <div className="mx-6 mt-4 flex items-start gap-3 rounded-md border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">
          <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
          <span className="flex-1">{errorMsg}</span>
          <button onClick={() => setErrorMsg(null)} className="shrink-0">
            <X className="h-4 w-4" />
          </button>
        </div>
      )}
      {successMsg && (
        <div className="mx-6 mt-4 flex items-start gap-3 rounded-md border border-green-200 bg-green-50 px-4 py-3 text-sm text-green-700">
          <Check className="mt-0.5 h-4 w-4 shrink-0" />
          <span className="flex-1">{successMsg}</span>
          <button onClick={() => setSuccessMsg(null)} className="shrink-0">
            <X className="h-4 w-4" />
          </button>
        </div>
      )}

      {/* LV Content */}
      <div className="flex-1 overflow-auto p-6">
        {!activeLV ? (
          <div className="py-12 text-center text-muted-foreground">
            <FileSpreadsheet className="mx-auto h-12 w-12 text-muted-foreground/50" />
            <p className="mt-4">Noch kein LV vorhanden. Erstellen Sie ein neues LV.</p>
          </div>
        ) : (
          <>
            {/* LV info + selected ÖNORMs */}
            <div className="mb-6 space-y-2">
              <div className="flex items-center gap-4 text-sm text-muted-foreground">
                <span>Gewerk: <strong className="text-foreground">{activeLV.trade}</strong></span>
                <span>ÖNORM: <strong className="text-foreground">{activeLV.onorm_basis}</strong></span>
                <span>Status: {activeLV.status}</span>
              </div>
              {activeLV.selected_onorms.length > 0 && (
                <div className="flex flex-wrap items-center gap-2">
                  <span className="text-xs font-medium text-muted-foreground">Wissensbasis:</span>
                  {activeLV.selected_onorms.map((doc) => (
                    <span
                      key={doc.id}
                      className="inline-flex items-center gap-1 rounded-full bg-primary/10 px-2.5 py-0.5 text-xs font-medium text-primary"
                    >
                      <BookOpen className="h-3 w-3" />
                      ÖNORM {doc.norm_nummer}
                    </span>
                  ))}
                </div>
              )}
            </div>

            {activeLV.gruppen.length === 0 ? (
              <div className="py-12 text-center text-muted-foreground">
                <Calculator className="mx-auto h-12 w-12 text-muted-foreground/50" />
                <p className="mt-4">
                  LV "{activeLV.trade}" ist leer. Klicken Sie "Berechnen", um die Mengen zu ermitteln.
                </p>
              </div>
            ) : (
              <div className="space-y-6">
                {activeLV.gruppen
                  .sort((a, b) => a.sort_order - b.sort_order)
                  .map((gruppe) => (
                    <LeistungsgruppeView key={gruppe.id} gruppe={gruppe} />
                  ))}
              </div>
            )}
          </>
        )}
      </div>
    </div>
  );
}


function CreateLVForm({
  onSubmit,
  onCancel,
  isLoading,
}: {
  onSubmit: (data: LVCreate) => void;
  onCancel: () => void;
  isLoading: boolean;
}) {
  const [trade, setTrade] = useState(TRADES[0].value);
  const [selectedOnormIds, setSelectedOnormIds] = useState<Set<string>>(new Set());
  const selected = TRADES.find((t) => t.value === trade)!;

  // Fetch available ÖNORM documents
  const { data: dokumente = [] } = useQuery<ONormDokument[]>({
    queryKey: ["onorm-dokumente"],
    queryFn: async () => {
      const { data } = await api.get("/onorm/dokumente");
      return data;
    },
  });

  const completedDocs = dokumente.filter((d) => d.upload_status === "completed");

  const toggleOnorm = (id: string) => {
    setSelectedOnormIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  const selectAll = () => {
    setSelectedOnormIds(new Set(completedDocs.map((d) => d.id)));
  };

  const selectNone = () => {
    setSelectedOnormIds(new Set());
  };

  return (
    <div className="space-y-4">
      <div className="flex items-end gap-4">
        <div>
          <label className="mb-1 block text-sm font-medium">Gewerk</label>
          <select
            value={trade}
            onChange={(e) => setTrade(e.target.value)}
            className="rounded-md border px-3 py-2 text-sm"
          >
            {TRADES.map((t) => (
              <option key={t.value} value={t.value}>
                {t.label} ({t.onorm})
              </option>
            ))}
          </select>
        </div>
      </div>

      {/* ÖNORM checklist */}
      {completedDocs.length > 0 && (
        <div>
          <div className="mb-2 flex items-center justify-between">
            <label className="text-sm font-medium">
              ÖNORM-Wissensbasis auswählen
            </label>
            <div className="flex gap-2 text-xs">
              <button onClick={selectAll} className="text-primary hover:underline">
                Alle
              </button>
              <span className="text-muted-foreground">|</span>
              <button onClick={selectNone} className="text-primary hover:underline">
                Keine
              </button>
            </div>
          </div>
          <div className="grid gap-1.5 sm:grid-cols-2 lg:grid-cols-3">
            {completedDocs.map((doc) => {
              const checked = selectedOnormIds.has(doc.id);
              return (
                <label
                  key={doc.id}
                  className={`flex cursor-pointer items-center gap-2.5 rounded-md border px-3 py-2 text-sm transition-colors ${
                    checked
                      ? "border-primary/50 bg-primary/5"
                      : "border-border hover:bg-accent/50"
                  }`}
                >
                  <div
                    className={`flex h-4 w-4 shrink-0 items-center justify-center rounded border ${
                      checked
                        ? "border-primary bg-primary text-primary-foreground"
                        : "border-muted-foreground/30"
                    }`}
                  >
                    {checked && <Check className="h-3 w-3" />}
                  </div>
                  <input
                    type="checkbox"
                    checked={checked}
                    onChange={() => toggleOnorm(doc.id)}
                    className="hidden"
                  />
                  <div className="min-w-0">
                    <p className="font-medium">ÖNORM {doc.norm_nummer}</p>
                    {doc.titel && (
                      <p className="truncate text-xs text-muted-foreground">{doc.titel}</p>
                    )}
                  </div>
                </label>
              );
            })}
          </div>
          {selectedOnormIds.size > 0 && (
            <p className="mt-2 text-xs text-muted-foreground">
              {selectedOnormIds.size} ÖNORM(en) ausgewählt — die AI verwendet nur diese als Wissensbasis.
            </p>
          )}
        </div>
      )}

      {completedDocs.length === 0 && (
        <p className="text-sm text-muted-foreground">
          Keine ÖNORMs in der Bibliothek vorhanden.{" "}
          <Link to="/settings/onorm" className="text-primary hover:underline">
            ÖNORMs hochladen
          </Link>
        </p>
      )}

      <div className="flex gap-2">
        <button
          onClick={() =>
            onSubmit({
              name: `LV ${selected.label}`,
              trade: selected.value,
              onorm_basis: selected.onorm,
              selected_onorm_ids: Array.from(selectedOnormIds),
            })
          }
          disabled={isLoading}
          className="rounded-md bg-primary px-4 py-2 text-sm font-medium text-primary-foreground hover:bg-primary/90 disabled:opacity-50"
        >
          {isLoading ? "Erstelle..." : "LV erstellen"}
        </button>
        <button
          onClick={onCancel}
          className="rounded-md border px-4 py-2 text-sm hover:bg-accent"
        >
          Abbrechen
        </button>
      </div>
    </div>
  );
}


function ONormPicker({
  currentSelection,
  onSave,
  onCancel,
  isLoading,
}: {
  currentSelection: ONormSelectionItem[];
  onSave: (ids: string[]) => void;
  onCancel: () => void;
  isLoading: boolean;
}) {
  const [selectedIds, setSelectedIds] = useState<Set<string>>(
    new Set(currentSelection.map((d) => d.id))
  );

  const { data: dokumente = [] } = useQuery<ONormDokument[]>({
    queryKey: ["onorm-dokumente"],
    queryFn: async () => {
      const { data } = await api.get("/onorm/dokumente");
      return data;
    },
  });

  const completedDocs = dokumente.filter((d) => d.upload_status === "completed");

  const toggle = (id: string) => {
    setSelectedIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  return (
    <div className="space-y-3">
      <h3 className="text-sm font-semibold">ÖNORM-Wissensbasis für dieses LV</h3>
      <p className="text-xs text-muted-foreground">
        Wählen Sie die ÖNORMs aus, die die AI als Wissensbasis für Positionstexte und Chat verwenden soll.
      </p>
      <div className="grid gap-1.5 sm:grid-cols-2 lg:grid-cols-3">
        {completedDocs.map((doc) => {
          const checked = selectedIds.has(doc.id);
          return (
            <label
              key={doc.id}
              className={`flex cursor-pointer items-center gap-2.5 rounded-md border px-3 py-2 text-sm transition-colors ${
                checked
                  ? "border-primary/50 bg-primary/5"
                  : "border-border hover:bg-accent/50"
              }`}
            >
              <div
                className={`flex h-4 w-4 shrink-0 items-center justify-center rounded border ${
                  checked
                    ? "border-primary bg-primary text-primary-foreground"
                    : "border-muted-foreground/30"
                }`}
              >
                {checked && <Check className="h-3 w-3" />}
              </div>
              <input
                type="checkbox"
                checked={checked}
                onChange={() => toggle(doc.id)}
                className="hidden"
              />
              <div className="min-w-0">
                <p className="font-medium">ÖNORM {doc.norm_nummer}</p>
                {doc.titel && (
                  <p className="truncate text-xs text-muted-foreground">{doc.titel}</p>
                )}
              </div>
            </label>
          );
        })}
      </div>
      {completedDocs.length === 0 && (
        <p className="text-sm text-muted-foreground">
          Keine ÖNORMs verfügbar.{" "}
          <Link to="/settings/onorm" className="text-primary hover:underline">
            ÖNORMs hochladen
          </Link>
        </p>
      )}
      <div className="flex gap-2">
        <button
          onClick={() => onSave(Array.from(selectedIds))}
          disabled={isLoading}
          className="rounded-md bg-primary px-4 py-2 text-sm font-medium text-primary-foreground hover:bg-primary/90 disabled:opacity-50"
        >
          {isLoading ? "Speichere..." : `Speichern (${selectedIds.size} ausgewählt)`}
        </button>
        <button
          onClick={onCancel}
          className="rounded-md border px-4 py-2 text-sm hover:bg-accent"
        >
          Abbrechen
        </button>
      </div>
    </div>
  );
}


function LeistungsgruppeView({ gruppe }: { gruppe: Leistungsgruppe }) {
  return (
    <div className="rounded-lg border">
      <div className="bg-muted/30 px-4 py-2.5">
        <h3 className="font-semibold">
          LG {gruppe.nummer} — {gruppe.bezeichnung}
        </h3>
      </div>
      <div className="divide-y">
        {gruppe.positionen
          .sort((a, b) => a.sort_order - b.sort_order)
          .map((position) => (
            <PositionRow key={position.id} position={position} />
          ))}
      </div>
    </div>
  );
}

function PositionRow({ position }: { position: Position }) {
  const [expanded, setExpanded] = useState(false);

  return (
    <div>
      <div
        className="flex cursor-pointer items-center gap-3 px-4 py-3 hover:bg-muted/20"
        onClick={() => setExpanded(!expanded)}
      >
        <button className="shrink-0">
          {expanded ? (
            <ChevronDown className="h-4 w-4 text-muted-foreground" />
          ) : (
            <ChevronRight className="h-4 w-4 text-muted-foreground" />
          )}
        </button>
        <span className="w-16 shrink-0 font-mono text-sm text-muted-foreground">
          {position.positions_nummer}
        </span>
        <span className="flex-1 text-sm">{position.kurztext}</span>
        <span className="w-20 shrink-0 text-right font-mono text-sm">
          {position.menge?.toFixed(3)}
        </span>
        <span className="w-10 shrink-0 text-center text-xs text-muted-foreground">
          {position.einheit}
        </span>
        <span className="w-20 shrink-0 text-right font-mono text-sm text-muted-foreground">
          {position.einheitspreis ? `€ ${position.einheitspreis.toFixed(2)}` : "—"}
        </span>
        <span className="w-24 shrink-0 text-right font-mono text-sm font-medium">
          {position.gesamtpreis ? `€ ${position.gesamtpreis.toFixed(2)}` : "—"}
        </span>
      </div>

      {/* Langtext */}
      {expanded && position.langtext && (
        <div className="bg-muted/10 px-12 py-2 text-sm italic text-muted-foreground">
          {position.langtext}
        </div>
      )}

      {/* Berechnungsnachweis */}
      {expanded && position.berechnungsnachweise.length > 0 && (
        <div className="bg-muted/10 px-12 pb-3">
          <h4 className="mb-2 text-xs font-semibold uppercase text-muted-foreground">
            Berechnungsnachweis
          </h4>
          <table className="w-full text-xs">
            <thead>
              <tr className="text-muted-foreground">
                <th className="py-1 text-left">Raum</th>
                <th className="py-1 text-left">Formel</th>
                <th className="py-1 text-right">Rohmaß</th>
                <th className="py-1 text-right">Faktor</th>
                <th className="py-1 text-left">ÖNORM</th>
                <th className="py-1 text-right">Netto</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-border/50">
              {position.berechnungsnachweise.map((bn) => (
                <BerechnungsnachweisRow key={bn.id} nachweis={bn} />
              ))}
            </tbody>
            <tfoot>
              <tr className="font-medium">
                <td colSpan={5} className="py-1 text-right">
                  Summe:
                </td>
                <td className="py-1 text-right font-mono">
                  {position.menge?.toFixed(3)} {position.einheit}
                </td>
              </tr>
            </tfoot>
          </table>
        </div>
      )}
    </div>
  );
}

function BerechnungsnachweisRow({ nachweis }: { nachweis: Berechnungsnachweis }) {
  return (
    <tr>
      <td className="py-1">{nachweis.formula_description.split(" ").slice(0, 3).join(" ")}</td>
      <td className="py-1 font-mono text-muted-foreground">
        {nachweis.formula_expression}
      </td>
      <td className="py-1 text-right font-mono">{nachweis.raw_quantity.toFixed(3)}</td>
      <td className="py-1 text-right font-mono">
        {nachweis.onorm_factor !== 1 ? `×${nachweis.onorm_factor.toFixed(2)}` : "—"}
      </td>
      <td className="py-1 text-muted-foreground">{nachweis.onorm_paragraph ?? ""}</td>
      <td className="py-1 text-right font-mono font-medium">
        {nachweis.net_quantity.toFixed(3)}
      </td>
    </tr>
  );
}
