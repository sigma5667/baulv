import { useParams, Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { FileText, Map, Calculator, ArrowLeft } from "lucide-react";
import { fetchProject } from "../api/projects";
import { fetchPlans } from "../api/plans";
import { fetchProjectRooms } from "../api/rooms";
import { fetchProjectLVs } from "../api/lv";

export function ProjectDetailPage() {
  const { id } = useParams<{ id: string }>();

  const { data: project, isLoading } = useQuery({
    queryKey: ["project", id],
    queryFn: () => fetchProject(id!),
    enabled: !!id,
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
      <div className="mb-8">
        <h1 className="text-2xl font-bold">{project.name}</h1>
        <div className="mt-1 flex flex-wrap gap-4 text-sm text-muted-foreground">
          {project.address && <span>{project.address}</span>}
          {project.client_name && <span>Bauwerber: {project.client_name}</span>}
          {project.project_number && <span>Nr. {project.project_number}</span>}
        </div>
      </div>

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

        {/* Rooms summary */}
        <div className="rounded-lg border bg-card p-5">
          <div className="flex items-center gap-3">
            <div className="rounded-lg bg-purple-50 p-2.5">
              <FileText className="h-5 w-5 text-purple-600" />
            </div>
            <div>
              <h3 className="font-semibold">Gebäudestruktur</h3>
              <p className="text-sm text-muted-foreground">{rooms.length} Räume</p>
            </div>
          </div>
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
        </div>
      </div>
    </div>
  );
}
