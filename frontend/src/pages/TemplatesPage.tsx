import { useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import {
  LibraryBig,
  FileText,
  Sparkles,
  Layers,
  Trash2,
  X,
  Eye,
  Loader2,
  AlertTriangle,
  Check,
  Plus,
} from "lucide-react";
import {
  fetchTemplates,
  fetchTemplate,
  deleteTemplate,
  createLVFromTemplate,
} from "../api/templates";
import { fetchProjects } from "../api/projects";
import {
  TEMPLATE_CATEGORY_LABELS,
  TEMPLATE_GEWERK_LABELS,
  type TemplateCategory,
  type TemplateSummary,
  type TemplateDetail,
} from "../types/template";

const CATEGORY_ORDER: (TemplateCategory | "all")[] = [
  "all",
  "einfamilienhaus",
  "wohnanlage",
  "buero",
  "sanierung",
  "dachausbau",
];

function getErrorMessage(err: unknown): string {
  if (err && typeof err === "object" && "response" in err) {
    const resp = (err as any).response;
    const detail: string | undefined = resp?.data?.detail;
    if (detail) return detail;
    if (resp?.status === 403)
      return "Diese Aktion ist für diese Vorlage nicht erlaubt.";
    if (resp?.status === 404) return "Vorlage nicht gefunden.";
    if (resp?.status >= 500)
      return `Serverfehler (${resp.status}). Bitte versuchen Sie es später erneut.`;
  }
  return "Ein unerwarteter Fehler ist aufgetreten.";
}

export function TemplatesPage() {
  const queryClient = useQueryClient();
  const [categoryFilter, setCategoryFilter] = useState<TemplateCategory | "all">(
    "all"
  );
  const [previewId, setPreviewId] = useState<string | null>(null);
  const [createFromId, setCreateFromId] = useState<string | null>(null);
  const [deleteConfirmId, setDeleteConfirmId] = useState<string | null>(null);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);
  const [successMsg, setSuccessMsg] = useState<string | null>(null);

  const { data: templates = [], isLoading } = useQuery({
    queryKey: ["templates"],
    queryFn: () => fetchTemplates(),
  });

  const visible = useMemo(() => {
    if (categoryFilter === "all") return templates;
    return templates.filter((t) => t.category === categoryFilter);
  }, [templates, categoryFilter]);

  const systemTemplates = visible.filter((t) => t.is_system);
  const userTemplates = visible.filter((t) => !t.is_system);

  const deleteMutation = useMutation({
    mutationFn: (id: string) => deleteTemplate(id),
    onSuccess: (_data, id) => {
      queryClient.invalidateQueries({ queryKey: ["templates"] });
      setDeleteConfirmId(null);
      const tpl = templates.find((t) => t.id === id);
      setSuccessMsg(`Vorlage "${tpl?.name ?? ""}" gelöscht.`);
    },
    onError: (err) => {
      setDeleteConfirmId(null);
      setErrorMsg(getErrorMessage(err));
    },
  });

  const templateToDelete = deleteConfirmId
    ? templates.find((t) => t.id === deleteConfirmId)
    : null;

  return (
    <div className="p-6">
      {/* Header */}
      <div className="mb-6">
        <h1 className="text-2xl font-bold flex items-center gap-2">
          <LibraryBig className="h-6 w-6 text-primary" />
          LV-Vorlagen
        </h1>
        <p className="mt-1 text-sm text-muted-foreground">
          Starten Sie ein neues LV aus einer fertigen Vorlage statt bei
          Null — oder speichern Sie ein bestehendes LV als eigene Vorlage
          für Ihre nächsten Projekte.
        </p>
      </div>

      {/* Filters */}
      <div className="mb-6 flex flex-wrap items-center gap-2">
        {CATEGORY_ORDER.map((c) => (
          <button
            key={c}
            onClick={() => setCategoryFilter(c)}
            className={`rounded-full border px-3 py-1.5 text-sm transition-colors ${
              categoryFilter === c
                ? "border-primary bg-primary text-primary-foreground"
                : "hover:bg-accent"
            }`}
          >
            {c === "all" ? "Alle" : TEMPLATE_CATEGORY_LABELS[c]}
          </button>
        ))}
      </div>

      {/* Banners */}
      {errorMsg && (
        <div className="mb-4 flex items-start gap-3 rounded-md border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">
          <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
          <span className="flex-1">{errorMsg}</span>
          <button onClick={() => setErrorMsg(null)}>
            <X className="h-4 w-4" />
          </button>
        </div>
      )}
      {successMsg && (
        <div className="mb-4 flex items-start gap-3 rounded-md border border-green-200 bg-green-50 px-4 py-3 text-sm text-green-700">
          <Check className="mt-0.5 h-4 w-4 shrink-0" />
          <span className="flex-1">{successMsg}</span>
          <button onClick={() => setSuccessMsg(null)}>
            <X className="h-4 w-4" />
          </button>
        </div>
      )}

      {isLoading ? (
        <div className="py-12 text-center text-muted-foreground">
          Lade Vorlagen...
        </div>
      ) : (
        <>
          {/* System templates section */}
          {systemTemplates.length > 0 && (
            <section className="mb-10">
              <h2 className="mb-3 flex items-center gap-2 text-lg font-semibold">
                <Sparkles className="h-4 w-4 text-primary" />
                Mitgelieferte Vorlagen
              </h2>
              <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-3">
                {systemTemplates.map((tpl) => (
                  <TemplateCard
                    key={tpl.id}
                    template={tpl}
                    onPreview={() => setPreviewId(tpl.id)}
                    onCreateLV={() => setCreateFromId(tpl.id)}
                    // System templates are undeletable — hide the icon.
                    onDelete={null}
                  />
                ))}
              </div>
            </section>
          )}

          {/* User templates section — always show, with empty-state if
              the user hasn't saved any yet. */}
          <section>
            <h2 className="mb-3 flex items-center gap-2 text-lg font-semibold">
              <Layers className="h-4 w-4 text-muted-foreground" />
              Eigene Vorlagen
            </h2>
            {userTemplates.length === 0 ? (
              <div className="rounded-lg border border-dashed bg-card/60 px-6 py-10 text-center text-sm text-muted-foreground">
                <Layers className="mx-auto mb-3 h-10 w-10 text-muted-foreground/40" />
                <p>
                  Keine eigenen Vorlagen. Erstellen Sie Ihr erstes LV und
                  speichern Sie es über "Als Vorlage speichern" als
                  wiederverwendbare Vorlage.
                </p>
              </div>
            ) : (
              <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-3">
                {userTemplates.map((tpl) => (
                  <TemplateCard
                    key={tpl.id}
                    template={tpl}
                    onPreview={() => setPreviewId(tpl.id)}
                    onCreateLV={() => setCreateFromId(tpl.id)}
                    onDelete={() => setDeleteConfirmId(tpl.id)}
                  />
                ))}
              </div>
            )}
          </section>
        </>
      )}

      {/* Preview modal */}
      {previewId && (
        <TemplatePreviewModal
          templateId={previewId}
          onClose={() => setPreviewId(null)}
          onCreateLV={() => {
            setCreateFromId(previewId);
            setPreviewId(null);
          }}
        />
      )}

      {/* Create-LV-from-template modal */}
      {createFromId && (
        <CreateLVFromTemplateModal
          templateId={createFromId}
          templateName={
            templates.find((t) => t.id === createFromId)?.name ?? ""
          }
          onClose={() => setCreateFromId(null)}
          onSuccess={(msg) => {
            setSuccessMsg(msg);
            setCreateFromId(null);
          }}
        />
      )}

      {/* Delete confirmation */}
      {deleteConfirmId && templateToDelete && (
        <DeleteConfirmModal
          name={templateToDelete.name}
          onCancel={() => setDeleteConfirmId(null)}
          onConfirm={() => deleteMutation.mutate(deleteConfirmId)}
          isPending={deleteMutation.isPending}
        />
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Card
// ---------------------------------------------------------------------------

function TemplateCard({
  template,
  onPreview,
  onCreateLV,
  onDelete,
}: {
  template: TemplateSummary;
  onPreview: () => void;
  onCreateLV: () => void;
  onDelete: (() => void) | null;
}) {
  const gewerkLabel =
    TEMPLATE_GEWERK_LABELS[template.gewerk] ?? template.gewerk;
  const categoryLabel =
    TEMPLATE_CATEGORY_LABELS[template.category as TemplateCategory] ??
    template.category;

  return (
    <div className="group flex flex-col rounded-lg border bg-card p-5 shadow-sm transition-all hover:shadow-md hover:border-primary/30">
      <div className="flex items-start justify-between">
        <h3 className="font-semibold text-card-foreground">{template.name}</h3>
        {template.is_system ? (
          <span className="shrink-0 rounded-full bg-primary/10 px-2 py-0.5 text-xs font-medium text-primary">
            System
          </span>
        ) : (
          <span className="shrink-0 rounded-full bg-amber-100 px-2 py-0.5 text-xs font-medium text-amber-800">
            Eigene Vorlage
          </span>
        )}
      </div>

      <p className="mt-2 line-clamp-3 text-sm text-muted-foreground">
        {template.description ?? "—"}
      </p>

      <div className="mt-3 flex flex-wrap gap-2 text-xs">
        <span className="rounded bg-muted px-2 py-0.5 text-muted-foreground">
          {categoryLabel}
        </span>
        <span className="rounded bg-muted px-2 py-0.5 text-muted-foreground">
          {gewerkLabel}
        </span>
        <span className="rounded bg-muted px-2 py-0.5 text-muted-foreground">
          {template.positionen_count} Positionen
        </span>
        <span className="rounded bg-muted px-2 py-0.5 text-muted-foreground">
          {template.gruppen_count} Leistungsgruppen
        </span>
      </div>

      <div className="mt-4 flex gap-2">
        <button
          onClick={onCreateLV}
          className="flex flex-1 items-center justify-center gap-1.5 rounded-md bg-primary px-3 py-2 text-sm font-medium text-primary-foreground hover:bg-primary/90"
        >
          <Plus className="h-3.5 w-3.5" />
          LV aus Vorlage erstellen
        </button>
        <button
          onClick={onPreview}
          className="flex items-center justify-center rounded-md border p-2 text-sm hover:bg-accent"
          title="Vorschau"
        >
          <Eye className="h-4 w-4" />
        </button>
        {onDelete && (
          <button
            onClick={onDelete}
            className="flex items-center justify-center rounded-md border p-2 text-sm text-destructive hover:bg-destructive/10"
            title="Vorlage löschen"
          >
            <Trash2 className="h-4 w-4" />
          </button>
        )}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Preview modal
// ---------------------------------------------------------------------------

function TemplatePreviewModal({
  templateId,
  onClose,
  onCreateLV,
}: {
  templateId: string;
  onClose: () => void;
  onCreateLV: () => void;
}) {
  const { data: detail, isLoading } = useQuery<TemplateDetail>({
    queryKey: ["template", templateId],
    queryFn: () => fetchTemplate(templateId),
  });

  return (
    <Modal onClose={onClose}>
      <div className="flex items-start justify-between border-b px-6 py-4">
        <div>
          <h2 className="text-lg font-semibold">
            {detail?.name ?? "Vorlage wird geladen…"}
          </h2>
          {detail?.description && (
            <p className="mt-1 text-sm text-muted-foreground">
              {detail.description}
            </p>
          )}
        </div>
        <button
          onClick={onClose}
          className="rounded p-1.5 text-muted-foreground hover:bg-accent"
          aria-label="Schließen"
        >
          <X className="h-4 w-4" />
        </button>
      </div>

      <div className="flex-1 overflow-auto px-6 py-4">
        {isLoading || !detail ? (
          <div className="py-8 text-center text-muted-foreground">
            <Loader2 className="mx-auto h-5 w-5 animate-spin" />
          </div>
        ) : (
          <div className="space-y-4">
            {detail.template_data.gruppen.map((gruppe) => (
              <div key={`${gruppe.nummer}-${gruppe.bezeichnung}`} className="rounded-lg border">
                <div className="bg-muted/30 px-4 py-2 text-sm font-semibold">
                  LG {gruppe.nummer} — {gruppe.bezeichnung}
                </div>
                <div className="divide-y text-sm">
                  {gruppe.positionen.map((pos) => (
                    <div key={pos.positions_nummer} className="px-4 py-2">
                      <div className="flex items-baseline gap-3">
                        <span className="w-16 shrink-0 font-mono text-xs text-muted-foreground">
                          {pos.positions_nummer}
                        </span>
                        <span className="flex-1">{pos.kurztext}</span>
                        <span className="w-10 shrink-0 text-xs text-muted-foreground">
                          {pos.einheit}
                        </span>
                      </div>
                      {pos.langtext && (
                        <p className="mt-1 pl-16 pr-16 text-xs italic text-muted-foreground">
                          {pos.langtext}
                        </p>
                      )}
                    </div>
                  ))}
                </div>
              </div>
            ))}
          </div>
        )}
      </div>

      <div className="flex justify-end gap-2 border-t px-6 py-3">
        <button
          onClick={onClose}
          className="rounded-md border px-4 py-2 text-sm hover:bg-accent"
        >
          Schließen
        </button>
        <button
          onClick={onCreateLV}
          disabled={!detail}
          className="flex items-center gap-1.5 rounded-md bg-primary px-4 py-2 text-sm font-medium text-primary-foreground hover:bg-primary/90 disabled:opacity-50"
        >
          <Plus className="h-4 w-4" />
          LV aus Vorlage erstellen
        </button>
      </div>
    </Modal>
  );
}

// ---------------------------------------------------------------------------
// Create-LV-from-template modal
// ---------------------------------------------------------------------------

function CreateLVFromTemplateModal({
  templateId,
  templateName,
  onClose,
  onSuccess,
}: {
  templateId: string;
  templateName: string;
  onClose: () => void;
  onSuccess: (msg: string) => void;
}) {
  const navigate = useNavigate();
  const [projectId, setProjectId] = useState<string>("");
  const [lvName, setLvName] = useState<string>(templateName);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);

  const { data: projects = [], isLoading: projectsLoading } = useQuery({
    queryKey: ["projects"],
    queryFn: fetchProjects,
  });

  const createMutation = useMutation({
    mutationFn: () =>
      createLVFromTemplate({
        project_id: projectId,
        template_id: templateId,
        name: lvName.trim() || templateName,
      }),
    onSuccess: (data) => {
      onSuccess(
        `LV "${data.name}" aus Vorlage "${templateName}" erstellt — ${data.positionen_created} Positionen übernommen.`
      );
      // Jump straight into the new LV so the user sees what they just made.
      navigate(`/app/projects/${data.project_id}/lv/${data.lv_id}`);
    },
    onError: (err) => {
      setErrorMsg(getErrorMessage(err));
    },
  });

  const canSubmit = projectId && !createMutation.isPending;

  return (
    <Modal onClose={onClose}>
      <div className="flex items-start justify-between border-b px-6 py-4">
        <h2 className="text-lg font-semibold">LV aus Vorlage erstellen</h2>
        <button
          onClick={onClose}
          className="rounded p-1.5 text-muted-foreground hover:bg-accent"
          aria-label="Schließen"
        >
          <X className="h-4 w-4" />
        </button>
      </div>

      <div className="space-y-4 px-6 py-4">
        <div className="rounded-md border bg-muted/30 px-3 py-2 text-sm">
          <span className="text-muted-foreground">Vorlage: </span>
          <span className="font-medium">{templateName}</span>
        </div>

        <div>
          <label className="mb-1 block text-sm font-medium">
            Zielprojekt <span className="text-destructive">*</span>
          </label>
          <select
            value={projectId}
            onChange={(e) => setProjectId(e.target.value)}
            className="w-full rounded-md border px-3 py-2 text-sm"
            disabled={projectsLoading}
          >
            <option value="">— Projekt auswählen —</option>
            {projects.map((p) => (
              <option key={p.id} value={p.id}>
                {p.name}
              </option>
            ))}
          </select>
          {projects.length === 0 && !projectsLoading && (
            <p className="mt-1 text-xs text-amber-700">
              Noch kein Projekt vorhanden. Legen Sie zuerst ein Projekt
              im Dashboard an.
            </p>
          )}
        </div>

        <div>
          <label className="mb-1 block text-sm font-medium">LV-Name</label>
          <input
            value={lvName}
            onChange={(e) => setLvName(e.target.value)}
            className="w-full rounded-md border px-3 py-2 text-sm"
            placeholder={templateName}
          />
          <p className="mt-1 text-xs text-muted-foreground">
            Leer lassen, um den Vorlagennamen zu übernehmen.
          </p>
        </div>

        {errorMsg && (
          <div className="rounded-md border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700">
            {errorMsg}
          </div>
        )}
      </div>

      <div className="flex justify-end gap-2 border-t px-6 py-3">
        <button
          onClick={onClose}
          className="rounded-md border px-4 py-2 text-sm hover:bg-accent"
        >
          Abbrechen
        </button>
        <button
          onClick={() => createMutation.mutate()}
          disabled={!canSubmit}
          className="flex items-center gap-1.5 rounded-md bg-primary px-4 py-2 text-sm font-medium text-primary-foreground hover:bg-primary/90 disabled:opacity-50"
        >
          {createMutation.isPending ? (
            <Loader2 className="h-3.5 w-3.5 animate-spin" />
          ) : (
            <FileText className="h-3.5 w-3.5" />
          )}
          {createMutation.isPending ? "Erstelle…" : "LV erstellen"}
        </button>
      </div>
    </Modal>
  );
}

// ---------------------------------------------------------------------------
// Delete confirmation
// ---------------------------------------------------------------------------

function DeleteConfirmModal({
  name,
  onCancel,
  onConfirm,
  isPending,
}: {
  name: string;
  onCancel: () => void;
  onConfirm: () => void;
  isPending: boolean;
}) {
  return (
    <Modal onClose={onCancel}>
      <div className="px-6 py-5">
        <h2 className="text-lg font-semibold">Vorlage löschen</h2>
        <p className="mt-2 text-sm text-muted-foreground">
          Vorlage <strong>"{name}"</strong> wirklich löschen? Diese Aktion
          kann nicht rückgängig gemacht werden.
        </p>
      </div>
      <div className="flex justify-end gap-2 border-t px-6 py-3">
        <button
          onClick={onCancel}
          className="rounded-md border px-4 py-2 text-sm hover:bg-accent"
        >
          Abbrechen
        </button>
        <button
          onClick={onConfirm}
          disabled={isPending}
          className="flex items-center gap-1.5 rounded-md bg-destructive px-4 py-2 text-sm font-medium text-destructive-foreground hover:bg-destructive/90 disabled:opacity-50"
        >
          {isPending ? (
            <Loader2 className="h-3.5 w-3.5 animate-spin" />
          ) : (
            <Trash2 className="h-3.5 w-3.5" />
          )}
          Löschen
        </button>
      </div>
    </Modal>
  );
}

// ---------------------------------------------------------------------------
// Modal shell — simple fixed-position overlay. No shadcn primitive in
// this codebase yet, so we inline it here rather than introduce a
// dependency. Click on backdrop closes.
// ---------------------------------------------------------------------------

function Modal({
  children,
  onClose,
}: {
  children: React.ReactNode;
  onClose: () => void;
}) {
  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4"
      onClick={onClose}
      role="dialog"
      aria-modal="true"
    >
      <div
        className="flex max-h-[90vh] w-full max-w-2xl flex-col overflow-hidden rounded-lg bg-background shadow-xl"
        onClick={(e) => e.stopPropagation()}
      >
        {children}
      </div>
    </div>
  );
}
