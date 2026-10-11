import { validCanFrame } from "./internal-can-data.mjs";

/** Only a native Save As choice grants a destination; renderer paths are rejected. */
export async function saveRecordingFile(input, { dialog, writeFile, window }) {
  try {
    if (!input || typeof input !== "object" || Array.isArray(input)
      || Object.keys(input).some((key) => !["fileName", "recording"].includes(key))
      || typeof input.fileName !== "string" || !/^[a-zA-Z0-9_-]{1,120}\.json$/.test(input.fileName)
      || !input.recording || typeof input.recording !== "object" || Array.isArray(input.recording)) {
      return { ok: false, saved: false, error: "invalid_recording" };
    }
    const json = JSON.stringify(input.recording) + "\n";
    if (Buffer.byteLength(json, "utf8") > 8 * 1024 * 1024) return { ok: false, saved: false, error: "recording_too_large" };
    const options = { title: "保存此次采集", defaultPath: input.fileName,
      filters: [{ name: "采集记录 JSON", extensions: ["json"] }] };
    const chosen = window ? await dialog.showSaveDialog(window, options) : await dialog.showSaveDialog(options);
    if (chosen.canceled || !chosen.filePath) return { ok: true, saved: false, canceled: true };
    await writeFile(chosen.filePath, json, "utf8");
    return { ok: true, saved: true, filePath: chosen.filePath };
  } catch (error) { return { ok: false, saved: false, error: error?.code || "recording_save_failed" }; }
}

/** Main-owned frame iterator only, never exposed as a renderer serialization API.
 * Packets stay in volatile memory until Save As; write bounded chunks without
 * materializing the entire batch or applying the ordinary IPC JSON size cap. */
export async function saveInternalCanRecording(recording, { dialog, openFile, window }) {
    if (recording?.kind !== "internal-can-acquisition" || !Number.isSafeInteger(recording.frameCount)
      || recording.frameCount < 1 || recording.retainedFrames !== recording.frameCount
      || recording.receivedTotal !== recording.frameCount || recording.retentionComplete !== true
      || !recording.frames || typeof recording.frames[Symbol.iterator] !== "function")
      return { ok: false, saved: false, error: "invalid_recording" };
    const { frames, ...summary } = recording;
    return saveArrayRecording(summary, [{ key: "frames", values: frames, count: recording.frameCount, valid: validCanFrame,
      error: "recording-frame-mismatch" }], "internal-can-result.json", { dialog, openFile, window });
}

/** Standard samples and finite worker results are owned and validated by main.
 * A renderer can request saving its batch ID, never submit large arrays or paths. */
export async function saveEngineRecording(recording, deps) {
  if (recording?.kind !== "engine-acquisition-batch" || recording.retentionComplete !== true
    || !Array.isArray(recording.samples) || !recording.samples.length || !Array.isArray(recording.runs))
    return { ok: false, saved: false, error: "invalid_recording" };
  const { samples, runs, ...summary } = recording;
  return saveArrayRecording(summary, [
    { key: "samples", values: samples, count: samples.length, valid: value => value && typeof value.pid === "string" },
    { key: "runs", values: runs, count: runs.length, valid: value => value && typeof value.runId === "string" },
  ], "engine-session.json", deps);
}

/** Main-owned full manufacturer cycle stream, including pages never displayed. */
export async function saveManufacturerRecording(recording, deps) {
  if (!["manufacturer-acquisition-read", "manufacturer-acquisition-rehearsal"].includes(recording?.kind)
    || recording.retentionComplete !== true || !Number.isSafeInteger(recording.sampleCount) || recording.sampleCount < 0
    || !Number.isSafeInteger(recording.cycleCount) || recording.cycleCount < 0
    || (!recording.sampleCount && !(recording.connectionEventCount > 0))
    || (recording.sampleCount > 0 && recording.cycleCount < 1)
    || recording.connectionEvents !== undefined && (!Number.isSafeInteger(recording.connectionEventCount)
      || recording.connectionEventCount < 0 || typeof recording.connectionEvents?.[Symbol.iterator] !== "function")
    || recording.connectionEventCount !== undefined && recording.connectionEvents === undefined
    || typeof recording.samples?.[Symbol.iterator] !== "function" || typeof recording.cycleResults?.[Symbol.iterator] !== "function")
    return { ok: false, saved: false, error: "invalid_recording" };
  const { samples, cycleResults, connectionEvents, ...summary } = recording;
  return saveArrayRecording(summary, [
    { key: "samples", values: samples, count: recording.sampleCount, valid: value => value && typeof value.parameterId === "string" },
    { key: "cycleResults", values: cycleResults, count: recording.cycleCount, valid: value => value && Number.isInteger(value.cycle) },
    ...(connectionEvents === undefined ? [] : [{ key: "connectionEvents", values: connectionEvents, count: recording.connectionEventCount,
      valid: value => value && Number.isSafeInteger(value.sequence) && value.sequence > 0 }]),
  ], recording.simulation ? "manufacturer-rehearsal.json" : "manufacturer-read.json", deps);
}

async function saveArrayRecording(summary, arrays, fileName, { dialog, openFile, window }) {
  let file;
  try {
    const metadata = JSON.stringify(summary);
    const options = { title: "保存此次采集", defaultPath: fileName,
      filters: [{ name: "采集记录 JSON", extensions: ["json"] }] };
    const chosen = window ? await dialog.showSaveDialog(window, options) : await dialog.showSaveDialog(options);
    if (chosen.canceled || !chosen.filePath) return { ok: true, saved: false, canceled: true };
    file = await openFile(chosen.filePath, "w");
    async function put(text) {
      const bytes = Buffer.from(text);
      let offset = 0;
      while (offset < bytes.length) {
        const { bytesWritten } = await file.write(bytes, offset, bytes.length - offset, null);
        if (!Number.isInteger(bytesWritten) || bytesWritten <= 0 || bytesWritten > bytes.length - offset)
          throw new Error("recording-short-write");
        offset += bytesWritten;
      }
    }
    await put(metadata.slice(0, -1));
    const counts = {};
    for (const { key, values, count: expected, valid, error = "recording-item-mismatch" } of arrays) {
      await put("," + JSON.stringify(key) + ":[");
      let count = 0, chunk = [], bytes = 0;
      for (const value of values) {
        if (!valid(value) || count >= expected) throw new Error(error);
        const item = (count ? "," : "") + JSON.stringify(value);
        chunk.push(item); bytes += Buffer.byteLength(item); count++;
        if (bytes >= 65536) { await put(chunk.join("")); chunk = []; bytes = 0; }
      }
      if (count !== expected) throw new Error(error);
      if (chunk.length) await put(chunk.join(""));
      await put("]");
      counts[key] = count;
    }
    await put("}\n");
    await file.close(); file = null;
    return { ok: true, saved: true, filePath: chosen.filePath,
      ...(counts.frames !== undefined ? { frameCount: counts.frames } : { sampleCount: counts.samples }),
      ...(counts.runs !== undefined ? { runCount: counts.runs } : {}),
      ...(counts.cycleResults !== undefined ? { cycleCount: counts.cycleResults } : {}) };
  } catch (error) {
    return { ok: false, saved: false, error: error?.code || error?.message || "recording_save_failed" };
  } finally {
    if (file) { try { await file.close(); } catch { /* Preserve the first failure; never claim a saved file. */ } }
  }
}
