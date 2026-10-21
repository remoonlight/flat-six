import { mainlandUnit, mainlandParameter, mainlandValueText } from "./live-unit-logic.mjs";

export function groupLiveCurves(series, currentElapsedMs = null) {
  const groups = new Map();
  for (const item of series) {
    let target = mainlandUnit(item.unit);
    const overflow = item.segments.some(segment => segment.some(point => Number.isFinite(point.value)
      && mainlandParameter(point.value, item.unit).unit !== target.unit));
    if (overflow) target = { type: null, unit: item.unit, scale: 1, offset: 0 };
    const unit = target.unit || "", type = target.type;
    const key = type ? `${type}:${unit}` : `parameter:${item.id}`;
    if (!groups.has(key)) groups.set(key, { key, label: type || item.name, unit, series: [] });
    const latest = !overflow && Number.isFinite(item.latestValue) ? mainlandValueText(item.latestValue, item.unit).text + (item.latestSuffix || "") : item.latest;
    groups.get(key).series.push({ ...item, unit, latest,
      segments: item.segments.map(segment => segment.map(point => ({ ...point,
        value: overflow ? point.value : mainlandParameter(point.value, item.unit).value,
      }))),
    });
  }
  // Every type belongs to the same batch clock. An old type must not keep a
  // separate window ending at its last sample while another type advances.
  let windowEndMs = Number.isFinite(currentElapsedMs) && currentElapsedMs >= 0 ? currentElapsedMs : -Infinity;
  for (const item of series) for (const segment of item.segments) for (const point of segment) {
    if (Number.isFinite(point.elapsedMs)) windowEndMs = Math.max(windowEndMs, point.elapsedMs);
  }
  return [...groups.values()].map(group => ({ ...group, windowEndMs }));
}

export function liveCurveGeometry(group) {
  const series = group.series.map(item => ({ ...item,
    segments: item.segments.flatMap(segment => {
      // An invalid observation breaks the line; never join across it.
      const valid = []; let current = [];
      for (const point of segment) {
        if (!Number.isFinite(point.elapsedMs) || !Number.isFinite(point.value)) {
          if (current.length) valid.push(current);
          current = [];
        } else {
          if (current.length && point.elapsedMs < current.at(-1).elapsedMs) { valid.push(current); current = []; }
          current.push(point);
        }
      }
      if (current.length) valid.push(current);
      return valid;
    }),
  }));
  const points = series.flatMap(item => item.segments.flat());
  if (!points.length) return null;
  let end = -Infinity;
  for (const point of points) end = Math.max(end, point.elapsedMs);
  if (Number.isFinite(group.windowEndMs)) end = Math.max(end, group.windowEndMs);
  const start = Math.max(0, end - 60000);
  // Keep the complete recording unchanged; only the visible window is filtered.
  for (const item of series) item.segments = item.segments
    .map(segment => segment.filter(point => point.elapsedMs >= start && point.elapsedMs <= end))
    .filter(segment => segment.length);
  let low = Infinity, high = -Infinity;
  for (const item of series) for (const segment of item.segments) for (const point of segment) {
    low = Math.min(low, point.value); high = Math.max(high, point.value);
  }
  if (!Number.isFinite(low) || !Number.isFinite(high)) return null;
  const padding = high === low ? Math.max(1, Math.abs(high) * .05) : (high - low) * .1;
  const min = low - padding, max = high + padding;
  return { min, max, start, end, series: series.map(item => ({ ...item,
    segments: item.segments.map(segment => segment.map(point => ({ ...point,
      x: 76 + (end === start ? 0 : (point.elapsedMs - start) / (end - start) * 374),
      y: 142 - (point.value - min) / (max - min) * 120,
    }))),
  })) };
}
