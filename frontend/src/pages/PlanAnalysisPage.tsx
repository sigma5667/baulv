import { useState, useCallback } from "react";
import { useParams, Link } from "react-router-dom";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import {
  Upload,
  FileText,
  Loader2,
  CheckCircle,
  AlertCircle,
  ArrowLeft,
  Search,
} from "lucide-react";
import { fetchPlans, uploadPlan, analyzePlan } from "../api/plans";
import { fetchProjectRooms, updateRoom, deleteRoom } from "../api/rooms";
import type { Plan } from "../types/plan";
import type { Room } from "../types/room";

export function PlanAnalysisPage() {
  const { id: projectId } = useParams<{ id: string }>();
  const queryClient = useQueryClient();

  const { data: plans = [] } = useQuery({
    queryKey: ["plans", projectId],
    queryFn: () => fetchPlans(projectId!),
    enabled: !!projectId,
  });

  const { data: rooms = [] } = useQuery({
    queryKey: ["rooms", projectId],
    queryFn: () => fetchProjectRooms(projectId!),
    enabled: !!projectId,
  });

  const uploadMutation = useMutation({
    mutationFn: (file: File) => uploadPlan(projectId!, file),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["plans", projectId] }),
  });

  const analyzeMutation = useMutation({
    mutationFn: (planId: string) => analyzePlan(planId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["plans", projectId] });
      queryClient.invalidateQueries({ queryKey: ["rooms", projectId] });
    },
  });

  const handleDrop = useCallback(
    (e: React.DragEvent) => {
      e.preventDefault();
      const files = Array.from(e.dataTransfer.files).filter(
        (f) => f.type === "application/pdf"
      );
      files.forEach((file) => uploadMutation.mutate(file));
    },
    [uploadMutation]
  );

  const handleFileSelect = (e: React.ChangeEvent<HTMLInputElement>) => {
    const files = Array.from(e.target.files ?? []);
    files.forEach((file) => uploadMutation.mutate(file));
  };

  return (
    <div className="p-6">
      <Link
        to={`/app/projects/${projectId}`}
        className="mb-4 inline-flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground"
      >
        <ArrowLeft className="h-4 w-4" />
        Zum Projekt
      </Link>

      <h1 className="mb-6 text-2xl font-bold">Plananalyse</h1>

      {/* Upload area */}
      <div
        onDragOver={(e) => e.preventDefault()}
        onDrop={handleDrop}
        className="mb-6 rounded-lg border-2 border-dashed border-border p-8 text-center transition-colors hover:border-primary/50"
      >
        <Upload className="mx-auto h-10 w-10 text-muted-foreground/50" />
        <p className="mt-2 text-sm text-muted-foreground">
          PDF-Baupläne hierher ziehen oder{" "}
          <label className="cursor-pointer text-primary hover:underline">
            Datei auswählen
            <input
              type="file"
              accept=".pdf"
              multiple
              onChange={handleFileSelect}
              className="hidden"
            />
          </label>
        </p>
        {uploadMutation.isPending && (
          <div className="mt-2 flex items-center justify-center gap-2 text-sm text-primary">
            <Loader2 className="h-4 w-4 animate-spin" />
            Wird hochgeladen...
          </div>
        )}
      </div>

      {/* Plans list */}
      {plans.length > 0 && (
        <div className="mb-8">
          <h2 className="mb-3 text-lg font-semibold">Hochgeladene Pläne</h2>
          <div className="space-y-2">
            {plans.map((plan) => (
              <PlanRow
                key={plan.id}
                plan={plan}
                onAnalyze={() => analyzeMutation.mutate(plan.id)}
                isAnalyzing={
                  analyzeMutation.isPending &&
                  analyzeMutation.variables === plan.id
                }
              />
            ))}
          </div>
        </div>
      )}

      {/* Extracted rooms table */}
      {rooms.length > 0 && (
        <div>
          <h2 className="mb-3 text-lg font-semibold">
            Extrahierte Räume ({rooms.length})
          </h2>
          <RoomTable rooms={rooms} projectId={projectId!} />
        </div>
      )}
    </div>
  );
}

function PlanRow({
  plan,
  onAnalyze,
  isAnalyzing,
}: {
  plan: Plan;
  onAnalyze: () => void;
  isAnalyzing: boolean;
}) {
  const statusIcon = {
    pending: <FileText className="h-4 w-4 text-muted-foreground" />,
    processing: <Loader2 className="h-4 w-4 animate-spin text-primary" />,
    completed: <CheckCircle className="h-4 w-4 text-green-600" />,
    failed: <AlertCircle className="h-4 w-4 text-destructive" />,
  }[plan.analysis_status] ?? <FileText className="h-4 w-4" />;

  return (
    <div className="flex items-center justify-between rounded-lg border bg-card px-4 py-3">
      <div className="flex items-center gap-3">
        {statusIcon}
        <div>
          <p className="text-sm font-medium">{plan.filename}</p>
          <p className="text-xs text-muted-foreground">
            {plan.plan_type} | {plan.page_count ?? "?"} Seiten
          </p>
        </div>
      </div>
      {plan.analysis_status === "pending" && (
        <button
          onClick={onAnalyze}
          disabled={isAnalyzing}
          className="flex items-center gap-1.5 rounded-md bg-primary px-3 py-1.5 text-xs font-medium text-primary-foreground hover:bg-primary/90 disabled:opacity-50"
        >
          {isAnalyzing ? (
            <Loader2 className="h-3 w-3 animate-spin" />
          ) : (
            <Search className="h-3 w-3" />
          )}
          AI-Analyse starten
        </button>
      )}
    </div>
  );
}

function RoomTable({ rooms, projectId }: { rooms: Room[]; projectId: string }) {
  const queryClient = useQueryClient();
  const [_editingId, setEditingId] = useState<string | null>(null);

  // eslint-disable-next-line @typescript-eslint/no-unused-vars
  const _updateMutation = useMutation({
    mutationFn: ({ id, updates }: { id: string; updates: Partial<Room> }) =>
      updateRoom(id, updates),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["rooms", projectId] });
      setEditingId(null);
    },
  });

  const deleteMutation = useMutation({
    mutationFn: deleteRoom,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["rooms", projectId] }),
  });

  return (
    <div className="overflow-x-auto rounded-lg border">
      <table className="w-full text-sm">
        <thead className="bg-muted/50">
          <tr>
            <th className="px-4 py-2 text-left font-medium">Raum</th>
            <th className="px-4 py-2 text-left font-medium">Typ</th>
            <th className="px-4 py-2 text-right font-medium">Fläche m²</th>
            <th className="px-4 py-2 text-right font-medium">Umfang m</th>
            <th className="px-4 py-2 text-right font-medium">RH m</th>
            <th className="px-4 py-2 text-left font-medium">Boden</th>
            <th className="px-4 py-2 text-center font-medium">Nassraum</th>
            <th className="px-4 py-2 text-center font-medium">Quelle</th>
            <th className="px-4 py-2 text-right font-medium">Konfidenz</th>
            <th className="px-4 py-2"></th>
          </tr>
        </thead>
        <tbody className="divide-y">
          {rooms.map((room) => (
            <tr key={room.id} className="hover:bg-muted/30">
              <td className="px-4 py-2 font-medium">{room.name}</td>
              <td className="px-4 py-2 text-muted-foreground">{room.room_type ?? "-"}</td>
              <td className="px-4 py-2 text-right font-mono">
                {room.area_m2?.toFixed(2) ?? "-"}
              </td>
              <td className="px-4 py-2 text-right font-mono">
                {room.perimeter_m?.toFixed(2) ?? "-"}
              </td>
              <td className="px-4 py-2 text-right font-mono">
                {room.height_m?.toFixed(2) ?? "-"}
              </td>
              <td className="px-4 py-2 text-muted-foreground">{room.floor_type ?? "-"}</td>
              <td className="px-4 py-2 text-center">
                {room.is_wet_room ? (
                  <span className="text-blue-600">Ja</span>
                ) : (
                  <span className="text-muted-foreground">-</span>
                )}
              </td>
              <td className="px-4 py-2 text-center">
                <span
                  className={`rounded-full px-2 py-0.5 text-xs ${
                    room.source === "ai"
                      ? "bg-purple-100 text-purple-700"
                      : "bg-gray-100 text-gray-700"
                  }`}
                >
                  {room.source === "ai" ? "AI" : "Manuell"}
                </span>
              </td>
              <td className="px-4 py-2 text-right font-mono text-xs">
                {room.ai_confidence ? `${(room.ai_confidence * 100).toFixed(0)}%` : "-"}
              </td>
              <td className="px-4 py-2 text-right">
                <button
                  onClick={() => deleteMutation.mutate(room.id)}
                  className="text-xs text-destructive hover:underline"
                >
                  Löschen
                </button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
