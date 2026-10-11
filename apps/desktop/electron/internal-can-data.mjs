import fs from "node:fs";
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

export function createCanPcapWriter(file, network, { open = fs.openSync, write = fs.writeSync, close = fs.closeSync,
  description = "CAN frames reported by adapter; timestamps are host chunk arrival, not hardware bus timing." } = {}) {
  let descriptor = open(file, "wx");
  let count = 0;
  const startedAt = new Date().toISOString();
  function put(bytes) {
    let offset = 0;
    while (offset < bytes.length) {
      const written = write(descriptor, bytes, offset, bytes.length - offset);
      if (!Number.isInteger(written) || written <= 0) throw new Error("pcap-short-write");
      offset += written;
    }
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
export function createCanFrameBatch({ now = () => new Date().toISOString() } = {}) {
  let pages = [], latest = new Map(), total = 0, endedAt = null, interruptions = [], omittedInterruptions = 0, stopped = false, stopReason = null;
  const startedAt = now();
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
      }
    },
    stop(reason) { stopped = true; endedAt ||= now(); stopReason = reason; },
    interrupt(reason) { if (!endedAt) { interruptions.push({ at: now(), reason: String(reason).slice(0, 160) }); if (interruptions.length > 128) { interruptions.shift(); omittedInterruptions++; } } },
    view() { return { startedAt, endedAt, frameCount: total, retainedFrames: total, frameLimit: null,
      retainedBytes: total * FRAME_BYTES, allocatedBytes: pages.length * PAGE_FRAMES * FRAME_BYTES, retentionComplete: true,
      batchComplete: true, batchClosed: !!endedAt, stopReason, interruptions, omittedInterruptions, latest: [...latest.values()], verifiedSignalCount: 0 }; },
    export({ stream = false } = {}) { const { latest: _latest, ...summary } = this.view();
      const frames = captureFrames();
      return { kind: "internal-can-acquisition", ...summary, interruptions: interruptions.map(entry => ({ ...entry })), receivedTotal: total,
        endedAt: endedAt || now(), frames: stream ? frames : Array.from(frames), timestampSource: "host-chunk-arrival", physicalSilenceVerified: false }; },
  };
}
