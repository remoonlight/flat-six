export function createEngineAcquisition(options?: { limit?: number; byteLimit?: number }): {
  append(final: object): boolean;
  readonly samples: { pid: string; cycle: number; elapsedMs?: number; capturedUtc?: string; value?: number; synthetic?: boolean }[];
  readonly full: boolean; readonly count: number;
  hasRun(id: unknown): boolean;
  canFit(count: number): boolean;
  export(): { kind: string; startedAt: string; endedAt: string; samples: object[]; runs: object[]; simulation: boolean; liveVerified: boolean };
};
