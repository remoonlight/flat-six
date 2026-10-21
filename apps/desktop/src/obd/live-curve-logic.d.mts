export type CurvePoint = { elapsedMs: number; value: number; timestamp?: string };
export type CurveSeries = { id: string; name: string; unit: string | null; latest: string; latestValue?: number; latestSuffix?: string; timestamp?: string; segments: CurvePoint[][] };
export type CurveGroup = { key: string; label: string; unit: string; series: CurveSeries[]; windowEndMs?: number };
export function groupLiveCurves(series: CurveSeries[], currentElapsedMs?: number | null): CurveGroup[];
export function liveCurveGeometry(group: CurveGroup): null | {
  min: number; max: number; start: number; end: number;
  series: (Omit<CurveSeries, "segments"> & { segments: (CurvePoint & { x: number; y: number })[][] })[];
};
