import { useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { Upload, BookOpen, Loader2, CheckCircle, AlertCircle, Trash2 } from "lucide-react";
import api from "../api/client";
import type { ONormDokument, ONormRegel } from "../types/onorm";

const KNOWN_TRADES: { value: string; label: string }[] = [
  { value: "allgemein", label: "Allgemein (Vergabe/Vertrag)" },
  { value: "malerarbeiten", label: "Maler- und Beschichtungsarbeiten" },
  { value: "putzarbeiten", label: "Putz- und Verputzarbeiten" },
  { value: "estricharbeiten", label: "Estricharbeiten" },
  { value: "fliesenarbeiten", label: "Fliesen- und Plattenarbeiten" },
  { value: "trockenbau", label: "Trockenbauarbeiten" },
  { value: "tischlerarbeiten", label: "Tischlerarbeiten" },
  { value: "schlosserarbeiten", label: "Schlosserarbeiten" },
  { value: "heizungsarbeiten", label: "Heizungsarbeiten" },
  { value: "sanitaerarbeiten", label: "Sanitärarbeiten" },
  { value: "elektroarbeiten", label: "Elektroarbeiten" },
];

export function ONormManagementPage() {
  const queryClient = useQueryClient();
  const [normNummer, setNormNummer] = useState("");
  const [titel, setTitel] = useState("");
  const [trade, setTrade] = useState("");

  const { data: dokumente = [] } = useQuery<ONormDokument[]>({
    queryKey: ["onorm-dokumente"],
    queryFn: async () => {
      const { data } = await api.get("/onorm/dokumente");
      return data;
    },
  });

  const { data: regeln = [] } = useQuery<ONormRegel[]>({
    queryKey: ["onorm-regeln"],
    queryFn: async () => {
      const { data } = await api.get("/onorm/regeln");
      return data;
    },
  });

  const uploadMutation = useMutation({
    mutationFn: async (file: File) => {
      const formData = new FormData();
      formData.append("file", file);
      const params = new URLSearchParams();
      params.set("norm_nummer", normNummer);
      if (titel) params.set("titel", titel);
      if (trade) params.set("trade", trade);
      const { data } = await api.post(
        `/onorm/upload?${params.toString()}`,
        formData,
        { headers: { "Content-Type": "multipart/form-data" } }
      );
      return data;
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["onorm-dokumente"] });
      setNormNummer("");
      setTitel("");
      setTrade("");
    },
  });

  const deleteMutation = useMutation({
    mutationFn: async (id: string) => {
      await api.delete(`/onorm/dokumente/${id}`);
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["onorm-dokumente"] });
      queryClient.invalidateQueries({ queryKey: ["onorm-regeln"] });
    },
  });

  const handleUpload = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (file && normNummer) {
      uploadMutation.mutate(file);
    }
  };

  const tradeLabel = (tradeValue: string | null) => {
    if (!tradeValue) return null;
    return KNOWN_TRADES.find((t) => t.value === tradeValue)?.label ?? tradeValue;
  };

  return (
    <div className="p-6">
      <h1 className="mb-6 text-2xl font-bold">ÖNORM-Bibliothek</h1>

      {/* Upload form */}
      <div className="mb-8 rounded-lg border bg-card p-5">
        <h2 className="mb-4 text-lg font-semibold">ÖNORM-PDF hochladen</h2>
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
          <div>
            <label className="mb-1 block text-sm font-medium">ÖNORM-Nummer *</label>
            <input
              value={normNummer}
              onChange={(e) => setNormNummer(e.target.value)}
              className="w-full rounded-md border px-3 py-2 text-sm"
              placeholder="z.B. B 2230-1"
            />
          </div>
          <div>
            <label className="mb-1 block text-sm font-medium">Titel</label>
            <input
              value={titel}
              onChange={(e) => setTitel(e.target.value)}
              className="w-full rounded-md border px-3 py-2 text-sm"
              placeholder="z.B. Maler- und Beschichtungsarbeiten"
            />
          </div>
          <div>
            <label className="mb-1 block text-sm font-medium">Gewerk</label>
            <select
              value={trade}
              onChange={(e) => setTrade(e.target.value)}
              className="w-full rounded-md border px-3 py-2 text-sm"
            >
              <option value="">— Gewerk zuordnen —</option>
              {KNOWN_TRADES.map((t) => (
                <option key={t.value} value={t.value}>
                  {t.label}
                </option>
              ))}
            </select>
          </div>
          <div className="flex items-end">
            <label
              className={`flex cursor-pointer items-center gap-2 rounded-md px-4 py-2 text-sm font-medium ${
                normNummer
                  ? "bg-primary text-primary-foreground hover:bg-primary/90"
                  : "bg-muted text-muted-foreground cursor-not-allowed"
              }`}
            >
              <Upload className="h-4 w-4" />
              PDF hochladen
              <input
                type="file"
                accept=".pdf"
                onChange={handleUpload}
                disabled={!normNummer}
                className="hidden"
              />
            </label>
          </div>
        </div>
        {uploadMutation.isPending && (
          <div className="mt-3 flex items-center gap-2 text-sm text-primary">
            <Loader2 className="h-4 w-4 animate-spin" />
            Wird verarbeitet (Text extrahieren, Chunks erstellen)...
          </div>
        )}
      </div>

      {/* Documents list */}
      <div className="mb-8">
        <h2 className="mb-3 text-lg font-semibold">
          Hochgeladene Dokumente ({dokumente.length})
        </h2>
        {dokumente.length === 0 ? (
          <p className="text-sm text-muted-foreground">
            Noch keine ÖNORM-Dokumente hochgeladen. Laden Sie ÖNORMs hoch, um sie als
            Wissensbasis für die LV-Erstellung zu verwenden.
          </p>
        ) : (
          <div className="space-y-2">
            {dokumente.map((doc) => (
              <div
                key={doc.id}
                className="flex items-center justify-between rounded-lg border bg-card px-4 py-3"
              >
                <div className="flex items-center gap-3">
                  <BookOpen className="h-5 w-5 text-muted-foreground" />
                  <div>
                    <p className="text-sm font-medium">
                      ÖNORM {doc.norm_nummer}
                      {doc.titel && (
                        <span className="ml-2 font-normal text-muted-foreground">
                          — {doc.titel}
                        </span>
                      )}
                    </p>
                    {doc.trade && (
                      <p className="text-xs text-muted-foreground">
                        Gewerk: {tradeLabel(doc.trade)}
                      </p>
                    )}
                  </div>
                </div>
                <div className="flex items-center gap-3">
                  <span
                    className={`flex items-center gap-1 text-xs ${
                      doc.upload_status === "completed"
                        ? "text-green-600"
                        : doc.upload_status === "failed"
                        ? "text-destructive"
                        : "text-muted-foreground"
                    }`}
                  >
                    {doc.upload_status === "completed" ? (
                      <CheckCircle className="h-3.5 w-3.5" />
                    ) : doc.upload_status === "failed" ? (
                      <AlertCircle className="h-3.5 w-3.5" />
                    ) : (
                      <Loader2 className="h-3.5 w-3.5 animate-spin" />
                    )}
                    {doc.upload_status}
                  </span>
                  <button
                    onClick={() => deleteMutation.mutate(doc.id)}
                    disabled={deleteMutation.isPending}
                    className="rounded p-1 text-muted-foreground hover:bg-destructive/10 hover:text-destructive"
                    title="Dokument löschen"
                  >
                    <Trash2 className="h-4 w-4" />
                  </button>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>

      {/* Rules list */}
      {regeln.length > 0 && (
        <div>
          <h2 className="mb-3 text-lg font-semibold">Codierte Regeln ({regeln.length})</h2>
          <div className="overflow-x-auto rounded-lg border">
            <table className="w-full text-sm">
              <thead className="bg-muted/50">
                <tr>
                  <th className="px-4 py-2 text-left font-medium">Code</th>
                  <th className="px-4 py-2 text-left font-medium">Gewerk</th>
                  <th className="px-4 py-2 text-left font-medium">Kategorie</th>
                  <th className="px-4 py-2 text-left font-medium">Beschreibung</th>
                  <th className="px-4 py-2 text-left font-medium">ÖNORM</th>
                  <th className="px-4 py-2 text-center font-medium">Aktiv</th>
                </tr>
              </thead>
              <tbody className="divide-y">
                {regeln.map((regel) => (
                  <tr key={regel.id}>
                    <td className="px-4 py-2 font-mono text-xs">{regel.regel_code}</td>
                    <td className="px-4 py-2">{regel.trade}</td>
                    <td className="px-4 py-2 text-muted-foreground">{regel.category}</td>
                    <td className="px-4 py-2">{regel.description_de}</td>
                    <td className="px-4 py-2 text-muted-foreground">{regel.onorm_reference}</td>
                    <td className="px-4 py-2 text-center">
                      {regel.is_active ? "Ja" : "Nein"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  );
}
