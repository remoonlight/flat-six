export function createEngineAcquisition(): {
  append(final: object): boolean;
  readonly samples: { pid: string; cycle: number; elapsedMs?: number; capturedUtc?: string; value?: number; synthetic?: boolean }[];
  readonly count: number;
  hasRun(id: unknown): boolean;
  stop(reason: string): void;
  export(): { kind: string; startedAt: string; endedAt: string; stopReason: string | null; samples: object[]; runs: object[]; simulation: boolean; liveVerified: boolean };
};
