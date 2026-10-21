import { randomUUID } from "node:crypto";
import { open, link, unlink } from "node:fs/promises";
import path from "node:path";
import { ENGINE_PID_META } from "../src/read-only-session-logic.mjs";
import { mainlandParameter } from "../src/obd/live-unit-logic.mjs";

// PCAPNG Upper PDU, tag 12 selects Wireshark's registered json dissector.
// https://www.wireshark.org/docs/wsar_html/exported__pdu__tlvs_8h.html
// https://github.com/wireshark/wireshark/blob/master/epan/dissectors/packet-json.c
// These are decoded parameter observations, never reconstructed CAN or IP traffic.
const end = Buffer.alloc(4), upperJson = Buffer.from([0, 12, 0, 4, 106, 115, 111, 110, 0, 0, 0, 0]);
const pad = data => Buffer.concat([data, Buffer.alloc((4 - data.length % 4) % 4)]);
function block(type, body) {
  const header = Buffer.alloc(8), trailer = Buffer.alloc(4), length = body.length + 12;
  header.writeUInt32LE(type); header.writeUInt32LE(length, 4); trailer.writeUInt32LE(length);
  return Buffer.concat([header, body, trailer]);
}
function option(code, value) {
  const data = Buffer.isBuffer(value) ? value : Buffer.from(value), header = Buffer.alloc(4);
  header.writeUInt16LE(code); header.writeUInt16LE(data.length, 2);
  return Buffer.concat([header, pad(data)]);
}
const fail = error => ({ ok: false, saved: false, error });
const text = (value, limit) => typeof value === "string" && value.length > 0 && Buffer.byteLength(value) <= limit;

export function parameterTimestampNs(timestamp) {
  if (typeof timestamp !== "string") throw new Error("parameter_timestamp_invalid");
  const match = /^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})(?:\.(\d{1,9}))?(Z|[+-]\d{2}:\d{2})$/.exec(timestamp);
  if (!match) throw new Error("parameter_timestamp_invalid");
  const local = new Date(match[1] + "Z"), milliseconds = Date.parse(match[1] + match[3]);
  if (!Number.isFinite(milliseconds) || !Number.isFinite(local.getTime()) || local.toISOString().slice(0, 19) !== match[1])
    throw new Error("parameter_timestamp_invalid");
  const stamp = BigInt(milliseconds) * 1000000n + BigInt((match[2] || "").padEnd(9, "0"));
  if (stamp < 0n || stamp > 0xffffffffffffffffn) throw new Error("parameter_timestamp_invalid");
  return stamp;
}

function parameters(recording) {
  if (recording?.retentionComplete !== true || typeof recording.simulation !== "boolean") throw new Error("invalid_recording");
  if (recording.kind === "engine-acquisition-batch" && Array.isArray(recording.samples))
    return { values: recording.samples, count: recording.samples.length, ecu: "DME", standard: true };
  if (["manufacturer-acquisition-read", "manufacturer-acquisition-rehearsal"].includes(recording.kind)
    && Number.isSafeInteger(recording.sampleCount) && recording.sampleCount >= 0
    && text(recording.profileId, 512) && typeof recording.samples?.[Symbol.iterator] === "function") {
    const unit = recording.profileId.split(":")[1] || recording.profileId;
    return { values: recording.samples, count: recording.sampleCount, ecu: unit === "CAN_CAN_Gateway" ? "Gateway" : unit, standard: false };
  }
  // Unqualified internal CAN bytes cannot be relabeled as decoded parameters.
  if (recording.kind === "internal-parameter-acquisition" && recording.simulation === false
    && recording.decodedSourceQualified === true && ["drive", "chassis", "comfort", "crash", "adas"].includes(recording.canNetwork)
    && Number.isSafeInteger(recording.sampleCount) && recording.sampleCount > 0
    && typeof recording.samples?.[Symbol.iterator] === "function")
    return { values: recording.samples, count: recording.sampleCount, ecu: `CAN-${recording.canNetwork}`, standard: false, internal: true };
  if (recording.kind === "internal-can-acquisition") throw new Error("parameter_data_unavailable");
  throw new Error("invalid_recording");
}

function project(sample, standard, simulation, internal = false) {
  if (!sample || typeof sample !== "object" || sample.synthetic !== undefined && sample.synthetic !== simulation)
    throw new Error("parameter_sample_invalid");
  const meta = standard ? ENGINE_PID_META[sample.pid] : null;
  if (standard && (!meta || sample.unit !== undefined && ![meta.unit, meta.unit === "°C" ? "C" : meta.unit].includes(sample.unit)))
    throw new Error("parameter_sample_invalid");
  const name = standard ? meta.label : sample.name;
  const unit = standard ? meta.unit : sample.unit;
  let value = standard || internal || sample.numeric === true ? sample.value : sample.display;
  if (!standard && sample.numeric === true && typeof value === "string"
    && /^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:e[+-]?\d+)?$/i.test(value.trim())) value = Number(value);
  if ((standard || internal || sample.numeric === true) && (typeof value !== "number" || !Number.isFinite(value)))
    throw new Error("parameter_sample_invalid");
  const capturedUtc = standard || internal ? sample.capturedUtc : sample.processedUtc;
  if (!text(name, 4096) || unit !== null && (typeof unit !== "string" || Buffer.byteLength(unit) > 256)
    || !(typeof value === "number" && Number.isFinite(value) || typeof value === "boolean"
      || typeof value === "string" && Buffer.byteLength(value) <= 65536))
    throw new Error("parameter_sample_invalid");
  const timestampNs = parameterTimestampNs(capturedUtc);
  const converted = mainlandParameter(value, unit);
  return { data: { name, value: converted.value, unit: converted.unit, capturedUtc }, timestampNs };
}

/** Main-owned batch only. Bounded writes, complete-count validation and no overwrite. */
export async function saveParameterRecording(recording, {
  desktopPath, openFile = open, linkFile = link, unlinkFile = unlink,
  nowFn = () => new Date(), uuidFn = randomUUID,
}) {
  let file, temporaryPath, owned = false;
  try {
    const source = parameters(recording);
    if (!source.count) return fail("parameter_data_unavailable");
    const desktop = typeof desktopPath === "function" ? desktopPath() : desktopPath;
    if (typeof desktop !== "string" || !path.isAbsolute(desktop)) return fail("desktop_path_unavailable");
    const ecu = source.ecu.replace(/[^a-zA-Z0-9_-]/g, "_").slice(0, 60) || "ECU";
    const started = recording.startedUtc || recording.startedAt;
    const captureDate = typeof started === "string" && Number.isFinite(Date.parse(started)) ? new Date(started) : nowFn();
    const date = captureDate.toISOString().replace(/[-:]/g, "").replace(/\.\d{3}Z$/, "Z");
    const stem = `${recording.simulation ? "SYNTHETIC-" : ""}${ecu}-${date}-parameters`;
    temporaryPath = path.join(desktop, `.flat-six-parameters-${uuidFn()}.tmp`);
    file = await openFile(temporaryPath, "wx"); owned = true;
    async function put(data) {
      let offset = 0;
      while (offset < data.length) {
        const { bytesWritten } = await file.write(data, offset, data.length - offset, null);
        if (!Number.isInteger(bytesWritten) || bytesWritten <= 0 || bytesWritten > data.length - offset)
          throw new Error("parameter_recording_short_write");
        offset += bytesWritten;
      }
    }
    const section = Buffer.alloc(16); section.writeUInt32LE(0x1a2b3c4d); section.writeUInt16LE(1, 4); section.writeBigInt64LE(-1n, 8);
    await put(block(0x0a0d0d0a, Buffer.concat([section, option(1,
      `${recording.simulation ? "SYNTHETIC; not vehicle evidence. " : ""}FlatSix decoded parameter data only; names, values, units and host observation timestamps. Not captured CAN/IP traffic. Complete retained parameter batch.`), end])));
    const description = Buffer.alloc(8); description.writeUInt16LE(252); description.writeUInt32LE(1048576, 4);
    await put(block(1, Buffer.concat([description, option(2, "decoded parameters"), option(9, Buffer.from([9])), end])));
    let count = 0, lastTimestamp = 0n;
    for (const sample of source.values) {
      if (count >= source.count) throw new Error("parameter_count_mismatch");
      const { data, timestampNs } = project(sample, source.standard, recording.simulation, source.internal);
      const packet = Buffer.concat([upperJson, Buffer.from(JSON.stringify(data))]), header = Buffer.alloc(20);
      header.writeUInt32LE(Number(timestampNs >> 32n), 4); header.writeUInt32LE(Number(timestampNs & 0xffffffffn), 8);
      header.writeUInt32LE(packet.length, 12); header.writeUInt32LE(packet.length, 16);
      await put(block(6, Buffer.concat([header, pad(packet)]))); count++; lastTimestamp = timestampNs;
    }
    if (count !== source.count) throw new Error("parameter_count_mismatch");
    const statistics = Buffer.alloc(12), received = Buffer.alloc(8);
    statistics.writeUInt32LE(Number(lastTimestamp >> 32n), 4); statistics.writeUInt32LE(Number(lastTimestamp & 0xffffffffn), 8);
    received.writeBigUInt64LE(BigInt(count));
    await put(block(5, Buffer.concat([statistics, option(4, received), end])));
    await file.sync(); await file.close(); file = null;
    // A same-directory hard link publishes a completed file without replacing
    // any existing name. Failed or partial writes never acquire a final name.
    let filePath;
    for (let attempt = 0; attempt < 8; attempt++) {
      filePath = path.join(desktop, `${stem}${attempt ? "-" + uuidFn() : ""}.pcapng`);
      try { await linkFile(temporaryPath, filePath); break; }
      catch (error) { if (error.code !== "EEXIST" || attempt === 7) throw error; }
    }
    try { await unlinkFile(temporaryPath); owned = false; } catch { /* Valid final file is already published; retry temporary cleanup below. */ }
    return { ok: true, saved: true, filePath, sampleCount: count, format: "parameter-data-pcapng" };
  } catch (error) { return fail(error.code || error.message || "parameter_recording_save_failed"); }
  finally {
    if (file) try { await file.close(); } catch { /* Preserve the original failure. */ }
    if (owned) try { await unlinkFile(temporaryPath); } catch { /* Never publish an incomplete temporary file. */ }
  }
}

/** Reveal only a file saved by this service for the requesting window. */
export function createParameterRecordingService(options) {
  const saved = new Map();
  return {
    async save(recording, owner) {
      if (!Number.isInteger(owner) || owner < 1) return fail("parameter_recording_owner_invalid");
      const result = await saveParameterRecording(recording, options);
      if (!result.saved) return result;
      const saveId = randomUUID(); saved.set(saveId, { owner, path: result.filePath });
      const own = [...saved].filter(([, value]) => value.owner === owner);
      for (const [id] of own.slice(0, Math.max(0, own.length - 20))) saved.delete(id);
      return { ...result, saveId };
    },
    reveal(request, owner) {
      if (!request || typeof request !== "object" || Array.isArray(request) || Object.keys(request).length !== 1 || typeof request.saveId !== "string"
        || saved.get(request.saveId)?.owner !== owner) return { ok: false, error: "parameter_recording_not_owned" };
      try { options.revealFile(saved.get(request.saveId).path); return { ok: true }; }
      catch (error) { return { ok: false, error: error.code || "parameter_recording_reveal_failed" }; }
    },
    clearOwner(owner) { for (const [id, value] of saved) if (value.owner === owner) saved.delete(id); },
  };
}
