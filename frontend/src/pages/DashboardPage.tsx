import { useState } from "react";
import { Link } from "react-router-dom";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import {
  Plus,
  FolderOpen,
  Clock,
  MapPin,
  ChevronRight,
  BookOpen,
  LayoutDashboard,
  FileText,
} from "lucide-react";
import { fetchProjects, createProject } from "../api/projects";
import type { Project, ProjectCreate } from "../types/project";

const STATUS_LABELS: Record<string, { label: string; color: string }> = {
  draft: { label: "Entwurf", color: "bg-gray-100 text-gray-700" },
  in_progress: { label: "In Bearbeitung", color: "bg-blue-100 text-blue-700" },
  completed: { label: "Abgeschlossen", color: "bg-green-100 text-green-700" },
};

export function DashboardPage() {
  const [showForm, setShowForm] = useState(false);
  const queryClient = useQueryClient();

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
        <Link
          to="/app/settings/onorm"
          className="flex items-center gap-3 rounded-lg border bg-card p-4 text-left transition-colors hover:border-primary/30 hover:shadow-sm"
        >
          <div className="rounded-lg bg-orange-100 p-2">
            <BookOpen className="h-5 w-5 text-orange-600" />
          </div>
          <div>
            <p className="font-medium">ÖNORM-Bibliothek</p>
            <p className="text-xs text-muted-foreground">Normen verwalten</p>
          </div>
        </Link>
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
            <ProjectCard key={project.id} project={project} />
          ))}
        </div>
      )}
    </div>
  );
}

function ProjectCard({ project }: { project: Project }) {
  const status = STATUS_LABELS[project.status] ?? STATUS_LABELS.draft;

  return (
    <Link
      to={`/app/projects/${project.id}`}
      className="group block rounded-lg border bg-card p-5 shadow-sm transition-all hover:shadow-md hover:border-primary/30"
    >
      <div className="flex items-start justify-between">
        <h3 className="font-semibold text-card-foreground group-hover:text-primary">
          {project.name}
        </h3>
        <span className={`rounded-full px-2.5 py-0.5 text-xs font-medium ${status.color}`}>
          {status.label}
        </span>
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
