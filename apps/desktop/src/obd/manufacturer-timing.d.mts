type Observation = { elapsedMs: number; connectionEpoch?: number; numeric: boolean; value: string | number };
export function manufacturerConnectionMessage(event: { phase: string; attempt: number; reason: string | null } | null, finished?: boolean): string;
export function manufacturerTiming<T extends Observation>(series: T[]): {
  segments: T[][]; points: T[]; intervalMs: number | null; recovered: boolean; interruptions: number;
};
