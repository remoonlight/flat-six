import {
  classifySessionFinal,
  combinedGeneration,
  flattenNodes,
} from "./can-topology-logic.mjs";
import { topologySeed } from "./can-topology-data";
import {
  normalizeSessionFinal,
  normalizeTopologyCapture,
  eventIdFromResults,
  type DiagSource,
  type DiagSnapshot,
} from "@porsche981/domain";
import type { PorscheApi } from "./api";

export async function persistTopologySnapshot(
  api: PorscheApi,
  input: {
    task: "read" | "clear";
    source: DiagSource;
    results: Array<{ nodeId: string; classified?: Record<string, unknown>; doc?: { final?: Record<string, unknown> } }>;
  },
) {
  if (input.task !== "read" || !api.obdDiag) return null;
  if (!input.results.length) return null;
  const gen = topologySeed();
  const nodes = flattenNodes(combinedGeneration(gen)) as Array<{ id: string; label?: string; profileId?: string }>;
  const snap = normalizeTopologyCapture({
    source: input.source,
    results: input.results,
    nodes,
    at: new Date().toISOString(),
    captureEventId: eventIdFromResults(input.results),
  });
  return await api.obdDiag({ op: "snapshot:capture", value: snap as unknown as Record<string, unknown> }) as DiagSnapshot;
}

export async function persistSessionSnapshot(
  api: PorscheApi,
  input: {
    source: DiagSource;
    profileId: string;
    name?: string;
    final: Record<string, unknown> | null;
    jobId?: string;
  },
) {
  if (!api.obdDiag || !input.final) return null;
  const classified = classifySessionFinal(input.final, {
    profileId: input.profileId,
    mode: input.source,
    simulated: input.source === "simulation",
  }) as { kind?: string; records?: unknown[] };
  const eventId = String(input.jobId || input.final.runId || "");
  if (!eventId) throw new Error("diag_capture_event_required");
  const snap = normalizeSessionFinal({
    source: input.source,
    profileId: input.profileId,
    name: input.name,
    final: input.final,
    classifiedKind: classified.kind,
    records: classified.records,
    at: new Date().toISOString(),
    captureEventId: eventId,
  });
  return api.obdDiag({ op: "snapshot:capture", value: snap as unknown as Record<string, unknown> });
}
