import { randomUUID } from "node:crypto";
import { basename } from "node:path";
import { Worker } from "node:worker_threads";
import { validCanFrame } from "./internal-can-data.mjs";
import { validConnectionEvent } from "./manufacturer-connection.mjs";

const KINDS = new Set(["engine-acquisition-batch", "manufacturer-acquisition-read",
  "manufacturer-acquisition-rehearsal", "internal-can-acquisition"]);
const fail = error => ({ ok: false, error });
const text = value => typeof value === "string" ? value : "";

/** Saved values are displayed as recorded, never fed into a vehicle transport or decoder. */
export function prepareReplay(doc, fileName) {
  if (!doc || !KINDS.has(doc.kind)) throw new Error("replay_unsupported_format");
  const internal = doc.kind === "internal-can-acquisition", engine = doc.kind === "engine-acquisition-batch";
  const input = internal ? doc.frames : doc.samples;
  const connectionEvents = [];
  if (doc.connectionEvents !== undefined || doc.connectionEventCount !== undefined) {
    if (internal || engine || !Array.isArray(doc.connectionEvents)
      || !Number.isSafeInteger(doc.connectionEventCount) || doc.connectionEventCount !== doc.connectionEvents.length)
      throw new Error("replay_invalid_connection_events");
    const live = doc.kind === "manufacturer-acquisition-read";
    if (doc.connectionEvents.length && (doc.simulation !== !live
      || doc.sourceKind !== (live ? "independent-manufacturer-read" : "historical-fixture-rehearsal")))
      throw new Error("replay_invalid_connection_events");
    for (const event of doc.connectionEvents) {
      if (!validConnectionEvent(event, { live, connectionEvents })) throw new Error("replay_invalid_connection_events");
      connectionEvents.push({ ...event, timeMs: event.elapsedMs });
    }
  }
  if (!Array.isArray(input) || !input.length && !connectionEvents.length) throw new Error("replay_no_samples");
  for (const key of internal ? ["frameCount", "retainedFrames", "receivedTotal"] : ["sampleCount"])
    if (doc[key] !== undefined && doc[key] !== input.length) throw new Error("replay_count_mismatch");
  if (doc.cycleCount !== undefined && (!Array.isArray(doc.cycleResults) || doc.cycleResults.length !== doc.cycleCount))
    throw new Error("replay_count_mismatch");
  const parameters = new Map(), rows = [];
  let min = connectionEvents[0]?.elapsedMs ?? Infinity, max = connectionEvents.at(-1)?.elapsedMs ?? -Infinity;
  let priorTime = -Infinity, regressed = false;
  for (let i = 0; i < input.length; i++) {
    const sample = input[i];
    if (!sample || typeof sample !== "object") throw new Error("replay_invalid_sample");
    const id = internal ? `${sample.extended ? "extended" : "standard"}:${sample.canId}` : engine ? sample.pid : sample.parameterId;
    const at = internal ? sample.timestampUs / 1000 : sample.elapsedMs;
    const value = internal ? sample.dataHex : sample.value;
    if (internal && !validCanFrame(sample) || typeof id !== "string" || !id.length || id.length > 256
      || !Number.isFinite(at) || !["string", "number"].includes(typeof value)
      || typeof value === "number" && !Number.isFinite(value)) throw new Error("replay_invalid_sample");
    if (!internal && (!Number.isSafeInteger(sample.cycle) || sample.cycle < 1
      || sample.connectionEpoch !== undefined && (!Number.isSafeInteger(sample.connectionEpoch) || sample.connectionEpoch < 1)
      || sample.display !== undefined && !["string", "number"].includes(typeof sample.display))) throw new Error("replay_invalid_sample");
    const unit = internal ? "" : text(sample.unit), name = internal ? `CAN ${sample.extended ? "扩展" : "标准"} ${sample.canId.toString(16).toUpperCase().padStart(sample.extended ? 8 : 3, "0")}` : text(sample.name || sample.label) || id;
    const param = parameters.get(id) || { id, name, unit, count: 0, points: [] };
    if (param.name !== name || param.unit !== unit) throw new Error("replay_parameter_conflict");
    const row = { index: i, parameterId: id, name, unit, value,
      display: internal ? sample.dataHex : sample.display === undefined ? String(value) : String(sample.display),
      numeric: !internal && typeof value === "number" && sample.numeric !== false,
      timeMs: at, sourceElapsedMs: internal ? null : at,
      timestamp: internal ? new Date(at).toISOString() : text(sample.capturedUtc || sample.processedUtc),
      ...(internal ? { timestampUs: sample.timestampUs } : {}),
      timestampKind: text(sample.timestampSource) || (internal ? sample.timestampSource : sample.capturedUtc ? "capturedUtc" : "processedUtc"),
      ...(sample.connectionEpoch !== undefined ? { connectionEpoch: sample.connectionEpoch } : {}),
      cycle: internal ? null : sample.cycle, synthetic: sample.synthetic === true,
      sourceKind: text(sample.sourceKind || doc.sourceKind) };
    if (!internal && (!row.timestamp || !Number.isFinite(Date.parse(row.timestamp)))) throw new Error("replay_invalid_timestamp");
    if (at < priorTime) regressed = true;
    priorTime = at; min = Math.min(min, at); max = Math.max(max, at);
    param.count++; param.points.push(row); parameters.set(id, param); rows.push(row);
  }
  if (!Number.isFinite(max - min)) throw new Error("replay_invalid_sample");
  for (const row of rows) row.timeMs -= min;
  for (const event of connectionEvents) event.timeMs -= min;
  for (const param of parameters.values()) {
    param.points.sort((a, b) => a.timeMs - b.timeMs || a.index - b.index);
    param.numericPoints = param.points.filter(p => p.numeric);
    param.overview = overview(param.numericPoints);
  }
  return { rows, parameters, connectionEvents, summary: { fileName: basename(fileName), kind: doc.kind,
    sourceKind: text(doc.sourceKind), profileId: text(doc.profileId), simulation: doc.simulation === true,
    vehicleDataCollected: doc.vehicleDataCollected === true, retentionComplete: doc.retentionComplete === true,
    timeRegressed: regressed, sampleCount: rows.length, durationMs: max - min,
    connectionEventCount: connectionEvents.length, sourceStartElapsedMs: internal ? null : min,
    startedUtc: text(doc.startedUtc || doc.startedAt), endedUtc: text(doc.finishedUtc || doc.endedAt),
    stopReason: text(doc.termination?.stopReason || doc.stopReason), error: text(doc.termination?.error),
    parameters: [...parameters.values()].map(({ id, name, unit, count }) => ({ id, name, unit, count })) } };
}

function upper(points, position) {
  let lo = 0, hi = points.length;
  while (lo < hi) { const mid = (lo + hi) >>> 1; if (points[mid].timeMs <= position) lo = mid + 1; else hi = mid; }
  return lo;
}

/** Min/max envelopes retain spikes; table paging always exposes every original row. */
function overview(points) {
  const numeric = points.filter(p => p.numeric);
  if (numeric.length <= 600) return numeric;
  const bins = new Map(), span = numeric.at(-1).timeMs - numeric[0].timeMs;
  for (const point of numeric) {
    const key = Math.min(298, span ? Math.floor((point.timeMs - numeric[0].timeMs) / span * 299) : 0);
    const bin = bins.get(key) || { low: point, high: point };
    if (point.value < bin.low.value) bin.low = point;
    if (point.value > bin.high.value) bin.high = point;
    bins.set(key, bin);
  }
  const kept = new Map([numeric[0], numeric.at(-1)].map(p => [p.index, p]));
  for (const bin of bins.values()) for (const p of [bin.low, bin.high]) kept.set(p.index, p);
  return [...kept.values()].sort((a, b) => a.timeMs - b.timeMs || a.index - b.index);
}

export function replayView(record, request) {
  const { parameterIds, positionMs, offset = 0, seriesOffset = 0, connectionOffset = 0 } = request;
  if (!Array.isArray(parameterIds) || new Set(parameterIds).size !== parameterIds.length
    || parameterIds.some(id => !record.parameters.has(id)) || !Number.isFinite(positionMs) || positionMs < 0
    || positionMs > record.summary.durationMs || !Number.isSafeInteger(offset) || offset < 0
    || !Number.isSafeInteger(seriesOffset) || seriesOffset < 0 || seriesOffset > parameterIds.length
    || !Number.isSafeInteger(connectionOffset) || connectionOffset < 0 || connectionOffset > record.connectionEvents.length) return fail("replay_invalid_view");
  const key = JSON.stringify(parameterIds);
  if (record.selection?.key !== key) {
    const ids = new Set(parameterIds);
    record.selection = { key, rows: record.rows.filter(row => ids.has(row.parameterId)) };
  }
  const selected = record.selection.rows;
  if (offset > selected.length) return fail("replay_invalid_view");
  const rows = []; let bytes = 0;
  for (let i = offset; i < selected.length && rows.length < 100; i++) {
    const row = selected[i], size = Buffer.byteLength(JSON.stringify(row));
    if (rows.length && bytes + size > 128 * 1024) break;
    rows.push(row); bytes += size;
  }
  const series = parameterIds.slice(seriesOffset, seriesOffset + 12).map(id => {
    const param = record.parameters.get(id), end = upper(param.points, positionMs);
    const current = param.points[end - 1] || null, points = param.overview.filter(p => p.timeMs <= positionMs);
    if (current?.numeric && !points.some(p => p.index === current.index)) points.push(current);
    return { id, name: param.name, unit: param.unit, current,
      numericCount: upper(param.numericPoints, positionMs), plottedCount: points.length, points };
  });
  const connectionEvents = record.connectionEvents.slice(connectionOffset, connectionOffset + 100);
  const connectionEnd = upper(record.connectionEvents, positionMs);
  return { ok: true, rows, offset, nextOffset: offset + rows.length, total: selected.length, series, positionMs,
    connectionEvents, connectionOffset, nextConnectionOffset: connectionOffset + connectionEvents.length,
    connectionTotal: record.connectionEvents.length, currentConnectionEvent: record.connectionEvents[connectionEnd - 1] || null,
    seriesOffset, nextSeriesOffset: seriesOffset + series.length, seriesTotal: parameterIds.length };
}

/** Native file choice only. Each window owns one volatile recording; no writes or devices. */
export function createAcquisitionReplay({ dialog, readFile, windowFor }) {
  const owned = new Map(), opening = new Map();
  function createReader() {
    const worker = new Worker(new URL("./acquisition-replay-worker.mjs", import.meta.url));
    const pending = new Map(); let serial = 0, closed = false;
    const abort = () => { closed = true; for (const resolve of pending.values()) resolve(fail("replay_reader_closed")); pending.clear(); };
    worker.on("message", message => {
      const resolve = pending.get(message.id); if (!resolve) return;
      pending.delete(message.id); resolve(message.result); if (!pending.size) worker.unref();
    });
    worker.on("error", abort); worker.on("exit", abort);
    return { call(request) {
      if (closed) return Promise.resolve(fail("replay_reader_closed"));
      worker.ref(); const id = ++serial;
      return new Promise(resolve => { pending.set(id, resolve); worker.postMessage({ id, request }); });
    }, stop() { abort(); void worker.terminate(); } };
  }
  return {
    clearOwned(owner) { owned.get(owner)?.reader.stop(); opening.get(owner)?.reader?.stop(); owned.delete(owner); opening.delete(owner); },
    async handle(request, owner) {
      if (!request || typeof request !== "object" || Array.isArray(request)) return fail("replay_invalid_request");
      const allowed = request.action === "view" ? ["action", "recordingId", "parameterIds", "positionMs", "offset", "seriesOffset", "connectionOffset"]
        : request.action === "discard" ? ["action", "recordingId"] : request.action === "open" ? ["action"] : [];
      if (!allowed.length || Object.keys(request).some(key => !allowed.includes(key))) return fail("replay_invalid_request");
      if (request.action === "open") {
        if (opening.has(owner)) return fail("replay_busy");
        const token = {}; opening.set(owner, token); let retained = false;
        try {
          const options = { title: "打开采集记录", properties: ["openFile"], filters: [{ name: "采集记录 JSON", extensions: ["json"] }] };
          const window = windowFor?.(owner), choice = window ? await dialog.showOpenDialog(window, options) : await dialog.showOpenDialog(options);
          if (choice.canceled || !choice.filePaths?.length) return { ok: true, canceled: true };
          const request = { action: "load", fileName: choice.filePaths[0] };
          if (readFile) request.text = await readFile(choice.filePaths[0], "utf8");
          if (opening.get(owner) !== token) return fail("replay_window_closed");
          token.reader = createReader();
          const result = await token.reader.call(request);
          if (opening.get(owner) !== token) return fail("replay_window_closed");
          if (!result.ok) return result;
          owned.get(owner)?.reader.stop();
          const record = { id: randomUUID(), reader: token.reader }; owned.set(owner, record); retained = true;
          return { ok: true, recordingId: record.id, summary: result.summary };
        } catch (error) { return fail(error instanceof SyntaxError ? "replay_invalid_json" : error?.code || error?.message || "replay_open_failed"); }
        finally { if (!retained) token.reader?.stop(); if (opening.get(owner) === token) opening.delete(owner); }
      }
      const record = owned.get(owner);
      if (!record || request.recordingId !== record.id) return fail("replay_not_found");
      if (request.action === "discard") { record.reader.stop(); owned.delete(owner); return { ok: true }; }
      return record.reader.call(request);
    },
  };
}
