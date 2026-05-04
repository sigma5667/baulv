import { useEffect, useRef, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import {
  Plus,
  FolderOpen,
  Clock,
  MapPin,
  ChevronRight,
  LayoutDashboard,
  FileText,
  MoreVertical,
  Trash2,
  Check,
  X,
} from "lucide-react";
import { fetchProjects, createProject, deleteProject } from "../api/projects";
import type { Project, ProjectCreate } from "../types/project";
import { DeleteConfirmModal } from "../components/DeleteConfirmModal";

const STATUS_LABELS: Record<string, { label: string; color: string }> = {
  draft: { label: "Entwurf", color: "bg-gray-100 text-gray-700" },
  in_progress: { label: "In Bearbeitung", color: "bg-blue-100 text-blue-700" },
  completed: { label: "Abgeschlossen", color: "bg-green-100 text-green-700" },
};

export function DashboardPage() {
  const [showForm, setShowForm] = useState(false);
  const queryClient = useQueryClient();

  // Project being targeted by the delete confirmation modal. ``null``
  // means no modal open. Lifted to the page level so the modal sits
  // outside the card grid (z-index above hover effects, single
  // instance regardless of which card opened it).
  const [deleteTarget, setDeleteTarget] = useState<Project | null>(null);
  // Brief toast after a successful action (currently delete; could be
  // extended to create later). Auto-clears after 3s.
  const [toast, setToast] = useState<string | null>(null);

  const { data: projects = [], isLoading } = useQuery({
    queryKey: ["projects"],
    queryFn: fetchProjects,
  });

  const createMutation = useMutation({
    mutationFn: createProject,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["projects"] });
      setShowForm(false);
    },
  });

  // v23.5 — DSGVO Art. 17: full-cascade hard delete (project +
  // every plan + every LV + the entire building tree). Backend
  // already returns 204 with the cascade configured at the
  // SQLAlchemy relationship layer; we just refetch the list and
  // drop a confirmation toast.
  const deleteMutation = useMutation({
    mutationFn: (projectId: string) => deleteProject(projectId),
    onSuccess: (_data, projectId) => {
      queryClient.invalidateQueries({ queryKey: ["projects"] });
      // Also drop any stale per-project caches so a back-button into
      // the deleted project lands on a clean 404 from the API
      // instead of rendering ghost data from the cache.
      queryClient.removeQueries({ queryKey: ["project", projectId] });
      setToast(
        `Projekt „${deleteTarget?.name ?? ""}" wurde gelöscht.`,
      );
      setDeleteTarget(null);
    },
    // onError leaves the modal open so the user sees the inline
    // error message and can decide whether to retry or cancel.
  });

  // Auto-clear the toast.
  useEffect(() => {
    if (toast === null) return;
    const t = setTimeout(() => setToast(null), 3000);
    return () => clearTimeout(t);
  }, [toast]);

  // ProjectDetailPage navigates here with ``?geloeschtes-projekt=<name>``
  // after a successful delete from there. Read it once on mount, fire
  // the toast, and immediately strip the param from the URL so a
  // refresh doesn't re-show the toast.
  const [searchParams, setSearchParams] = useSearchParams();
  useEffect(() => {
    const deleted = searchParams.get("geloeschtes-projekt");
    if (deleted) {
      setToast(`Projekt „${deleted}" wurde gelöscht.`);
      const next = new URLSearchParams(searchParams);
      next.delete("geloeschtes-projekt");
      setSearchParams(next, { replace: true });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const statusCounts = {
    draft: projects.filter((p) => p.status === "draft").length,
    in_progress: projects.filter((p) => p.status === "in_progress").length,
    completed: projects.filter((p) => p.status === "completed").length,
  };

  const recentProjects = [...projects]
    .sort((a, b) => new Date(b.updated_at).getTime() - new Date(a.updated_at).getTime())
    .slice(0, 5);

  return (
    <div className="p-6">
      {/* Header */}
      <div className="mb-6">
        <h1 className="text-2xl font-bold flex items-center gap-2">
          <LayoutDashboard className="h-6 w-6 text-primary" />
          Dashboard
        </h1>
        <p className="text-sm text-muted-foreground mt-1">
          Willkommen bei BauLV — Ihre Projektübersicht
        </p>
      </div>

      {/* Stats cards */}
      <div className="mb-8 grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <div className="rounded-lg border bg-card p-4">
          <p className="text-sm text-muted-foreground">Projekte gesamt</p>
          <p className="mt-1 text-3xl font-bold">{projects.length}</p>
        </div>
        <div className="rounded-lg border bg-card p-4">
          <p className="text-sm text-muted-foreground">Entwürfe</p>
          <p className="mt-1 text-3xl font-bold text-gray-600">{statusCounts.draft}</p>
        </div>
        <div className="rounded-lg border bg-card p-4">
          <p className="text-sm text-muted-foreground">In Bearbeitung</p>
          <p className="mt-1 text-3xl font-bold text-blue-600">{statusCounts.in_progress}</p>
        </div>
        <div className="rounded-lg border bg-card p-4">
          <p className="text-sm text-muted-foreground">Abgeschlossen</p>
          <p className="mt-1 text-3xl font-bold text-green-600">{statusCounts.completed}</p>
        </div>
      </div>

      {/* Quick actions */}
      <div className="mb-8 grid gap-4 sm:grid-cols-3">
        <button
          onClick={() => setShowForm(true)}
          className="flex items-center gap-3 rounded-lg border bg-card p-4 text-left transition-colors hover:border-primary/30 hover:shadow-sm"
        >
          <div className="rounded-lg bg-primary/10 p-2">
            <Plus className="h-5 w-5 text-primary" />
          </div>
          <div>
            <p className="font-medium">Neues Projekt</p>
            <p className="text-xs text-muted-foreground">Projekt anlegen</p>
          </div>
        </button>
        {recentProjects.length > 0 && (
          <Link
            to={`/app/projects/${recentProjects[0].id}`}
            className="flex items-center gap-3 rounded-lg border bg-card p-4 text-left transition-colors hover:border-primary/30 hover:shadow-sm"
          >
            <div className="rounded-lg bg-blue-100 p-2">
              <FileText className="h-5 w-5 text-blue-600" />
            </div>
            <div>
              <p className="font-medium truncate">{recentProjects[0].name}</p>
              <p className="text-xs text-muted-foreground">Zuletzt bearbeitet</p>
            </div>
          </Link>
        )}
      </div>

      {/* New Project Form */}
      {showForm && (
        <NewProjectForm
          onSubmit={(data) => createMutation.mutate(data)}
          onCancel={() => setShowForm(false)}
          isLoading={createMutation.isPending}
        />
      )}

      {/* Recent activity / Project list */}
      <div className="mb-4 flex items-center justify-between">
        <h2 className="text-lg font-semibold">
          {projects.length > 0 ? "Letzte Aktivität" : "Projekte"}
        </h2>
        {projects.length > 5 && (
          <span className="text-sm text-muted-foreground">
            Zeige {recentProjects.length} von {projects.length}
          </span>
        )}
      </div>

      {isLoading ? (
        <div className="text-center text-muted-foreground py-12">Lade Projekte...</div>
      ) : projects.length === 0 ? (
        <div className="text-center py-12">
          <FolderOpen className="mx-auto h-12 w-12 text-muted-foreground/50" />
          <h3 className="mt-4 text-lg font-medium">Keine Projekte</h3>
          <p className="mt-1 text-sm text-muted-foreground">
            Erstellen Sie Ihr erstes Projekt, um loszulegen.
          </p>
        </div>
      ) : (
        <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-3">
          {recentProjects.map((project) => (
            <ProjectCard
              key={project.id}
              project={project}
              onRequestDelete={() => setDeleteTarget(project)}
            />
          ))}
        </div>
      )}

      {/* v23.5 — type-the-name delete confirmation. Single instance
          regardless of which card was clicked; the target is
          tracked by ``deleteTarget`` state. */}
      {deleteTarget && (
        <DeleteConfirmModal
          entityLabel="Projekt"
          entityName={deleteTarget.name}
          cascadeItems={[
            "Alle Leistungsverzeichnisse (LVs)",
            "Alle hochgeladenen Pläne",
            "Die gesamte Gebäudestruktur (Räume, Stockwerke, Einheiten)",
          ]}
          onCancel={() => {
            // Don't reset while the request is in flight — the user
            // would see the modal disappear before the toast lands,
            // which is jarring.
            if (!deleteMutation.isPending) {
              setDeleteTarget(null);
              deleteMutation.reset();
            }
          }}
          onConfirm={() => deleteMutation.mutate(deleteTarget.id)}
          isLoading={deleteMutation.isPending}
          errorMessage={
            deleteMutation.isError
              ? "Löschen fehlgeschlagen. Bitte versuchen Sie es erneut."
              : null
          }
        />
      )}

      {/* v23.5 — success toast for delete (and future post-mutation
          confirmations). Bottom-right, dismissible. */}
      {toast && (
        <div
          role="status"
          aria-live="polite"
          className="fixed bottom-6 right-6 z-50 flex items-center gap-2 rounded-md border border-green-200 bg-green-50 px-4 py-2.5 text-sm text-green-800 shadow-lg"
        >
          <Check className="h-4 w-4" />
          <span>{toast}</span>
          <button
            type="button"
            onClick={() => setToast(null)}
            aria-label="Schließen"
            className="ml-1 text-green-700/70 hover:text-green-900"
          >
            <X className="h-4 w-4" />
          </button>
        </div>
      )}
    </div>
  );
}

function ProjectCard({
  project,
  onRequestDelete,
}: {
  project: Project;
  onRequestDelete: () => void;
}) {
  const status = STATUS_LABELS[project.status] ?? STATUS_LABELS.draft;
  const [menuOpen, setMenuOpen] = useState(false);
  const menuRef = useRef<HTMLDivElement | null>(null);

  // Close menu on outside click + Escape. Mounting/unmounting the
  // listeners only while the menu is open keeps the document-level
  // overhead at zero in the steady state.
  useEffect(() => {
    if (!menuOpen) return;
    const handleClick = (e: MouseEvent) => {
      if (menuRef.current && !menuRef.current.contains(e.target as Node)) {
        setMenuOpen(false);
      }
    };
    const handleKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setMenuOpen(false);
    };
    document.addEventListener("mousedown", handleClick);
    document.addEventListener("keydown", handleKey);
    return () => {
      document.removeEventListener("mousedown", handleClick);
      document.removeEventListener("keydown", handleKey);
    };
  }, [menuOpen]);

  // The card uses ``Link`` so right-click → "open in new tab" still
  // works. The 3-dot menu is layered on top with ``stopPropagation``
  // so clicking it doesn't navigate. We deliberately do NOT pull the
  // header into the link click area separately — keeping the whole
  // card a navigation surface (minus the menu) matches the prior
  // UX; the new menu is the only escape hatch.
  return (
    <div className="relative">
      <Link
        to={`/app/projects/${project.id}`}
        className="group block rounded-lg border bg-card p-5 shadow-sm transition-all hover:shadow-md hover:border-primary/30"
      >
        <div className="flex items-start justify-between gap-2">
          <h3 className="font-semibold text-card-foreground group-hover:text-primary">
            {project.name}
          </h3>
          <div className="flex shrink-0 items-center gap-2">
            <span
              className={`rounded-full px-2.5 py-0.5 text-xs font-medium ${status.color}`}
            >
              {status.label}
            </span>
            {/* Spacer so the menu button (overlaid below) doesn't
                visually crash into the status badge. */}
            <span className="w-7" aria-hidden />
          </div>
        </div>

        {project.address && (
          <div className="mt-2 flex items-center gap-1.5 text-sm text-muted-foreground">
            <MapPin className="h-3.5 w-3.5" />
            {project.address}
          </div>
        )}

        {project.client_name && (
          <p className="mt-1 text-sm text-muted-foreground">
            Bauwerber: {project.client_name}
          </p>
        )}

        <div className="mt-4 flex items-center justify-between">
          <div className="flex items-center gap-1 text-xs text-muted-foreground">
            <Clock className="h-3 w-3" />
            {new Date(project.updated_at).toLocaleDateString("de-AT")}
          </div>
          <ChevronRight className="h-4 w-4 text-muted-foreground group-hover:text-primary" />
        </div>
      </Link>

      {/* 3-dot menu — absolutely positioned over the link. The
          menu button + dropdown sit in the card's relative parent
          so they don't navigate when clicked. */}
      <div
        ref={menuRef}
        className="absolute right-3 top-3"
        // Defensive: any click that bubbles from inside this menu
        // must NOT propagate to the Link wrapper.
        onClick={(e) => e.stopPropagation()}
      >
        <button
          type="button"
          onClick={(e) => {
            e.preventDefault();
            e.stopPropagation();
            setMenuOpen((open) => !open);
          }}
          aria-label="Aktionen für dieses Projekt"
          aria-haspopup="menu"
          aria-expanded={menuOpen}
          className="rounded p-1 text-muted-foreground hover:bg-muted hover:text-foreground"
        >
          <MoreVertical className="h-4 w-4" />
        </button>
        {menuOpen && (
          <div
            role="menu"
            className="absolute right-0 top-full z-10 mt-1 min-w-[10rem] rounded-md border bg-card py-1 shadow-lg"
          >
            <button
              type="button"
              role="menuitem"
              onClick={(e) => {
                e.preventDefault();
                e.stopPropagation();
                setMenuOpen(false);
                onRequestDelete();
              }}
              className="flex w-full items-center gap-2 px-3 py-1.5 text-left text-sm text-red-600 hover:bg-red-50"
            >
              <Trash2 className="h-3.5 w-3.5" />
              Projekt löschen
            </button>
          </div>
        )}
      </div>
    </div>
  );
}

function NewProjectForm({
  onSubmit,
  onCancel,
  isLoading,
}: {
  onSubmit: (data: ProjectCreate) => void;
  onCancel: () => void;
  isLoading: boolean;
}) {
  const [formData, setFormData] = useState<ProjectCreate>({
    name: "",
    address: "",
    client_name: "",
    project_number: "",
  });

  return (
    <div className="mb-6 rounded-lg border bg-card p-5">
      <h3 className="mb-4 font-semibold">Neues Projekt anlegen</h3>
      <form
        onSubmit={(e) => {
          e.preventDefault();
          onSubmit(formData);
        }}
        className="grid gap-4 sm:grid-cols-2"
      >
        <div>
          <label className="mb-1 block text-sm font-medium">Projektname *</label>
          <input
            required
            value={formData.name}
            onChange={(e) => setFormData({ ...formData, name: e.target.value })}
            className="w-full rounded-md border px-3 py-2 text-sm"
            placeholder="z.B. Wohnhaus Linzer Straße 42"
          />
        </div>
        <div>
          <label className="mb-1 block text-sm font-medium">Adresse</label>
          <input
            value={formData.address ?? ""}
            onChange={(e) => setFormData({ ...formData, address: e.target.value })}
            className="w-full rounded-md border px-3 py-2 text-sm"
            placeholder="Straße, PLZ Ort"
          />
        </div>
        <div>
          <label className="mb-1 block text-sm font-medium">Bauwerber</label>
          <input
            value={formData.client_name ?? ""}
            onChange={(e) => setFormData({ ...formData, client_name: e.target.value })}
            className="w-full rounded-md border px-3 py-2 text-sm"
          />
        </div>
        <div>
          <label className="mb-1 block text-sm font-medium">Projektnummer</label>
          <input
            value={formData.project_number ?? ""}
            onChange={(e) => setFormData({ ...formData, project_number: e.target.value })}
            className="w-full rounded-md border px-3 py-2 text-sm"
          />
        </div>
        <div className="flex gap-2 sm:col-span-2">
          <button
            type="submit"
            disabled={isLoading}
            className="rounded-md bg-primary px-4 py-2 text-sm font-medium text-primary-foreground hover:bg-primary/90 disabled:opacity-50"
          >
            {isLoading ? "Erstelle..." : "Projekt erstellen"}
          </button>
          <button
            type="button"
            onClick={onCancel}
            className="rounded-md border px-4 py-2 text-sm font-medium hover:bg-accent"
          >
            Abbrechen
          </button>
        </div>
      </form>
    </div>
  );
}
