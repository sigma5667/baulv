import { useState } from "react";
import { useParams, Link, useNavigate } from "react-router-dom";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import {
  FileText,
  Map,
  Calculator,
  ArrowLeft,
  Trash2,
} from "lucide-react";
import { fetchProject, deleteProject } from "../api/projects";
import { fetchPlans } from "../api/plans";
import { fetchProjectRooms } from "../api/rooms";
import { fetchProjectLVs } from "../api/lv";
import { DeleteConfirmModal } from "../components/DeleteConfirmModal";
import { useToast } from "../components/Toast";

export function ProjectDetailPage() {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const toast = useToast();
  const [showDeleteModal, setShowDeleteModal] = useState(false);

  const { data: project, isLoading } = useQuery({
    queryKey: ["project", id],
    queryFn: () => fetchProject(id!),
    enabled: !!id,
  });

  // v23.5 → v23.6 — DSGVO Art. 17 cascade delete. After success the
  // user has no business being on this page anymore (the project is
  // gone), so we fire the success toast (it lives in the global
  // ``ToastProvider`` so it survives the navigation) and bounce
  // back to ``/app``. Pre-v23.6 we used a ``?geloeschtes-projekt=``
  // URL parameter to pass the success message across routes — the
  // toast context makes that hack unnecessary.
  const deleteMutation = useMutation({
    mutationFn: () => deleteProject(id!),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["projects"] });
      queryClient.removeQueries({ queryKey: ["project", id] });
      toast.success(
        `Projekt „${project?.name ?? ""}" wurde gelöscht.`,
      );
      navigate("/app", { replace: true });
    },
    onError: () => {
      toast.error("Löschen fehlgeschlagen. Bitte versuchen Sie es erneut.");
    },
  });

  const { data: plans = [] } = useQuery({
    queryKey: ["plans", id],
    queryFn: () => fetchPlans(id!),
    enabled: !!id,
  });

  const { data: rooms = [] } = useQuery({
    queryKey: ["rooms", id],
    queryFn: () => fetchProjectRooms(id!),
    enabled: !!id,
  });

  const { data: lvs = [] } = useQuery({
    queryKey: ["lvs", id],
    queryFn: () => fetchProjectLVs(id!),
    enabled: !!id,
  });

  if (isLoading) {
    return <div className="p-6 text-muted-foreground">Lade Projekt...</div>;
  }

  if (!project) {
    return <div className="p-6 text-destructive">Projekt nicht gefunden.</div>;
  }

  return (
    <div className="p-6">
      {/* Back link */}
      <Link
        to="/app"
        className="mb-4 inline-flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground"
      >
        <ArrowLeft className="h-4 w-4" />
        Zur Projektliste
      </Link>

      {/* Project header */}
      <div className="mb-8 flex items-start justify-between gap-4">
        <div className="flex-1">
          <h1 className="text-2xl font-bold">{project.name}</h1>
          <div className="mt-1 flex flex-wrap gap-4 text-sm text-muted-foreground">
            {project.address && <span>{project.address}</span>}
            {project.client_name && (
              <span>Bauwerber: {project.client_name}</span>
            )}
            {project.project_number && <span>Nr. {project.project_number}</span>}
          </div>
        </div>
        {/* v23.5 — destructive action in the header. Visually
            differentiated (red, outlined → solid on hover) so a
            casual click is unlikely; the modal then enforces the
            type-the-name confirmation. */}
        <button
          type="button"
          onClick={() => setShowDeleteModal(true)}
          className="flex shrink-0 items-center gap-1.5 rounded-md border border-red-200 px-3 py-1.5 text-sm font-medium text-red-600 hover:border-red-600 hover:bg-red-600 hover:text-white"
        >
          <Trash2 className="h-3.5 w-3.5" />
          Projekt löschen
        </button>
      </div>

      {showDeleteModal && (
        <DeleteConfirmModal
          entityLabel="Projekt"
          entityName={project.name}
          cascadeItems={[
            "Alle Leistungsverzeichnisse (LVs)",
            "Alle hochgeladenen Pläne",
            "Die gesamte Gebäudestruktur (Räume, Stockwerke, Einheiten)",
          ]}
          onCancel={() => {
            if (!deleteMutation.isPending) {
              setShowDeleteModal(false);
              deleteMutation.reset();
            }
          }}
          onConfirm={() => deleteMutation.mutate()}
          isLoading={deleteMutation.isPending}
          errorMessage={
            deleteMutation.isError
              ? "Löschen fehlgeschlagen. Bitte versuchen Sie es erneut."
              : null
          }
        />
      )}

      {/* Action cards */}
      <div className="grid gap-4 md:grid-cols-3">
        {/* Plans */}
        <Link
          to={`/app/projects/${id}/plans`}
          className="group rounded-lg border bg-card p-5 transition-all hover:shadow-md hover:border-primary/30"
        >
          <div className="flex items-center gap-3">
            <div className="rounded-lg bg-blue-50 p-2.5">
              <Map className="h-5 w-5 text-blue-600" />
            </div>
            <div>
              <h3 className="font-semibold group-hover:text-primary">Plananalyse</h3>
              <p className="text-sm text-muted-foreground">
                {plans.length} Plan{plans.length !== 1 ? "e" : ""} hochgeladen
              </p>
            </div>
          </div>
          <p className="mt-3 text-sm text-muted-foreground">
            Baupläne hochladen, AI-Analyse starten, Räume extrahieren
          </p>
          <div className="mt-2 text-sm text-muted-foreground">
            {rooms.length} Raum/Räume extrahiert
          </div>
        </Link>

        {/* LV */}
        <Link
          to={`/app/projects/${id}/lv`}
          className="group rounded-lg border bg-card p-5 transition-all hover:shadow-md hover:border-primary/30"
        >
          <div className="flex items-center gap-3">
            <div className="rounded-lg bg-green-50 p-2.5">
              <Calculator className="h-5 w-5 text-green-600" />
            </div>
            <div>
              <h3 className="font-semibold group-hover:text-primary">Leistungsverzeichnisse</h3>
              <p className="text-sm text-muted-foreground">
                {lvs.length} LV{lvs.length !== 1 ? "s" : ""} erstellt
              </p>
            </div>
          </div>
          <p className="mt-3 text-sm text-muted-foreground">
            LV erstellen, Mengen berechnen, Positionen generieren
          </p>
        </Link>

        {/* Structure (Gebäude → Stockwerk → Einheit → Raum) */}
        <Link
          to={`/app/projects/${id}/structure`}
          className="group rounded-lg border bg-card p-5 transition-all hover:shadow-md hover:border-primary/30"
        >
          <div className="flex items-center gap-3">
            <div className="rounded-lg bg-purple-50 p-2.5">
              <FileText className="h-5 w-5 text-purple-600" />
            </div>
            <div>
              <h3 className="font-semibold group-hover:text-primary">
                Gebäudestruktur
              </h3>
              <p className="text-sm text-muted-foreground">
                {rooms.length} Raum/Räume
              </p>
            </div>
          </div>
          <p className="mt-3 text-sm text-muted-foreground">
            Gebäude, Stockwerke, Einheiten und Räume manuell anlegen
          </p>
          {rooms.length > 0 && (
            <div className="mt-3 space-y-1">
              {rooms.slice(0, 5).map((room) => (
                <div key={room.id} className="flex justify-between text-sm">
                  <span className="text-muted-foreground">{room.name}</span>
                  <span className="font-mono text-xs">
                    {room.area_m2?.toFixed(1)} m²
                  </span>
                </div>
              ))}
              {rooms.length > 5 && (
                <p className="text-xs text-muted-foreground">
                  +{rooms.length - 5} weitere Räume
                </p>
              )}
            </div>
          )}
        </Link>
      </div>
    </div>
  );
}
