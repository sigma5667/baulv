/**
 * StructurePage — manual entry path for the Gebäude → Stockwerk →
 * Einheit → Raum hierarchy.
 *
 * Why this page exists
 * --------------------
 * The Plananalyse flow extracts rooms from a PDF; the Wandberechnung
 * feature sums their wall areas; the LV pulls m² values from that
 * calculation. That whole chain is useless to a tester who doesn't
 * have a plan PDF to upload. This page gives them a click-through
 * way to build the same data structure by hand.
 *
 * Manually-entered rooms are indistinguishable from AI-extracted rooms
 * once saved — they flow into Wandberechnung and the "Wandflächen"
 * sync in the LV editor with zero extra plumbing, because everything
 * downstream reads the ``rooms`` table and doesn't care about
 * ``source``.
 *
 * Layout
 * ------
 * Single-page collapsible tree. Each level has an "add child" button
 * and an edit/delete pair. The Room card exposes its openings inline
 * so the user doesn't have to drill into a separate dialog to add a
 * window or a door. Deletes use a confirm dialog because the cascade
 * is ruthless (delete a Gebäude and its entire subtree goes with it).
 *
 * Data flow
 * ---------
 * One ``GET /projects/{id}/structure`` feeds the whole tree. All
 * mutations invalidate that single query key so the tree always
 * redraws from server truth after an edit — this is simpler and safer
 * than trying to splice mutation responses into the cached tree
 * ourselves. At this entity scale (a single project's rooms is O(10)
 * to O(100)) the refetch cost is negligible.
 */

import { useState } from "react";
import { useParams, Link } from "react-router-dom";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import {
  AlertTriangle,
  ArrowLeft,
  Building2,
  ChevronDown,
  ChevronRight,
  DoorOpen,
  Edit2,
  Home,
  Info,
  Layers,
  Plus,
  Sparkles,
  Trash2,
  X,
} from "lucide-react";
import { InlineNumericEdit } from "../components/room/InlineNumericEdit";
import { fetchProject } from "../api/projects";
import {
  fetchProjectStructure,
  createBuilding,
  updateBuilding,
  deleteBuilding,
  createFloor,
  updateFloor,
  deleteFloor,
  createUnit,
  updateUnit,
  deleteUnit,
  quickAddSingleFamily,
} from "../api/structure";
import {
  createRoom,
  updateRoom,
  deleteRoom,
  createOpening,
  updateOpening,
  deleteOpening,
} from "../api/rooms";
import { normalizeError } from "../lib/errors";
import type {
  BuildingWithChildren,
  FloorWithUnits,
  UnitWithRooms,
} from "../types/project";
import type { Opening, Room } from "../types/room";

// --- Constants ---------------------------------------------------------------

// Room type values are kept as short English keys on the wire (they
// also live in ``room_type`` in the backend schema as an untyped
// string), but the UI renders German labels. Keep the list short —
// this is a categorization hint, not a rigid taxonomy. ``stairwell``
// is the only value the wall-calculator keys off (1.5× factor), but
// we expose the others so the user gets a meaningful dropdown rather
// than a free-text field.
const ROOM_TYPE_OPTIONS: { value: string; label: string }[] = [
  { value: "normal", label: "Normal" },
  { value: "stairwell", label: "Treppenhaus" },
  { value: "bathroom", label: "Bad / WC" },
  { value: "kitchen", label: "Küche" },
  { value: "cellar", label: "Keller" },
];

const OPENING_TYPE_OPTIONS: { value: string; label: string }[] = [
  { value: "fenster", label: "Fenster" },
  { value: "tuer", label: "Tür" },
];

// Default floor height used when the user creates a room without a
// floor-level height set. Kept in sync with the calculator's own
// fallback in ``app/services/wall_calculator.py``.
const DEFAULT_FLOOR_HEIGHT_M = 2.5;

// Error-banner state for the page. ``unavailable`` is reserved for
// "feature requires paid plan" responses (403) — not used yet here,
// but keeps the shape consistent with other pages in case the backend
// gates structure editing behind a tier in the future.
type BannerState =
  | { kind: "idle" }
  | { kind: "error"; message: string };

// German decimal formatter — Austrian convention is comma-separated.
function fmtNumber(value: number | null | undefined, digits = 2): string {
  if (value === null || value === undefined) return "—";
  return value.toFixed(digits).replace(".", ",");
}

export function StructurePage() {
  const { id } = useParams<{ id: string }>();
  const queryClient = useQueryClient();
  const [banner, setBanner] = useState<BannerState>({ kind: "idle" });

  const { data: project } = useQuery({
    queryKey: ["project", id],
    queryFn: () => fetchProject(id!),
    enabled: !!id,
  });

  const {
    data: structure,
    isLoading,
    isError,
    error,
  } = useQuery({
    queryKey: ["structure", id],
    queryFn: () => fetchProjectStructure(id!),
    enabled: !!id,
  });

  // Any mutation invalidates the whole tree. Also invalidates the
  // flat ``rooms`` and ``plans`` query keys so the Plananalyse page
  // and ProjectDetailPage pick up new rooms without a manual refresh.
  const invalidateAll = () => {
    queryClient.invalidateQueries({ queryKey: ["structure", id] });
    queryClient.invalidateQueries({ queryKey: ["rooms", id] });
  };

  const reportError = (err: unknown) => {
    const norm = normalizeError(err);
    setBanner({ kind: "error", message: norm.message });
  };

  // --- Mutations -------------------------------------------------------------

  const createBuildingMut = useMutation({
    mutationFn: (data: { name: string; sort_order?: number }) =>
      createBuilding(id!, data),
    onSuccess: invalidateAll,
    onError: reportError,
  });

  const updateBuildingMut = useMutation({
    mutationFn: ({
      buildingId,
      data,
    }: {
      buildingId: string;
      data: { name?: string; sort_order?: number };
    }) => updateBuilding(buildingId, data),
    onSuccess: invalidateAll,
    onError: reportError,
  });

  const deleteBuildingMut = useMutation({
    mutationFn: deleteBuilding,
    onSuccess: invalidateAll,
    onError: reportError,
  });

  const createFloorMut = useMutation({
    mutationFn: ({
      buildingId,
      data,
    }: {
      buildingId: string;
      data: {
        name: string;
        level_number?: number | null;
        floor_height_m?: number | null;
      };
    }) => createFloor(buildingId, data),
    onSuccess: invalidateAll,
    onError: reportError,
  });

  const updateFloorMut = useMutation({
    mutationFn: ({
      floorId,
      data,
    }: {
      floorId: string;
      data: {
        name?: string;
        level_number?: number | null;
        floor_height_m?: number | null;
      };
    }) => updateFloor(floorId, data),
    onSuccess: invalidateAll,
    onError: reportError,
  });

  const deleteFloorMut = useMutation({
    mutationFn: deleteFloor,
    onSuccess: invalidateAll,
    onError: reportError,
  });

  const createUnitMut = useMutation({
    mutationFn: ({
      floorId,
      data,
    }: {
      floorId: string;
      data: { name: string; unit_type?: string | null };
    }) => createUnit(floorId, data),
    onSuccess: invalidateAll,
    onError: reportError,
  });

  const updateUnitMut = useMutation({
    mutationFn: ({
      unitId,
      data,
    }: {
      unitId: string;
      data: { name?: string; unit_type?: string | null };
    }) => updateUnit(unitId, data),
    onSuccess: invalidateAll,
    onError: reportError,
  });

  const deleteUnitMut = useMutation({
    mutationFn: deleteUnit,
    onSuccess: invalidateAll,
    onError: reportError,
  });

  const quickAddMut = useMutation({
    mutationFn: () => quickAddSingleFamily(id!),
    onSuccess: invalidateAll,
    onError: reportError,
  });

  // --- Render ----------------------------------------------------------------

  if (!id) {
    return <div className="p-6 text-destructive">Projekt-ID fehlt.</div>;
  }

  return (
    <div className="p-6">
      <Link
        to={`/app/projects/${id}`}
        className="mb-4 inline-flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground"
      >
        <ArrowLeft className="h-4 w-4" />
        Zurück zum Projekt
      </Link>

      <div className="mb-6">
        <h1 className="flex items-center gap-2 text-2xl font-bold">
          <Building2 className="h-6 w-6 text-primary" />
          Gebäudestruktur
        </h1>
        {project && (
          <p className="mt-1 text-sm text-muted-foreground">
            {project.name}
            {project.address ? ` — ${project.address}` : ""}
          </p>
        )}
        <p className="mt-2 text-sm text-muted-foreground">
          Legen Sie Gebäude, Stockwerke, Einheiten und Räume manuell an.
          Die Daten fließen automatisch in die Wandberechnung und in den
          LV-Editor ein.
        </p>
      </div>

      {banner.kind === "error" && (
        <div
          role="alert"
          className="mb-6 flex items-start gap-3 rounded-md border border-destructive/30 bg-destructive/5 p-4"
        >
          <AlertTriangle className="mt-0.5 h-5 w-5 shrink-0 text-destructive" />
          <div className="flex-1 text-sm">
            <p className="font-medium text-destructive">Fehler</p>
            <p className="mt-0.5 text-destructive/90">{banner.message}</p>
          </div>
          <button
            type="button"
            onClick={() => setBanner({ kind: "idle" })}
            className="text-xs text-destructive hover:underline"
          >
            Schließen
          </button>
        </div>
      )}

      {isLoading && (
        <div className="text-sm text-muted-foreground">
          Lade Gebäudestruktur...
        </div>
      )}

      {isError && !isLoading && (
        <div className="rounded-md border border-destructive/30 bg-destructive/5 p-4 text-sm text-destructive">
          Fehler beim Laden: {normalizeError(error).message}
        </div>
      )}

      {structure && !isLoading && !isError && (
        <StructureTree
          buildings={structure.buildings}
          // --- top-level actions ---
          onQuickAdd={() => quickAddMut.mutate()}
          isQuickAdding={quickAddMut.isPending}
          onCreateBuilding={(data) => createBuildingMut.mutate(data)}
          isCreatingBuilding={createBuildingMut.isPending}
          // --- building-level ---
          onUpdateBuilding={(buildingId, data) =>
            updateBuildingMut.mutate({ buildingId, data })
          }
          onDeleteBuilding={(buildingId) =>
            deleteBuildingMut.mutate(buildingId)
          }
          // --- floor-level ---
          onCreateFloor={(buildingId, data) =>
            createFloorMut.mutate({ buildingId, data })
          }
          onUpdateFloor={(floorId, data) =>
            updateFloorMut.mutate({ floorId, data })
          }
          onDeleteFloor={(floorId) => deleteFloorMut.mutate(floorId)}
          // --- unit-level ---
          onCreateUnit={(floorId, data) =>
            createUnitMut.mutate({ floorId, data })
          }
          onUpdateUnit={(unitId, data) =>
            updateUnitMut.mutate({ unitId, data })
          }
          onDeleteUnit={(unitId) => deleteUnitMut.mutate(unitId)}
        />
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// StructureTree — top-level layout: quick-add + add-building + list of
// Building cards, each recursively rendering its subtree.
// ---------------------------------------------------------------------------

interface StructureTreeProps {
  buildings: BuildingWithChildren[];
  onQuickAdd: () => void;
  isQuickAdding: boolean;
  onCreateBuilding: (data: { name: string; sort_order?: number }) => void;
  isCreatingBuilding: boolean;
  onUpdateBuilding: (
    buildingId: string,
    data: { name?: string; sort_order?: number }
  ) => void;
  onDeleteBuilding: (buildingId: string) => void;
  onCreateFloor: (
    buildingId: string,
    data: {
      name: string;
      level_number?: number | null;
      floor_height_m?: number | null;
    }
  ) => void;
  onUpdateFloor: (
    floorId: string,
    data: {
      name?: string;
      level_number?: number | null;
      floor_height_m?: number | null;
    }
  ) => void;
  onDeleteFloor: (floorId: string) => void;
  onCreateUnit: (
    floorId: string,
    data: { name: string; unit_type?: string | null }
  ) => void;
  onUpdateUnit: (
    unitId: string,
    data: { name?: string; unit_type?: string | null }
  ) => void;
  onDeleteUnit: (unitId: string) => void;
}

function StructureTree(props: StructureTreeProps) {
  const { buildings } = props;
  const [addingBuilding, setAddingBuilding] = useState(false);

  const noBuildings = buildings.length === 0;

  return (
    <div>
      {/* Action bar */}
      <div className="mb-4 flex flex-wrap gap-2">
        <button
          type="button"
          onClick={() => setAddingBuilding(true)}
          className="flex items-center gap-2 rounded-md bg-primary px-4 py-2 text-sm font-medium text-primary-foreground hover:bg-primary/90"
        >
          <Plus className="h-4 w-4" />
          Neues Gebäude
        </button>
        <button
          type="button"
          onClick={props.onQuickAdd}
          disabled={!noBuildings || props.isQuickAdding}
          title={
            noBuildings
              ? "Legt ein typisches Einfamilienhaus mit Keller, EG und OG an"
              : "Die Schnell-Anlage funktioniert nur mit leeren Projekten"
          }
          className="flex items-center gap-2 rounded-md border border-primary px-4 py-2 text-sm font-medium text-primary hover:bg-primary/5 disabled:cursor-not-allowed disabled:opacity-50"
        >
          <Sparkles className="h-4 w-4" />
          {props.isQuickAdding
            ? "Lege Einfamilienhaus an..."
            : "Schnell-Anlage: Einfamilienhaus"}
        </button>
      </div>

      {/* Info banner explaining why Schnell-Anlage is disabled. v14
          testers complained that a greyed-out button with nothing but
          a native tooltip looked broken — a visible banner makes the
          why obvious. Only shown when the project already has
          buildings; on a fresh project the button is enabled so there
          is no explaining to do. */}
      {!noBuildings && (
        <div
          role="note"
          className="mb-4 flex items-start gap-2 rounded-md border border-amber-300 bg-amber-50 px-3 py-2 text-xs text-amber-900"
        >
          <Info className="mt-0.5 h-4 w-4 shrink-0 text-amber-700" />
          <span>
            Die <strong>Schnell-Anlage</strong> ist nur für leere Projekte
            gedacht. Sie würde sonst ein zweites Gebäude „Haupthaus" mit
            identischer Struktur anlegen. Bitte Gebäude und Stockwerke über
            die Buttons oben manuell ergänzen.
          </span>
        </div>
      )}

      {addingBuilding && (
        <BuildingForm
          initial={null}
          onSubmit={(data) => {
            props.onCreateBuilding(data);
            setAddingBuilding(false);
          }}
          onCancel={() => setAddingBuilding(false)}
          isLoading={props.isCreatingBuilding}
        />
      )}

      {/* Empty state */}
      {noBuildings && !addingBuilding && (
        <div className="rounded-lg border border-dashed bg-muted/30 py-12 text-center">
          <Building2 className="mx-auto h-12 w-12 text-muted-foreground/50" />
          <h3 className="mt-4 text-lg font-medium">
            Noch keine Gebäudestruktur angelegt
          </h3>
          <p className="mx-auto mt-1 max-w-md text-sm text-muted-foreground">
            Legen Sie ein Gebäude manuell an oder nutzen Sie die Schnell-Anlage,
            um mit einem typischen Einfamilienhaus zu starten.
          </p>
        </div>
      )}

      {/* Tree */}
      <div className="space-y-3">
        {buildings.map((b) => (
          <BuildingNode
            key={b.id}
            building={b}
            onUpdate={props.onUpdateBuilding}
            onDelete={props.onDeleteBuilding}
            onCreateFloor={props.onCreateFloor}
            onUpdateFloor={props.onUpdateFloor}
            onDeleteFloor={props.onDeleteFloor}
            onCreateUnit={props.onCreateUnit}
            onUpdateUnit={props.onUpdateUnit}
            onDeleteUnit={props.onDeleteUnit}
          />
        ))}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// BuildingNode — one row in the tree for a Building, with inline edit,
// delete-confirm and an "Add floor" form.
// ---------------------------------------------------------------------------

interface BuildingNodeProps {
  building: BuildingWithChildren;
  onUpdate: (
    buildingId: string,
    data: { name?: string; sort_order?: number }
  ) => void;
  onDelete: (buildingId: string) => void;
  onCreateFloor: StructureTreeProps["onCreateFloor"];
  onUpdateFloor: StructureTreeProps["onUpdateFloor"];
  onDeleteFloor: StructureTreeProps["onDeleteFloor"];
  onCreateUnit: StructureTreeProps["onCreateUnit"];
  onUpdateUnit: StructureTreeProps["onUpdateUnit"];
  onDeleteUnit: StructureTreeProps["onDeleteUnit"];
}

function BuildingNode(props: BuildingNodeProps) {
  const { building } = props;
  const [expanded, setExpanded] = useState(true);
  const [editing, setEditing] = useState(false);
  const [confirmingDelete, setConfirmingDelete] = useState(false);
  const [addingFloor, setAddingFloor] = useState(false);

  const floorCount = building.floors.length;
  const roomCount = building.floors.reduce(
    (s, f) => s + f.units.reduce((s2, u) => s2 + u.rooms.length, 0),
    0
  );

  return (
    <div className="rounded-lg border bg-card">
      {/* Header */}
      <div className="flex items-center gap-2 p-4">
        <button
          type="button"
          onClick={() => setExpanded(!expanded)}
          className="rounded p-1 hover:bg-accent"
          aria-label={expanded ? "Einklappen" : "Ausklappen"}
        >
          {expanded ? (
            <ChevronDown className="h-4 w-4" />
          ) : (
            <ChevronRight className="h-4 w-4" />
          )}
        </button>
        <Building2 className="h-5 w-5 text-blue-600" />
        {editing ? (
          <div className="flex-1">
            <BuildingForm
              initial={building}
              onSubmit={(data) => {
                props.onUpdate(building.id, data);
                setEditing(false);
              }}
              onCancel={() => setEditing(false)}
            />
          </div>
        ) : (
          <>
            <div className="flex-1">
              <h2 className="font-semibold">{building.name}</h2>
              <p className="text-xs text-muted-foreground">
                {floorCount} Stockwerk{floorCount !== 1 ? "e" : ""} ·{" "}
                {roomCount} Raum/Räume
              </p>
            </div>
            <button
              type="button"
              onClick={() => setEditing(true)}
              className="rounded p-1.5 text-muted-foreground hover:bg-accent hover:text-foreground"
              aria-label="Gebäude bearbeiten"
              title="Gebäude bearbeiten"
            >
              <Edit2 className="h-4 w-4" />
            </button>
            <button
              type="button"
              onClick={() => setConfirmingDelete(true)}
              className="rounded p-1.5 text-muted-foreground hover:bg-destructive/10 hover:text-destructive"
              aria-label="Gebäude löschen"
              title="Gebäude löschen"
            >
              <Trash2 className="h-4 w-4" />
            </button>
          </>
        )}
      </div>

      {/* Children */}
      {expanded && (
        <div className="border-t bg-muted/20 p-4 pt-3">
          <div className="mb-3 flex justify-end">
            <button
              type="button"
              onClick={() => setAddingFloor(true)}
              className="flex items-center gap-1.5 rounded-md border bg-card px-3 py-1.5 text-xs font-medium hover:bg-accent"
            >
              <Plus className="h-3.5 w-3.5" />
              Stockwerk hinzufügen
            </button>
          </div>

          {addingFloor && (
            <FloorForm
              initial={null}
              onSubmit={(data) => {
                props.onCreateFloor(building.id, data);
                setAddingFloor(false);
              }}
              onCancel={() => setAddingFloor(false)}
            />
          )}

          {building.floors.length === 0 && !addingFloor && (
            <p className="text-sm italic text-muted-foreground">
              Keine Stockwerke in diesem Gebäude.
            </p>
          )}

          <div className="space-y-3">
            {building.floors.map((f) => (
              <FloorNode
                key={f.id}
                floor={f}
                onUpdate={props.onUpdateFloor}
                onDelete={props.onDeleteFloor}
                onCreateUnit={props.onCreateUnit}
                onUpdateUnit={props.onUpdateUnit}
                onDeleteUnit={props.onDeleteUnit}
              />
            ))}
          </div>
        </div>
      )}

      {confirmingDelete && (
        <ConfirmDeleteDialog
          entityLabel="Gebäude"
          entityName={building.name}
          cascadeNote={
            floorCount > 0
              ? `Dieses Gebäude enthält ${floorCount} Stockwerk${
                  floorCount !== 1 ? "e" : ""
                } und ${roomCount} Raum/Räume. Alle werden mitgelöscht.`
              : undefined
          }
          onConfirm={() => {
            props.onDelete(building.id);
            setConfirmingDelete(false);
          }}
          onCancel={() => setConfirmingDelete(false)}
        />
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// FloorNode — same pattern: inline add/edit, delete confirm, units nested.
// ---------------------------------------------------------------------------

interface FloorNodeProps {
  floor: FloorWithUnits;
  onUpdate: StructureTreeProps["onUpdateFloor"];
  onDelete: StructureTreeProps["onDeleteFloor"];
  onCreateUnit: StructureTreeProps["onCreateUnit"];
  onUpdateUnit: StructureTreeProps["onUpdateUnit"];
  onDeleteUnit: StructureTreeProps["onDeleteUnit"];
}

function FloorNode(props: FloorNodeProps) {
  const { floor } = props;
  const [expanded, setExpanded] = useState(true);
  const [editing, setEditing] = useState(false);
  const [confirmingDelete, setConfirmingDelete] = useState(false);
  const [addingUnit, setAddingUnit] = useState(false);

  const unitCount = floor.units.length;
  const roomCount = floor.units.reduce((s, u) => s + u.rooms.length, 0);

  return (
    <div className="rounded-md border bg-card">
      <div className="flex items-center gap-2 p-3">
        <button
          type="button"
          onClick={() => setExpanded(!expanded)}
          className="rounded p-1 hover:bg-accent"
          aria-label={expanded ? "Einklappen" : "Ausklappen"}
        >
          {expanded ? (
            <ChevronDown className="h-4 w-4" />
          ) : (
            <ChevronRight className="h-4 w-4" />
          )}
        </button>
        <Layers className="h-4 w-4 text-indigo-600" />
        {editing ? (
          <div className="flex-1">
            <FloorForm
              initial={floor}
              onSubmit={(data) => {
                props.onUpdate(floor.id, data);
                setEditing(false);
              }}
              onCancel={() => setEditing(false)}
            />
          </div>
        ) : (
          <>
            <div className="flex-1">
              <p className="font-medium">{floor.name}</p>
              <p className="text-xs text-muted-foreground">
                {floor.level_number !== null
                  ? `Ebene ${floor.level_number} · `
                  : ""}
                Raumhöhe{" "}
                {floor.floor_height_m !== null
                  ? `${fmtNumber(floor.floor_height_m)} m`
                  : `${fmtNumber(DEFAULT_FLOOR_HEIGHT_M)} m (Standard)`}{" "}
                · {unitCount} Einheit{unitCount !== 1 ? "en" : ""} ·{" "}
                {roomCount} Raum/Räume
              </p>
            </div>
            <button
              type="button"
              onClick={() => setEditing(true)}
              className="rounded p-1.5 text-muted-foreground hover:bg-accent hover:text-foreground"
              aria-label="Stockwerk bearbeiten"
              title="Stockwerk bearbeiten"
            >
              <Edit2 className="h-3.5 w-3.5" />
            </button>
            <button
              type="button"
              onClick={() => setConfirmingDelete(true)}
              className="rounded p-1.5 text-muted-foreground hover:bg-destructive/10 hover:text-destructive"
              aria-label="Stockwerk löschen"
              title="Stockwerk löschen"
            >
              <Trash2 className="h-3.5 w-3.5" />
            </button>
          </>
        )}
      </div>

      {expanded && (
        <div className="border-t bg-muted/10 p-3 pt-2">
          <div className="mb-3 flex justify-end">
            <button
              type="button"
              onClick={() => setAddingUnit(true)}
              className="flex items-center gap-1.5 rounded-md border bg-card px-3 py-1.5 text-xs font-medium hover:bg-accent"
            >
              <Plus className="h-3.5 w-3.5" />
              Einheit hinzufügen
            </button>
          </div>

          {addingUnit && (
            <UnitForm
              initial={null}
              onSubmit={(data) => {
                props.onCreateUnit(floor.id, data);
                setAddingUnit(false);
              }}
              onCancel={() => setAddingUnit(false)}
            />
          )}

          {floor.units.length === 0 && !addingUnit && (
            <p className="text-sm italic text-muted-foreground">
              Keine Einheiten in diesem Stockwerk.
            </p>
          )}

          <div className="space-y-3">
            {floor.units.map((u) => (
              <UnitNode
                key={u.id}
                unit={u}
                floorHeightM={floor.floor_height_m ?? DEFAULT_FLOOR_HEIGHT_M}
                onUpdate={props.onUpdateUnit}
                onDelete={props.onDeleteUnit}
              />
            ))}
          </div>
        </div>
      )}

      {confirmingDelete && (
        <ConfirmDeleteDialog
          entityLabel="Stockwerk"
          entityName={floor.name}
          cascadeNote={
            unitCount > 0
              ? `Dieses Stockwerk enthält ${unitCount} Einheit${
                  unitCount !== 1 ? "en" : ""
                } und ${roomCount} Raum/Räume. Alle werden mitgelöscht.`
              : undefined
          }
          onConfirm={() => {
            props.onDelete(floor.id);
            setConfirmingDelete(false);
          }}
          onCancel={() => setConfirmingDelete(false)}
        />
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// UnitNode — last wrapping layer. Room-level CRUD happens directly
// against the Rooms API (no prop drilling from the top), because Room
// mutations have more fields than Building/Floor/Unit and would bloat
// the parent's prop list. We fetch the same structure key for the
// invalidation so the tree still redraws after a room mutation.
// ---------------------------------------------------------------------------

interface UnitNodeProps {
  unit: UnitWithRooms;
  floorHeightM: number;
  onUpdate: StructureTreeProps["onUpdateUnit"];
  onDelete: StructureTreeProps["onDeleteUnit"];
}

function UnitNode(props: UnitNodeProps) {
  const { unit, floorHeightM } = props;
  const queryClient = useQueryClient();
  const { id: projectId } = useParams<{ id: string }>();

  const [expanded, setExpanded] = useState(true);
  const [editing, setEditing] = useState(false);
  const [confirmingDelete, setConfirmingDelete] = useState(false);
  const [addingRoom, setAddingRoom] = useState(false);
  const [roomError, setRoomError] = useState<string | null>(null);

  const invalidate = () => {
    queryClient.invalidateQueries({ queryKey: ["structure", projectId] });
    queryClient.invalidateQueries({ queryKey: ["rooms", projectId] });
  };

  const reportErr = (e: unknown) => setRoomError(normalizeError(e).message);

  const createRoomMut = useMutation({
    mutationFn: (data: Partial<Room> & { name: string }) =>
      createRoom(unit.id, data),
    onSuccess: () => {
      setAddingRoom(false);
      setRoomError(null);
      invalidate();
    },
    onError: reportErr,
  });

  return (
    <div className="rounded-md border bg-card">
      <div className="flex items-center gap-2 p-3">
        <button
          type="button"
          onClick={() => setExpanded(!expanded)}
          className="rounded p-1 hover:bg-accent"
          aria-label={expanded ? "Einklappen" : "Ausklappen"}
        >
          {expanded ? (
            <ChevronDown className="h-4 w-4" />
          ) : (
            <ChevronRight className="h-4 w-4" />
          )}
        </button>
        <Home className="h-4 w-4 text-emerald-600" />
        {editing ? (
          <div className="flex-1">
            <UnitForm
              initial={unit}
              onSubmit={(data) => {
                props.onUpdate(unit.id, data);
                setEditing(false);
              }}
              onCancel={() => setEditing(false)}
            />
          </div>
        ) : (
          <>
            <div className="flex-1">
              <p className="font-medium">{unit.name}</p>
              <p className="text-xs text-muted-foreground">
                {unit.unit_type ? `${unit.unit_type} · ` : ""}
                {unit.rooms.length} Raum/Räume
              </p>
            </div>
            <button
              type="button"
              onClick={() => setEditing(true)}
              className="rounded p-1.5 text-muted-foreground hover:bg-accent hover:text-foreground"
              aria-label="Einheit bearbeiten"
              title="Einheit bearbeiten"
            >
              <Edit2 className="h-3.5 w-3.5" />
            </button>
            <button
              type="button"
              onClick={() => setConfirmingDelete(true)}
              className="rounded p-1.5 text-muted-foreground hover:bg-destructive/10 hover:text-destructive"
              aria-label="Einheit löschen"
              title="Einheit löschen"
            >
              <Trash2 className="h-3.5 w-3.5" />
            </button>
          </>
        )}
      </div>

      {expanded && (
        <div className="border-t bg-muted/5 p-3 pt-2">
          <div className="mb-3 flex justify-end">
            <button
              type="button"
              onClick={() => {
                setAddingRoom(true);
                setRoomError(null);
              }}
              className="flex items-center gap-1.5 rounded-md border bg-card px-3 py-1.5 text-xs font-medium hover:bg-accent"
            >
              <Plus className="h-3.5 w-3.5" />
              Raum hinzufügen
            </button>
          </div>

          {roomError && (
            <div
              role="alert"
              className="mb-3 rounded-md border border-destructive/30 bg-destructive/5 p-3 text-sm text-destructive"
            >
              {roomError}
            </div>
          )}

          {addingRoom && (
            <RoomForm
              initial={null}
              floorHeightM={floorHeightM}
              onSubmit={(data) => createRoomMut.mutate(data)}
              onCancel={() => setAddingRoom(false)}
              isLoading={createRoomMut.isPending}
            />
          )}

          {unit.rooms.length === 0 && !addingRoom && (
            <p className="text-sm italic text-muted-foreground">
              Keine Räume in dieser Einheit.
            </p>
          )}

          <div className="space-y-2">
            {unit.rooms.map((r) => (
              <RoomNode
                key={r.id}
                room={r}
                floorHeightM={floorHeightM}
                onReportError={(msg) => setRoomError(msg)}
              />
            ))}
          </div>
        </div>
      )}

      {confirmingDelete && (
        <ConfirmDeleteDialog
          entityLabel="Einheit"
          entityName={unit.name}
          cascadeNote={
            unit.rooms.length > 0
              ? `Diese Einheit enthält ${unit.rooms.length} Raum/Räume. Alle werden mitgelöscht.`
              : undefined
          }
          onConfirm={() => {
            props.onDelete(unit.id);
            setConfirmingDelete(false);
          }}
          onCancel={() => setConfirmingDelete(false)}
        />
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// RoomNode — leaf of the tree. Edits are inline on the Room card (as
// opposed to a modal) because the form is long and an inline editor
// lets the user see the openings list alongside the main fields.
// ---------------------------------------------------------------------------

function RoomNode({
  room,
  floorHeightM,
  onReportError,
}: {
  room: Room;
  floorHeightM: number;
  onReportError: (msg: string) => void;
}) {
  const queryClient = useQueryClient();
  const { id: projectId } = useParams<{ id: string }>();

  const [editing, setEditing] = useState(false);
  const [confirmingDelete, setConfirmingDelete] = useState(false);

  const invalidate = () => {
    queryClient.invalidateQueries({ queryKey: ["structure", projectId] });
    queryClient.invalidateQueries({ queryKey: ["rooms", projectId] });
  };

  const updateRoomMut = useMutation({
    mutationFn: (data: Partial<Room>) => updateRoom(room.id, data),
    onSuccess: () => {
      setEditing(false);
      invalidate();
    },
    onError: (e) => onReportError(normalizeError(e).message),
  });

  // Separate mutation for the inline single-field edits (perimeter,
  // height). Same endpoint as ``updateRoomMut``, but without the
  // ``setEditing(false)`` side-effect — inline edits don't open or
  // close the full card editor, and we want them to fly through
  // independently of whether the user happens to also be in card
  // edit mode.
  const quickSaveMut = useMutation({
    mutationFn: (data: Partial<Room>) => updateRoom(room.id, data),
    onSuccess: invalidate,
    onError: (e) => onReportError(normalizeError(e).message),
  });

  const deleteRoomMut = useMutation({
    mutationFn: () => deleteRoom(room.id),
    onSuccess: invalidate,
    onError: (e) => onReportError(normalizeError(e).message),
  });

  const addOpeningMut = useMutation({
    mutationFn: (data: Omit<Opening, "id" | "room_id" | "source">) =>
      createOpening(room.id, data),
    onSuccess: invalidate,
    onError: (e) => onReportError(normalizeError(e).message),
  });

  const updateOpeningMut = useMutation({
    mutationFn: ({
      openingId,
      data,
    }: {
      openingId: string;
      data: Partial<Omit<Opening, "id" | "room_id" | "source">>;
    }) => updateOpening(openingId, data),
    onSuccess: invalidate,
    onError: (e) => onReportError(normalizeError(e).message),
  });

  const deleteOpeningMut = useMutation({
    mutationFn: (openingId: string) => deleteOpening(openingId),
    onSuccess: invalidate,
    onError: (e) => onReportError(normalizeError(e).message),
  });

  if (editing) {
    return (
      <div className="rounded-md border border-primary/50 bg-card p-3 ring-2 ring-primary/10">
        <RoomForm
          initial={room}
          floorHeightM={floorHeightM}
          onSubmit={(data) => updateRoomMut.mutate(data)}
          onCancel={() => setEditing(false)}
          isLoading={updateRoomMut.isPending}
        />
      </div>
    );
  }

  const ceilingSource = room.ceiling_height_source;
  const ceilingWarn = ceilingSource === "default";

  return (
    <div className="rounded-md border bg-card p-3">
      <div className="flex items-start gap-2">
        <DoorOpen className="mt-0.5 h-4 w-4 shrink-0 text-orange-600" />
        <div className="flex-1">
          <div className="flex flex-wrap items-baseline gap-x-2 gap-y-0.5">
            <span className="font-medium">{room.name}</span>
            {room.room_number && (
              <span className="text-xs text-muted-foreground">
                #{room.room_number}
              </span>
            )}
            {room.room_type && (
              <span className="rounded bg-muted px-1.5 py-0.5 text-[10px] font-medium text-muted-foreground">
                {ROOM_TYPE_OPTIONS.find((o) => o.value === room.room_type)
                  ?.label ?? room.room_type}
              </span>
            )}
            {room.is_staircase && (
              <span className="rounded bg-amber-100 px-1.5 py-0.5 text-[10px] font-medium text-amber-800">
                Treppenhaus (Faktor 1,5)
              </span>
            )}
          </div>
          <div className="mt-1 flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-muted-foreground">
            <span className="inline-flex items-center gap-1">
              Fläche:
              <span className="font-mono text-foreground">
                {fmtNumber(room.area_m2)} m²
              </span>
            </span>
            <span className="inline-flex items-center gap-1">
              Umfang:
              <InlineNumericEdit
                value={room.perimeter_m}
                unit="m"
                state={room.perimeter_m === null ? "missing" : "ok"}
                missingLabel="Bitte eintragen"
                warningLabel=""
                tooltip="Wandumfang fehlt — bitte aus Plan messen oder schätzen"
                isSaving={quickSaveMut.isPending}
                onSave={(next) => quickSaveMut.mutate({ perimeter_m: next })}
                ariaLabel={`Umfang von ${room.name} bearbeiten`}
              />
            </span>
            <span className="inline-flex items-center gap-1">
              Raumhöhe:
              <InlineNumericEdit
                value={room.height_m}
                unit="m"
                state={
                  room.height_m === null
                    ? "missing"
                    : ceilingWarn
                      ? "warning"
                      : "ok"
                }
                missingLabel="Bitte eintragen"
                warningLabel="Bitte prüfen"
                tooltip={
                  room.height_m === null
                    ? "Raumhöhe fehlt — bitte aus Plan oder Schnitt messen"
                    : "Deckenhöhe wurde auf 2,50 m geschätzt — bitte aus Plan oder Schnitt prüfen"
                }
                isSaving={quickSaveMut.isPending}
                onSave={(next) => quickSaveMut.mutate({ height_m: next })}
                ariaLabel={`Raumhöhe von ${room.name} bearbeiten`}
              />
            </span>
            <span className="inline-flex items-center gap-1">
              Wandfläche netto:
              <span className="font-mono text-foreground">
                {fmtNumber(room.wall_area_net_m2)} m²
              </span>
            </span>
          </div>
          {!room.deductions_enabled && (
            <p className="mt-1 text-xs text-amber-700">
              Öffnungsabzug deaktiviert (brutto = netto).
            </p>
          )}
        </div>
        <button
          type="button"
          onClick={() => setEditing(true)}
          className="rounded p-1.5 text-muted-foreground hover:bg-accent hover:text-foreground"
          aria-label="Raum bearbeiten"
          title="Raum bearbeiten"
        >
          <Edit2 className="h-3.5 w-3.5" />
        </button>
        <button
          type="button"
          onClick={() => setConfirmingDelete(true)}
          className="rounded p-1.5 text-muted-foreground hover:bg-destructive/10 hover:text-destructive"
          aria-label="Raum löschen"
          title="Raum löschen"
        >
          <Trash2 className="h-3.5 w-3.5" />
        </button>
      </div>

      {/* Openings sub-table */}
      <OpeningsTable
        openings={room.openings}
        onAdd={(data) => addOpeningMut.mutate(data)}
        onUpdate={(openingId, data) =>
          updateOpeningMut.mutate({ openingId, data })
        }
        onDelete={(openingId) => deleteOpeningMut.mutate(openingId)}
      />

      {confirmingDelete && (
        <ConfirmDeleteDialog
          entityLabel="Raum"
          entityName={room.name}
          cascadeNote={
            room.openings.length > 0
              ? `Dieser Raum enthält ${room.openings.length} Öffnung${
                  room.openings.length !== 1 ? "en" : ""
                } (Fenster/Türen). Alle werden mitgelöscht.`
              : undefined
          }
          onConfirm={() => {
            deleteRoomMut.mutate();
            setConfirmingDelete(false);
          }}
          onCancel={() => setConfirmingDelete(false)}
        />
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// OpeningsTable — embedded in each RoomNode. Adds/edits/deletes are
// optimistic-free: the mutation fires, on success the whole structure
// invalidates and re-renders. This adds one round-trip of latency but
// keeps the calculated wall area in sync with what the user sees
// (opening changes trigger the backend's automatic wall-area recalc).
// ---------------------------------------------------------------------------

function OpeningsTable({
  openings,
  onAdd,
  onUpdate,
  onDelete,
}: {
  openings: Opening[];
  onAdd: (data: Omit<Opening, "id" | "room_id" | "source">) => void;
  onUpdate: (
    openingId: string,
    data: Partial<Omit<Opening, "id" | "room_id" | "source">>
  ) => void;
  onDelete: (openingId: string) => void;
}) {
  const [adding, setAdding] = useState(false);
  const [editingId, setEditingId] = useState<string | null>(null);

  return (
    <div className="mt-3 rounded-md border bg-muted/10 p-2">
      <div className="mb-2 flex items-center justify-between px-1">
        <p className="text-xs font-medium text-muted-foreground">
          Öffnungen (Fenster / Türen)
        </p>
        <button
          type="button"
          onClick={() => setAdding(true)}
          className="flex items-center gap-1 rounded-md border bg-card px-2 py-1 text-[11px] font-medium hover:bg-accent"
        >
          <Plus className="h-3 w-3" />
          Öffnung hinzufügen
        </button>
      </div>

      {adding && (
        <div className="mb-2 rounded-md border bg-card p-2">
          <OpeningForm
            initial={null}
            onSubmit={(data) => {
              onAdd(data);
              setAdding(false);
            }}
            onCancel={() => setAdding(false)}
          />
        </div>
      )}

      {openings.length === 0 && !adding ? (
        <p className="px-1 py-1 text-xs italic text-muted-foreground">
          Keine Öffnungen erfasst.
        </p>
      ) : (
        <div className="space-y-1">
          {openings.map((o) =>
            editingId === o.id ? (
              <div key={o.id} className="rounded-md border bg-card p-2">
                <OpeningForm
                  initial={o}
                  onSubmit={(data) => {
                    onUpdate(o.id, data);
                    setEditingId(null);
                  }}
                  onCancel={() => setEditingId(null)}
                />
              </div>
            ) : (
              <div
                key={o.id}
                className="flex items-center gap-2 rounded-md bg-card px-2 py-1 text-xs"
              >
                <span className="font-medium">
                  {OPENING_TYPE_OPTIONS.find((opt) => opt.value === o.opening_type)
                    ?.label ?? o.opening_type}
                </span>
                <span className="font-mono text-muted-foreground">
                  {fmtNumber(o.width_m)} × {fmtNumber(o.height_m)} m
                </span>
                {o.count > 1 && (
                  <span className="text-muted-foreground">×{o.count}</span>
                )}
                {o.description && (
                  <span className="text-muted-foreground">— {o.description}</span>
                )}
                <div className="ml-auto flex items-center gap-1">
                  <button
                    type="button"
                    onClick={() => setEditingId(o.id)}
                    className="rounded p-1 text-muted-foreground hover:bg-accent hover:text-foreground"
                    aria-label="Öffnung bearbeiten"
                  >
                    <Edit2 className="h-3 w-3" />
                  </button>
                  <button
                    type="button"
                    onClick={() => onDelete(o.id)}
                    className="rounded p-1 text-muted-foreground hover:bg-destructive/10 hover:text-destructive"
                    aria-label="Öffnung löschen"
                  >
                    <Trash2 className="h-3 w-3" />
                  </button>
                </div>
              </div>
            )
          )}
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Forms. Each of BuildingForm / FloorForm / UnitForm / RoomForm /
// OpeningForm handle both create (``initial === null``) and edit
// (``initial`` populated) — collapsing create and edit into one form
// component halves the code and guarantees field parity between the
// two code paths.
// ---------------------------------------------------------------------------

function BuildingForm({
  initial,
  onSubmit,
  onCancel,
  isLoading,
}: {
  initial: { name: string; sort_order: number } | null;
  onSubmit: (data: { name: string; sort_order?: number }) => void;
  onCancel: () => void;
  isLoading?: boolean;
}) {
  const [name, setName] = useState(initial?.name ?? "");
  const [sortOrder, setSortOrder] = useState<string>(
    initial?.sort_order?.toString() ?? "0"
  );

  return (
    <form
      onSubmit={(e) => {
        e.preventDefault();
        if (!name.trim()) return;
        onSubmit({
          name: name.trim(),
          sort_order: parseInt(sortOrder, 10) || 0,
        });
      }}
      className="mb-4 rounded-md border bg-card p-3"
    >
      <p className="mb-2 text-sm font-medium">
        {initial ? "Gebäude bearbeiten" : "Neues Gebäude"}
      </p>
      <div className="grid gap-2 sm:grid-cols-[1fr,120px]">
        <div>
          <label className="mb-1 block text-xs font-medium">Name *</label>
          <input
            autoFocus
            required
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="z.B. Haupthaus"
            className="w-full rounded-md border px-3 py-1.5 text-sm"
          />
        </div>
        <div>
          <label className="mb-1 block text-xs font-medium">
            Reihenfolge
          </label>
          <input
            type="number"
            value={sortOrder}
            onChange={(e) => setSortOrder(e.target.value)}
            className="w-full rounded-md border px-3 py-1.5 text-sm"
          />
        </div>
      </div>
      <div className="mt-3 flex gap-2">
        <button
          type="submit"
          disabled={!name.trim() || isLoading}
          className="rounded-md bg-primary px-3 py-1.5 text-sm font-medium text-primary-foreground hover:bg-primary/90 disabled:opacity-50"
        >
          {isLoading ? "Speichere..." : "Speichern"}
        </button>
        <button
          type="button"
          onClick={onCancel}
          className="rounded-md border px-3 py-1.5 text-sm font-medium hover:bg-accent"
        >
          Abbrechen
        </button>
      </div>
    </form>
  );
}

function FloorForm({
  initial,
  onSubmit,
  onCancel,
  isLoading,
}: {
  initial: {
    name: string;
    level_number: number | null;
    floor_height_m: number | null;
  } | null;
  onSubmit: (data: {
    name: string;
    level_number?: number | null;
    floor_height_m?: number | null;
  }) => void;
  onCancel: () => void;
  isLoading?: boolean;
}) {
  const [name, setName] = useState(initial?.name ?? "");
  const [level, setLevel] = useState<string>(
    initial?.level_number?.toString() ?? ""
  );
  const [height, setHeight] = useState<string>(
    initial?.floor_height_m?.toString() ?? DEFAULT_FLOOR_HEIGHT_M.toString()
  );

  return (
    <form
      onSubmit={(e) => {
        e.preventDefault();
        if (!name.trim()) return;
        const parsedLevel =
          level.trim() === "" ? null : parseInt(level, 10);
        const parsedHeight =
          height.trim() === "" ? null : parseFloat(height.replace(",", "."));
        onSubmit({
          name: name.trim(),
          level_number: Number.isNaN(parsedLevel) ? null : parsedLevel,
          floor_height_m: Number.isNaN(parsedHeight) ? null : parsedHeight,
        });
      }}
      className="mb-4 rounded-md border bg-card p-3"
    >
      <p className="mb-2 text-sm font-medium">
        {initial ? "Stockwerk bearbeiten" : "Neues Stockwerk"}
      </p>
      <div className="grid gap-2 sm:grid-cols-[2fr,1fr,1fr]">
        <div>
          <label className="mb-1 block text-xs font-medium">Name *</label>
          <input
            autoFocus
            required
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="z.B. EG, OG, Keller"
            className="w-full rounded-md border px-3 py-1.5 text-sm"
          />
        </div>
        <div>
          <label className="mb-1 block text-xs font-medium">
            Ebene (Zahl)
          </label>
          <input
            type="number"
            value={level}
            onChange={(e) => setLevel(e.target.value)}
            placeholder="-1, 0, 1..."
            className="w-full rounded-md border px-3 py-1.5 text-sm"
          />
        </div>
        <div>
          <label className="mb-1 block text-xs font-medium">
            Raumhöhe (m)
          </label>
          <input
            type="text"
            inputMode="decimal"
            value={height}
            onChange={(e) => setHeight(e.target.value)}
            placeholder="2,50"
            className="w-full rounded-md border px-3 py-1.5 text-sm"
          />
        </div>
      </div>
      <p className="mt-1 text-[11px] text-muted-foreground">
        Die Raumhöhe wird als Vorgabe für neue Räume in diesem Stockwerk
        übernommen.
      </p>
      <div className="mt-3 flex gap-2">
        <button
          type="submit"
          disabled={!name.trim() || isLoading}
          className="rounded-md bg-primary px-3 py-1.5 text-sm font-medium text-primary-foreground hover:bg-primary/90 disabled:opacity-50"
        >
          {isLoading ? "Speichere..." : "Speichern"}
        </button>
        <button
          type="button"
          onClick={onCancel}
          className="rounded-md border px-3 py-1.5 text-sm font-medium hover:bg-accent"
        >
          Abbrechen
        </button>
      </div>
    </form>
  );
}

function UnitForm({
  initial,
  onSubmit,
  onCancel,
  isLoading,
}: {
  initial: { name: string; unit_type: string | null } | null;
  onSubmit: (data: { name: string; unit_type?: string | null }) => void;
  onCancel: () => void;
  isLoading?: boolean;
}) {
  const [name, setName] = useState(initial?.name ?? "");
  const [unitType, setUnitType] = useState(initial?.unit_type ?? "");

  return (
    <form
      onSubmit={(e) => {
        e.preventDefault();
        if (!name.trim()) return;
        onSubmit({
          name: name.trim(),
          unit_type: unitType.trim() || null,
        });
      }}
      className="mb-4 rounded-md border bg-card p-3"
    >
      <p className="mb-2 text-sm font-medium">
        {initial ? "Einheit bearbeiten" : "Neue Einheit"}
      </p>
      <div className="grid gap-2 sm:grid-cols-2">
        <div>
          <label className="mb-1 block text-xs font-medium">Name *</label>
          <input
            autoFocus
            required
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="z.B. Wohnung Top 1, Standard"
            className="w-full rounded-md border px-3 py-1.5 text-sm"
          />
        </div>
        <div>
          <label className="mb-1 block text-xs font-medium">
            Typ (optional)
          </label>
          <input
            value={unitType}
            onChange={(e) => setUnitType(e.target.value)}
            placeholder="Wohnung, Büro, Gewerbe..."
            className="w-full rounded-md border px-3 py-1.5 text-sm"
          />
        </div>
      </div>
      <div className="mt-3 flex gap-2">
        <button
          type="submit"
          disabled={!name.trim() || isLoading}
          className="rounded-md bg-primary px-3 py-1.5 text-sm font-medium text-primary-foreground hover:bg-primary/90 disabled:opacity-50"
        >
          {isLoading ? "Speichere..." : "Speichern"}
        </button>
        <button
          type="button"
          onClick={onCancel}
          className="rounded-md border px-3 py-1.5 text-sm font-medium hover:bg-accent"
        >
          Abbrechen
        </button>
      </div>
    </form>
  );
}

function RoomForm({
  initial,
  floorHeightM,
  onSubmit,
  onCancel,
  isLoading,
}: {
  initial: Room | null;
  floorHeightM: number;
  onSubmit: (data: Partial<Room> & { name: string }) => void;
  onCancel: () => void;
  isLoading?: boolean;
}) {
  const [name, setName] = useState(initial?.name ?? "");
  const [number, setNumber] = useState(initial?.room_number ?? "");
  const [area, setArea] = useState<string>(
    initial?.area_m2?.toString().replace(".", ",") ?? ""
  );
  const [perimeter, setPerimeter] = useState<string>(
    initial?.perimeter_m?.toString().replace(".", ",") ?? ""
  );
  const [height, setHeight] = useState<string>(
    initial?.height_m?.toString().replace(".", ",") ??
      floorHeightM.toString().replace(".", ",")
  );
  const [roomType, setRoomType] = useState<string>(
    initial?.room_type ?? "normal"
  );
  const [deductionsEnabled, setDeductionsEnabled] = useState<boolean>(
    initial?.deductions_enabled ?? true
  );

  const parseNum = (s: string): number | null => {
    const t = s.trim().replace(",", ".");
    if (t === "") return null;
    const n = parseFloat(t);
    return Number.isNaN(n) ? null : n;
  };

  return (
    <form
      onSubmit={(e) => {
        e.preventDefault();
        if (!name.trim()) return;
        // For manually-entered rooms the ceiling-height source is
        // "manual" whenever the user has typed a value (even if they
        // kept the pre-filled floor default — they saw it, they
        // accepted it). If they cleared the field, we fall back to
        // "default" so the amber warning appears.
        const parsedHeight = parseNum(height);
        const ceilingSource = parsedHeight === null ? "default" : "manual";
        onSubmit({
          name: name.trim(),
          room_number: number.trim() || null,
          area_m2: parseNum(area),
          perimeter_m: parseNum(perimeter),
          height_m: parsedHeight,
          room_type: roomType || null,
          is_staircase: roomType === "stairwell",
          is_wet_room: roomType === "bathroom",
          deductions_enabled: deductionsEnabled,
          ceiling_height_source: ceilingSource,
        });
      }}
      className="space-y-3"
    >
      <p className="text-sm font-medium">
        {initial ? "Raum bearbeiten" : "Neuer Raum"}
      </p>

      <div className="grid gap-2 sm:grid-cols-[2fr,1fr]">
        <div>
          <label className="mb-1 block text-xs font-medium">Name *</label>
          <input
            autoFocus
            required
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="z.B. Wohnzimmer, Bad, Schlafzimmer 1"
            className="w-full rounded-md border px-3 py-1.5 text-sm"
          />
        </div>
        <div>
          <label className="mb-1 block text-xs font-medium">
            Raumnummer
          </label>
          <input
            value={number}
            onChange={(e) => setNumber(e.target.value)}
            placeholder="01, 02..."
            className="w-full rounded-md border px-3 py-1.5 text-sm"
          />
        </div>
      </div>

      <div className="grid gap-2 sm:grid-cols-3">
        <div>
          <label className="mb-1 block text-xs font-medium">
            Fläche (m²)
          </label>
          <input
            type="text"
            inputMode="decimal"
            value={area}
            onChange={(e) => setArea(e.target.value)}
            placeholder="25,50"
            className="w-full rounded-md border px-3 py-1.5 text-sm"
          />
        </div>
        <div>
          <label className="mb-1 block text-xs font-medium">
            Wandumfang (m)
          </label>
          <input
            type="text"
            inputMode="decimal"
            value={perimeter}
            onChange={(e) => setPerimeter(e.target.value)}
            placeholder="20,20"
            className="w-full rounded-md border px-3 py-1.5 text-sm"
          />
        </div>
        <div>
          <label className="mb-1 block text-xs font-medium">
            Raumhöhe (m)
          </label>
          <input
            type="text"
            inputMode="decimal"
            value={height}
            onChange={(e) => setHeight(e.target.value)}
            placeholder={floorHeightM.toString().replace(".", ",")}
            className="w-full rounded-md border px-3 py-1.5 text-sm"
          />
          <p className="mt-0.5 text-[10px] text-muted-foreground">
            Vorgabe vom Stockwerk: {fmtNumber(floorHeightM)} m
          </p>
        </div>
      </div>

      <div className="grid gap-2 sm:grid-cols-2">
        <div>
          <label className="mb-1 block text-xs font-medium">Raumtyp</label>
          <select
            value={roomType}
            onChange={(e) => setRoomType(e.target.value)}
            className="w-full rounded-md border bg-white px-3 py-1.5 text-sm"
          >
            {ROOM_TYPE_OPTIONS.map((o) => (
              <option key={o.value} value={o.value}>
                {o.label}
              </option>
            ))}
          </select>
          {roomType === "stairwell" && (
            <p className="mt-0.5 text-[10px] text-amber-700">
              Wandberechnung multipliziert mit Faktor 1,5 (österr. Standard).
            </p>
          )}
        </div>
        <div className="flex items-center">
          <label className="mt-4 flex cursor-pointer items-center gap-2 text-sm">
            <input
              type="checkbox"
              checked={deductionsEnabled}
              onChange={(e) => setDeductionsEnabled(e.target.checked)}
              className="h-4 w-4 rounded border"
            />
            <span>Öffnungsabzug aktiv</span>
          </label>
        </div>
      </div>

      <div className="flex gap-2">
        <button
          type="submit"
          disabled={!name.trim() || isLoading}
          className="rounded-md bg-primary px-3 py-1.5 text-sm font-medium text-primary-foreground hover:bg-primary/90 disabled:opacity-50"
        >
          {isLoading ? "Speichere..." : "Speichern"}
        </button>
        <button
          type="button"
          onClick={onCancel}
          className="rounded-md border px-3 py-1.5 text-sm font-medium hover:bg-accent"
        >
          Abbrechen
        </button>
      </div>
    </form>
  );
}

function OpeningForm({
  initial,
  onSubmit,
  onCancel,
}: {
  initial: Opening | null;
  onSubmit: (data: Omit<Opening, "id" | "room_id" | "source">) => void;
  onCancel: () => void;
}) {
  const [openingType, setOpeningType] = useState(
    initial?.opening_type ?? "fenster"
  );
  const [width, setWidth] = useState<string>(
    initial?.width_m?.toString().replace(".", ",") ?? ""
  );
  const [height, setHeight] = useState<string>(
    initial?.height_m?.toString().replace(".", ",") ?? ""
  );
  const [count, setCount] = useState<string>(
    initial?.count?.toString() ?? "1"
  );
  const [description, setDescription] = useState(initial?.description ?? "");

  const parseNum = (s: string): number => {
    const t = s.trim().replace(",", ".");
    const n = parseFloat(t);
    return Number.isNaN(n) ? 0 : n;
  };

  return (
    <form
      onSubmit={(e) => {
        e.preventDefault();
        const w = parseNum(width);
        const h = parseNum(height);
        const c = Math.max(1, parseInt(count, 10) || 1);
        if (w <= 0 || h <= 0) return;
        onSubmit({
          opening_type: openingType,
          width_m: w,
          height_m: h,
          count: c,
          description: description.trim() || null,
        });
      }}
      className="grid gap-2 text-xs sm:grid-cols-[80px,80px,80px,50px,1fr,auto]"
    >
      <div>
        <label className="mb-0.5 block font-medium">Typ</label>
        <select
          value={openingType}
          onChange={(e) => setOpeningType(e.target.value)}
          className="w-full rounded-md border bg-white px-2 py-1 text-xs"
        >
          {OPENING_TYPE_OPTIONS.map((o) => (
            <option key={o.value} value={o.value}>
              {o.label}
            </option>
          ))}
        </select>
      </div>
      <div>
        <label className="mb-0.5 block font-medium">Breite (m)</label>
        <input
          type="text"
          inputMode="decimal"
          value={width}
          onChange={(e) => setWidth(e.target.value)}
          placeholder="1,20"
          className="w-full rounded-md border px-2 py-1 text-xs"
        />
      </div>
      <div>
        <label className="mb-0.5 block font-medium">Höhe (m)</label>
        <input
          type="text"
          inputMode="decimal"
          value={height}
          onChange={(e) => setHeight(e.target.value)}
          placeholder="1,50"
          className="w-full rounded-md border px-2 py-1 text-xs"
        />
      </div>
      <div>
        <label className="mb-0.5 block font-medium">Anzahl</label>
        <input
          type="number"
          min="1"
          value={count}
          onChange={(e) => setCount(e.target.value)}
          className="w-full rounded-md border px-2 py-1 text-xs"
        />
      </div>
      <div>
        <label className="mb-0.5 block font-medium">Beschreibung</label>
        <input
          value={description}
          onChange={(e) => setDescription(e.target.value)}
          placeholder="optional"
          className="w-full rounded-md border px-2 py-1 text-xs"
        />
      </div>
      <div className="flex items-end gap-1">
        <button
          type="submit"
          className="rounded-md bg-primary px-2 py-1 text-xs font-medium text-primary-foreground hover:bg-primary/90"
        >
          OK
        </button>
        <button
          type="button"
          onClick={onCancel}
          className="rounded-md border px-2 py-1 text-xs font-medium hover:bg-accent"
        >
          <X className="h-3 w-3" />
        </button>
      </div>
    </form>
  );
}

// ---------------------------------------------------------------------------
// ConfirmDeleteDialog — warns about cascade before an irreversible
// delete. Uses a simple centered overlay rather than a headless-ui
// modal because the codebase doesn't pull in any modal library and
// the UX tradeoff is minor.
// ---------------------------------------------------------------------------

function ConfirmDeleteDialog({
  entityLabel,
  entityName,
  cascadeNote,
  onConfirm,
  onCancel,
}: {
  entityLabel: string;
  entityName: string;
  cascadeNote?: string;
  onConfirm: () => void;
  onCancel: () => void;
}) {
  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4"
      onClick={onCancel}
    >
      <div
        className="w-full max-w-md rounded-lg bg-card p-5 shadow-xl"
        onClick={(e) => e.stopPropagation()}
        role="dialog"
        aria-modal="true"
      >
        <div className="flex items-start gap-3">
          <div className="rounded-full bg-destructive/10 p-2">
            <AlertTriangle className="h-5 w-5 text-destructive" />
          </div>
          <div className="flex-1">
            <h3 className="font-semibold">
              {entityLabel} wirklich löschen?
            </h3>
            <p className="mt-1 text-sm text-muted-foreground">
              <span className="font-medium text-foreground">{entityName}</span>{" "}
              wird unwiderruflich gelöscht.
            </p>
            {cascadeNote && (
              <p className="mt-2 rounded-md bg-amber-50 p-2 text-xs text-amber-800">
                {cascadeNote}
              </p>
            )}
          </div>
        </div>
        <div className="mt-4 flex justify-end gap-2">
          <button
            type="button"
            onClick={onCancel}
            className="rounded-md border px-4 py-2 text-sm font-medium hover:bg-accent"
          >
            Abbrechen
          </button>
          <button
            type="button"
            onClick={onConfirm}
            className="rounded-md bg-destructive px-4 py-2 text-sm font-medium text-white hover:bg-destructive/90"
          >
            Löschen
          </button>
        </div>
      </div>
    </div>
  );
}
