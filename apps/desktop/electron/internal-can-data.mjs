import fs from "node:fs";
import path from "node:path";
import { validCaptureClock } from "./capture-clock.mjs";

// PCAPNG SHB/IDB/EPB/ISB; LINKTYPE_CAN_SOCKETCAN=227. The CAN header ID
// is big endian even in a little endian capture. Timestamps are host arrival.
// https://www.iana.org/assignments/pcap/pcap.xhtml
// https://github.com/pcapng/pcapng
const padding = (data) => Buffer.concat([data, Buffer.alloc((4 - data.length % 4) % 4)]);
function option(code, data) {
  const value = Buffer.isBuffer(data) ? data : Buffer.from(String(data));
  const header = Buffer.alloc(4); header.writeUInt16LE(code); header.writeUInt16LE(value.length, 2);
  return Buffer.concat([header, padding(value)]);
}
function block(type, body) {
  const header = Buffer.alloc(8), end = Buffer.alloc(4);
  header.writeUInt32LE(type); header.writeUInt32LE(body.length + 12, 4); end.writeUInt32LE(body.length + 12);
  return Buffer.concat([header, body, end]);
}
const endOptions = Buffer.alloc(4);
export function validCanFrame(frame) {
  return frame && Number.isSafeInteger(frame.canId) && frame.canId >= 0 && typeof frame.extended === "boolean" &&
    frame.canId <= (frame.extended ? 0x1fffffff : 0x7ff) && typeof frame.dataHex === "string" &&
    /^(?:[0-9A-F]{2}){0,8}$/.test(frame.dataHex) && Number.isSafeInteger(frame.timestampUs) && frame.timestampUs > 0 &&
    frame.timestampSource === "host-chunk-arrival" && validCaptureClock(frame);
}

/** Racelogic's documented 64-bit numbering, not a Vector DBC start bit.
 * RLVBCAN02 manual p11 and VBOX3_CAN_Format p2/p8: Motorola bit 63 is
 * byte 0's MSB, bit 0 is byte 7's LSB; Start Bit locates the signal's LSB.
 * Only classic eight-byte integer definitions are handled here. The caller
 * still owns source-layout and vehicle qualification; this grants neither. */
export function decodeRacelogicInteger(dataHex, definition) {
  const d = definition;
  const number = v => typeof v === "number" && Number.isFinite(v) ? v
    : typeof v === "string" && /^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?$/.test(v) ? Number(v) : NaN;
  if (!d || d.dlc !== 8 || !Number.isInteger(d.startBit) || d.startBit < 0
    || !Number.isInteger(d.length) || d.length < 1 || d.length > 53 || d.startBit + d.length > 64
    || !["Intel", "Motorola"].includes(d.byteOrder) || typeof d.signed !== "boolean")
    throw new Error("internal-signal-layout-invalid");
  if (typeof dataHex !== "string" || !/^[0-9A-F]{16}$/.test(dataHex)) throw new Error("internal-signal-data-invalid");
  const factor = number(d.factor), offset = number(d.offset);
  if (!Number.isFinite(factor) || !Number.isFinite(offset)) throw new Error("internal-signal-scale-invalid");
  const hex = d.byteOrder === "Intel" ? dataHex.match(/../g).reverse().join("") : dataHex;
  const bits = BigInt(d.length);
  let raw = BigInt(`0x${hex}`) >> BigInt(d.startBit) & ((1n << bits) - 1n);
  if (d.signed && (raw & (1n << (bits - 1n)))) raw -= 1n << bits;
  const value = Number(raw) * factor + offset;
  if (!Number.isFinite(value)) throw new Error("internal-signal-value-invalid");
  return { raw: Number(raw), value };
}

/** Representation belongs to the source, not the byte order. The ASL steering
 * channel uses sign/magnitude, unlike signed two's-complement REF integers. */
export function decodeInternalInteger(dataHex, definition) {
  const encoding = definition?.signEncoding;
  if (![undefined, "twos-complement", "sign-magnitude"].includes(encoding)
    || encoding === "sign-magnitude" && definition.signed !== true) throw new Error("internal-signal-layout-invalid");
  if (encoding !== "sign-magnitude" && definition?.divisor === undefined) return decodeRacelogicInteger(dataHex, definition);
  let raw = decodeRacelogicInteger(dataHex, { ...definition, signed: encoding === "sign-magnitude" ? false : definition.signed }).raw;
  if (encoding === "sign-magnitude") {
    const sign = 2 ** (definition.length - 1);
    raw = raw < sign ? raw : -(raw - sign);
  }
  const divisor = definition.divisor === undefined ? 1 : definition.divisor;
  if (typeof divisor !== "number" || !Number.isFinite(divisor) || divisor === 0) throw new Error("internal-signal-scale-invalid");
  const value = raw * Number(definition.factor) / divisor + Number(definition.offset);
  if (!Number.isFinite(value)) throw new Error("internal-signal-value-invalid");
  return { raw, value };
}

/** Main-owned definitions from exact producer/consumer bindings. REF rows retain
 * their independent preset match; ASL-only entries keep their original units.
 * Source qualification does not claim head or vehicle acceptance. */
export function loadInternalSignals(repoRoot, network, generation = "981") {
  const catalog = JSON.parse(fs.readFileSync(path.join(repoRoot, "data/seed/obd/internal-signals.v1.json"), "utf8"));
  if (catalog.schemaVersion !== 3 || catalog.qualification !== "cross-checked-source-definitions"
    || catalog.generation !== "981" || catalog.network !== "drive" || catalog.vehicleVerified !== false
    || catalog.sources?.racelogic?.sha256 !== "ea524a538fdfeeb08697863f01f29312c21e54fa343a0220ad8b7a4caa6b9371"
    || catalog.sources?.autosportlabs?.sha256 !== "a26b555e018d39ac46e9375b31bf64d14260ad4d481c22061d1e2bbf86340b7d"
    || catalog.sources?.consumer?.commit !== "fa6ecf6d71b88b9b2e751ef7745aaf4d892b53f2"
    || catalog.sources?.consumer?.sha256 !== "381b57388d30db326330cc7d83af894585c70133c148e7fbdfb82d7081118640"
    || !Array.isArray(catalog.entries) || catalog.entries.length !== 8) throw new Error("internal-signal-source-invalid");
  const names = new Set();
  const definitions = catalog.entries.map(entry => {
    if (entry.sourceKind === "autosportlabs-preset") {
      const steering = entry.nameEn === "Steering", brake = entry.nameEn === "Brake";
      const brakeOffsets = { Brake: 1, BrakeLF: 2, BrakeRF: 3, BrakeLR: 4, BrakeRR: 5 };
      const brakeOffset = Object.hasOwn(brakeOffsets, entry.nameEn) ? brakeOffsets[entry.nameEn] : null;
      const percentageBrake = entry.nameEn === "BrakeRR";
      const expected = steering ? { add: 0, bigEndian: false, bm: false, bus: 0, div: 30.2, filtId: 0,
        id: 159, idMask: 0, len: 2, max: 100, min: -100, mult: 1, nm: "Steering", offset: 2,
        prec: 0, sr: 50, subId: -1, type: 3, ut: "%" } : { add: 0, bigEndian: false, bm: false, bus: 0,
        div: 1.7, filtId: 0, id: 287, idMask: 0, len: 1, max: brake || percentageBrake ? 2000 : 1000,
        min: brake || percentageBrake ? 0 : -1000, mult: 1, nm: entry.nameEn,
        offset: brakeOffset, prec: 0, sr: 25, subId: -1, type: 0, ut: brake ? "PSI" : percentageBrake ? "%" : "" };
      const p = entry.presetChannel;
      if ((!steering && brakeOffset === null) || entry.id !== `broadcast-asl-981-${entry.nameEn.toLowerCase()}`
        || entry.name !== (steering ? "转向输入" : brake ? "制动输入" : `制动输入（${entry.nameEn}）`) || entry.refRow !== undefined || names.has(entry.nameEn) || !p
        || Object.keys(p).length !== Object.keys(expected).length
        || Object.entries(expected).some(([key, value]) => p[key] !== value)) throw new Error("internal-signal-source-invalid");
      names.add(entry.nameEn);
      // PSI is the producer's spelling of psi. The shared display/recording
      // conversion retains raw precision and uses kPa for both page and file.
      // Three source channels do not declare a unit. Keep it empty; their
      // neighbour's PSI calibration does not give them a pressure unit.
      const d = { id: entry.id, name: entry.name, nameEn: entry.nameEn, unit: brake ? "psi" : p.ut, canId: p.id,
        extended: false, dlc: 8, startBit: p.offset * 8, length: p.len * 8, signed: steering,
        ...(steering ? { signEncoding: "sign-magnitude" } : {}),
        byteOrder: "Intel", factor: p.mult, divisor: p.div, offset: p.add, sourceQualified: true };
      decodeInternalInteger("0000000000000000", d);
      return d;
    }
    const row = typeof entry.refRow === "string" ? entry.refRow.split(",") : [], p = entry.presetChannel;
    const [name, can, unit, start, width, offset, factor, , , sign, order, dlc, end] = row;
    const expected = {
      Engine_Speed: ["RPM", "rpm", "Engine_Speed,261,rpm,16,16,0,0.25,10000,0,Unsigned,Intel,8,"],
      Throttle_Position: ["TPS", "%", "Throttle_Position,261,%,8,8,0,0.4,102,0,Unsigned,Motorola,8,"],
    }[name];
    if (!expected || names.has(name) || row.length !== 13 || end !== "" || sign !== "Unsigned"
      || !["Intel", "Motorola"].includes(order) || dlc !== "8" || !p || p.nm !== expected[0]
      || entry.refRow !== expected[2] || unit !== expected[1] || p.ut !== (name === "Engine_Speed" ? "" : "%")
      || p.bus !== 0 || p.type !== 0 || p.bm !== false || p.idMask !== 0 || p.subId !== -1 || p.filtId !== 0
      || typeof p.bigEndian !== "boolean" || (p.bigEndian !== (order === "Motorola") && p.len !== 1)
      || !Number.isInteger(p.offset) || p.offset < 0 || !Number.isInteger(p.len) || p.len < 1 || p.offset + p.len > 8
      || ![p.mult, p.div, p.add].every(v => typeof v === "number" && Number.isFinite(v)) || p.div === 0
      || ![can, start, width, offset, factor].every(v => typeof v === "string" && /^[+-]?(?:\d+(?:\.\d*)?|\.\d+)$/.test(v))
      || p.id !== Number(can) || p.len * 8 !== Number(width) || p.add !== Number(offset) || p.mult / p.div !== Number(factor)
      || Number(start) !== (order === "Intel" ? p.offset * 8 : 64 - (p.offset + p.len) * 8)
      || entry.id !== `broadcast-vbox-981-${name.toLowerCase().replaceAll("_", "-")}` || entry.nameEn !== name
      || typeof entry.name !== "string" || !entry.name.trim())
      throw new Error("internal-signal-source-invalid");
    names.add(name);
    const d = { id: entry.id, name: entry.name, nameEn: name, unit, canId: Number(can), extended: false,
      dlc: 8, startBit: Number(start), length: Number(width), signed: false, byteOrder: order,
      factor: Number(factor), offset: Number(offset), sourceQualified: true };
    decodeInternalInteger("0000000000000000", d);
    return d;
  });
  return generation === catalog.generation && network === catalog.network ? definitions : [];
}

export function createCanPcapWriter(file, network, { open = fs.openSync, write = fs.writeSync, close = fs.closeSync,
  description = "CAN frames reported by adapter; timestamps are host chunk arrival, not hardware bus timing." } = {}) {
  let descriptor = open(file, "wx");
  let count = 0, writeFailed = false;
  const startedAt = new Date().toISOString();
  function put(bytes) {
    let offset = 0;
    try {
      while (offset < bytes.length) {
        const written = write(descriptor, bytes, offset, bytes.length - offset);
        if (!Number.isInteger(written) || written <= 0) throw new Error("pcap-short-write");
        offset += written;
      }
    } catch (error) { writeFailed = true; throw error; }
  }
  try {
    const section = Buffer.alloc(16); section.writeUInt32LE(0x1a2b3c4d); section.writeUInt16LE(1, 4); section.writeBigInt64LE(-1n, 8);
    put(block(0x0a0d0d0a, Buffer.concat([section, option(1, description), endOptions])));
    const iface = Buffer.alloc(8); iface.writeUInt16LE(227); iface.writeUInt32LE(16, 4);
    put(block(1, Buffer.concat([iface, option(2, `${network} CAN (user-declared wiring)`), option(9, Buffer.from([6])), endOptions])));
  } catch (error) { close(descriptor); descriptor = null; throw error; }
  return {
    append(frames) {
      if (descriptor === null) throw new Error("pcap-closed");
      if (writeFailed) throw new Error("pcap-write-failed");
      const packets = frames.map((frame) => {
        if (!validCanFrame(frame)) throw new Error("pcap-invalid-frame");
        const timestamp = BigInt(frame.timestampUs);
        const packet = Buffer.alloc(16);
        packet.writeUInt32BE((frame.canId | (frame.extended ? 0x80000000 : 0)) >>> 0);
        packet[4] = frame.dataHex.length / 2;
        Buffer.from(frame.dataHex, "hex").copy(packet, 8);
        const header = Buffer.alloc(20); header.writeUInt32LE(Number(timestamp >> 32n), 4);
        header.writeUInt32LE(Number(timestamp & 0xffffffffn), 8); header.writeUInt32LE(16, 12); header.writeUInt32LE(16, 16);
        const context = frame.captureContext || frame.monotonicClock ? JSON.stringify({ ...frame.captureContext,
          ...(frame.monotonicClock ? { monotonicClock: frame.monotonicClock, monotonicTimestampUs: frame.monotonicTimestampUs,
            startedMonotonicTimestampUs: frame.startedMonotonicTimestampUs, startedTimestampUs: frame.startedTimestampUs } : {}) }) : null;
        if (context && Buffer.byteLength(context) > 2048) throw new Error("pcap-context-limit");
        return block(6, Buffer.concat([header, packet, ...(context ? [option(1, context), endOptions] : [])]));
      });
      put(Buffer.concat(packets)); count += frames.length;
    },
    finish(reason = "user-stopped", interruptions = []) {
      if (descriptor === null) return;
      try {
        // A valid prefix is useful, but a lost/partial write cannot be closed
        // with the same completeness marker as a successful recording.
        if (writeFailed) return;
        const stamp = BigInt(Date.now()) * 1000n;
        const header = Buffer.alloc(12); header.writeUInt32LE(Number(stamp >> 32n), 4); header.writeUInt32LE(Number(stamp & 0xffffffffn), 8);
        const received = Buffer.alloc(8); received.writeBigUInt64LE(BigInt(count));
        put(block(5, Buffer.concat([header, option(4, received), option(1, JSON.stringify({ reason, startedAt, endedAt: new Date().toISOString(), interruptions })), endOptions])));
      } finally { close(descriptor); descriptor = null; }
    },
    get count() { return count; },
    file,
    startedAt,
  };
}

// The connection manager owns the ten-minute deadline. Store canonical received
// frames in volatile packed pages; a display window is never a retention limit.
const FRAME_BYTES = 21, PAGE_FRAMES = 4096;
const FRAME_KEYS = new Set(["canId", "extended", "dataHex", "timestampUs", "timestampSource"]);
export function createCanFrameBatch({ now = () => new Date().toISOString(), elapsedNow = () => performance.now(),
  startElapsedMs, signals = [], signalDefinitionError = false } = {}) {
  let pages = [], latest = new Map(), total = 0, endedAt = null, interruptions = [], omittedInterruptions = 0, stopped = false, stopReason = null;
  const startedAt = now();
  const startedTick = startElapsedMs ?? elapsedNow(); let endedTick = null, epoch = 0, parameterSampleCount = 0;
  const ids = new Set(), byFrame = new Map();
  if (!Array.isArray(signals) || signals.length > 4096) throw new Error("internal-signal-source-invalid");
  const definitions = signals.map(input => {
    const d = structuredClone(input);
    if (!d || d.sourceQualified !== true || typeof d.id !== "string" || !/^[a-zA-Z0-9:_-]{1,160}$/.test(d.id)
      || ids.has(d.id) || typeof d.name !== "string" || !d.name.trim() || d.name.length > 240
      || typeof d.unit !== "string" || d.unit.length > 256 || typeof d.extended !== "boolean"
      || !Number.isInteger(d.canId) || d.canId < 0 || d.canId > (d.extended ? 0x1fffffff : 0x7ff))
      throw new Error("internal-signal-source-invalid");
    decodeInternalInteger("0000000000000000", d);
    ids.add(d.id);
    const key = `${d.canId}:${d.extended}`;
    if (!byFrame.has(key)) byFrame.set(key, []);
    const state = { definition: d, latest: null, invalid: false, breaks: 0, buckets: [] };
    byFrame.get(key).push(state); return state;
  });
  const plotTime = () => Math.max(0, (endedTick ?? elapsedNow()) - startedTick);
  const timestamp = us => new Date(Math.floor(us / 1000)).toISOString().replace(/(\.\d{3})Z$/, `$1${String(us % 1000).padStart(3, "0")}Z`);
  function decode(frame, d) {
    const value = decodeInternalInteger(frame.dataHex, d).value;
    return { parameterId: d.id, name: d.name, value, unit: d.unit, capturedUtc: timestamp(frame.timestampUs) };
  }
  function prune(state, time) {
    while (state.buckets.length && state.buckets[0].end < time - 60000) state.buckets.shift();
  }
  function observe(frame, time) {
    for (const state of byFrame.get(`${frame.canId}:${frame.extended}`) || []) {
      let sample;
      try { sample = decode(frame, state.definition); }
      catch { if (!state.invalid) state.breaks++; state.invalid = true; continue; }
      state.latest = { value: sample.value, elapsedMs: time }; state.invalid = false; parameterSampleCount++;
      const point = { value: sample.value, elapsedMs: time, sequence: parameterSampleCount };
      const bin = Math.floor(time / 500);
      const segment = `${epoch}:${state.breaks}`;
      let bucket = state.buckets.at(-1);
      if (!bucket || bucket.bin !== bin || bucket.epoch !== segment) {
        bucket = { bin, epoch: segment, end: time, points: [] }; state.buckets.push(bucket);
      }
      bucket.end = time;
      const points = [...bucket.points, point];
      // Display only: retain first/min/max/last actual observations per half
      // second. Full parameter export is decoded from every retained frame.
      bucket.points = [...new Set([points[0], points.reduce((a, b) => a.value <= b.value ? a : b),
        points.reduce((a, b) => a.value >= b.value ? a : b), point])].sort((a, b) => a.sequence - b.sequence);
      prune(state, time);
    }
  }
  function parameterView(time) {
    return definitions.map(state => {
      prune(state, time);
      const segments = []; let previousEpoch;
      for (const bucket of state.buckets) for (const point of bucket.points) {
        if (point.elapsedMs < time - 60000) continue;
        if (previousEpoch !== bucket.epoch) { segments.push([]); previousEpoch = bucket.epoch; }
        segments.at(-1).push({ value: point.value, elapsedMs: point.elapsedMs });
      }
      const { id, name, nameEn, unit } = state.definition;
      return { id, name, nameEn, unit, value: state.latest?.value ?? null,
        lastElapsedMs: state.latest?.elapsedMs ?? null, invalid: state.invalid, segments };
    });
  }
  function captureFrames() {
    const count = total, capturedPages = pages.slice();
    return (function* () {
      for (let index = 0; index < count; index++) {
        const page = capturedPages[Math.floor(index / PAGE_FRAMES)], at = index % PAGE_FRAMES * FRAME_BYTES;
        const id = page.readUInt32LE(at), length = page[at + 12];
        yield { canId: id & 0x1fffffff, extended: !!(id & 0x80000000),
          dataHex: page.subarray(at + 13, at + 13 + length).toString("hex").toUpperCase(),
          timestampUs: page.readDoubleLE(at + 4), timestampSource: "host-chunk-arrival" };
      }
    })();
  }
  return {
    ingest(input) {
      if (!Array.isArray(input) || input.length > 512 || !input.every(frame => validCanFrame(frame)
        && Object.keys(frame).every(key => FRAME_KEYS.has(key)))) throw new Error("internal-frame-invalid");
      if (stopped) return;
      const time = plotTime();
      for (const frame of input) {
        const index = Math.floor(total / PAGE_FRAMES), at = total % PAGE_FRAMES * FRAME_BYTES;
        if (!pages[index]) pages[index] = Buffer.alloc(PAGE_FRAMES * FRAME_BYTES);
        const page = pages[index];
        page.writeUInt32LE((frame.canId | (frame.extended ? 0x80000000 : 0)) >>> 0, at);
        page.writeDoubleLE(frame.timestampUs, at + 4);
        page[at + 12] = frame.dataHex.length / 2;
        Buffer.from(frame.dataHex, "hex").copy(page, at + 13);
        total++;
        const key = `${frame.canId}:${frame.extended}`;
        if (latest.has(key)) latest.delete(key);
        latest.set(key, frame);
        if (latest.size > 256) latest.delete(latest.keys().next().value);
        observe(frame, time);
      }
    },
    stop(reason) { stopped = true; endedAt ||= now(); endedTick ??= reason === "time-limit" ? Math.min(elapsedNow(), startedTick + 600000) : elapsedNow(); stopReason = reason; },
    interrupt(reason) { if (!endedAt) { epoch++; interruptions.push({ at: now(), reason: String(reason).slice(0, 160) }); if (interruptions.length > 128) { interruptions.shift(); omittedInterruptions++; } } },
    view() { return { startedAt, endedAt, frameCount: total, retainedFrames: total, frameLimit: null,
      retainedBytes: total * FRAME_BYTES, allocatedBytes: pages.length * PAGE_FRAMES * FRAME_BYTES, retentionComplete: true,
      batchComplete: true, batchClosed: !!endedAt, stopReason, interruptions, omittedInterruptions, latest: [...latest.values()],
      verifiedSignalCount: definitions.length, signalDefinitionError, parameterSampleCount,
      plotElapsedMs: plotTime(), parameters: parameterView(plotTime()) }; },
    export({ stream = false } = {}) { const { latest: _latest, parameters: _parameters, plotElapsedMs: _plot, parameterSampleCount: _sampleCount, signalDefinitionError: _definitionError, ...summary } = this.view();
      const frames = captureFrames();
      return { kind: "internal-can-acquisition", ...summary, interruptions: interruptions.map(entry => ({ ...entry })), receivedTotal: total,
        endedAt: endedAt || now(), frames: stream ? frames : Array.from(frames), timestampSource: "host-chunk-arrival", physicalSilenceVerified: false }; },
    exportParameters(network) {
      if (!stopped) throw new Error("internal_receive_busy");
      if (!parameterSampleCount) throw new Error("parameter_data_unavailable");
      return { kind: "internal-parameter-acquisition", simulation: false, retentionComplete: true,
        sampleCount: parameterSampleCount, decodedSourceQualified: true, canNetwork: network, startedUtc: startedAt, finishedUtc: endedAt,
        samples: { *[Symbol.iterator]() {
          for (const frame of captureFrames()) for (const state of byFrame.get(`${frame.canId}:${frame.extended}`) || []) {
            try { yield decode(frame, state.definition); } catch { /* invalid observations stay absent, never filled */ }
          }
        } } };
    },
  };
}
