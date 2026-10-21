declare module "*can-topology-logic.mjs" {
  export function classifySessionFinal(final: unknown, extras?: Record<string, unknown>): Record<string, unknown>;
  export function flattenNodes(gen: unknown): Array<Record<string, unknown>>;
  export function branchAppearances(gen: unknown): Array<Record<string, unknown> & { appearances: Array<Record<string, unknown>> }>;
  export function emptyStatusMap(gen: unknown): Record<string, Record<string, unknown>>;
  export function adaptedNodes(gen: unknown): Array<Record<string, unknown>>;
  export function generationOf(seed: unknown, id: string): unknown;
  export function combinedGeneration(seed: unknown): unknown;
  export function isAdaptedProfile(id: unknown): boolean;
  export function canTransmit(node: unknown): boolean;
  export function liveAllowed(generationId: string, mode?: string): boolean;
  export function matchQuery(node: Record<string, unknown>, q: string): boolean;
  export function countSummary(
    nodes: unknown[],
    statuses: Record<string, Record<string, unknown>>,
  ): Record<string, number>;
  export function statusText(entry: Record<string, unknown>, opts?: { simulated?: boolean }): string;
  export function dtcLabel(rec: unknown): string;
  export function dtcDetail(rec: unknown, profileId?: string): string;
  export function manualDtcTitle(rec: unknown, scope: { generation: string; module: string; namespace: string } | undefined): { title: string; language: string; page: number } | null;
  export function orderDtcRecords<T>(records: readonly T[] | null | undefined): T[];
  export function dtcBadgeCount(entry: Record<string, unknown> | undefined): number | null;
  export function mergeStatusAfterJob(
    prev: Record<string, unknown> | undefined,
    next: Record<string, unknown>,
    sessionTask: string,
    extras?: Record<string, unknown>,
  ): Record<string, unknown>;
  export function preClearSnapshot(final: unknown): { dtcCount: number; records?: unknown } | null;
  export function headerTaskState(opts: Record<string, unknown>): "idle" | "running" | "offline";
  export function headerTaskLabel(state: string): string;
  export function buildSessionStart(node: Record<string, unknown>, ctx: Record<string, unknown>): Record<string, unknown>;
  export function completionFeedback(opts: {
    results?: unknown[];
    adaptedQueued?: number;
    totalNodes?: number;
  }): string;
  export function failureReasonZh(error: unknown): string;
  export function prepareScanRetry(opts: {
    invoke: (req: Record<string, unknown>) => Promise<Record<string, unknown>>;
    connection?: (req: Record<string, unknown>) => Promise<Record<string, unknown>>;
    jobId?: string | null;
    mode: string;
    deviceId?: string | null;
    model?: string | null;
    cancelled?: () => boolean;
  }): Promise<{ ok: boolean; error?: string }>;
  export function buildExportReport(opts: Record<string, unknown>): Record<string, unknown>;
  export function createScanQueue(opts: {
    invoke: (req: Record<string, unknown>) => Promise<Record<string, unknown>>;
    pollMs?: number;
    sleep?: (ms: number) => Promise<void>;
    pollBudgetMs?: number;
    cancelWaitMs?: number;
  }): {
    readonly busy: boolean;
    readonly jobId: string | null;
    readonly cancelled: boolean;
    readonly stopRequested: boolean;
    stopAfterCurrent: () => boolean;
    cancel: () => Promise<void>;
    run: (opts: {
      nodes: unknown[];
      ctx: Record<string, unknown>;
      hooks?: {
        onKind?: (id: string, classified: Record<string, unknown>) => void;
        onStatus?: (doc: Record<string, unknown>) => void;
        onJob?: (jobId: string) => void;
        onInterrupted?: (checkpoint: { row: { jobId?: string; classified: Record<string, unknown> }; remaining: unknown[]; readback?: boolean; canRetry: boolean }) => Promise<"retry" | "terminate">;
        statusOf?: (id: string) => Record<string, unknown> | undefined;
      };
    }) => Promise<{ error: string | null; stopped: boolean; results: unknown[]; clearResults: unknown[]; clearOutcomeUnconfirmed: boolean; readbackStarted: boolean }>;
  };
}

declare module "@can-topology" {
  const value: {
    schemaVersion: number;
    sources: Array<{ id: string; file: string; modelYear?: string; pages?: number; sha256?: string }>;
    generations: unknown[];
  };
  export default value;
}

declare module "*read-only-session-logic.mjs";
declare module "*obd-connection-logic.mjs" {
  export const ELM_SERIAL_BAUDS: readonly number[];
  export const ELM_DEFAULT_BAUDS: Readonly<Record<string, number>>;
  export function validSerialBaud(value: unknown): boolean;
  export function supportsSerialBaud(deviceId: unknown, model: unknown): boolean;
  export function tcpEndpointId(host: string, port: number): string | null;
  export function parseTcpDeviceId(value: unknown): { host: string; port: number } | null;
  export function validObdDeviceId(value: unknown): boolean;
  export function formatHeaderVoltage(volts: number | null | undefined): string;
  export function parseVoltageVolts(text: string | null | undefined): number | null;
  export function voltageFresh(at: number, now: number, ttlMs: number): boolean;
  export function createTransportGate(): {
    tryAcquire(who: string): boolean;
    release(who: string): void;
    owner(): string | null;
  };
}
