import fs from "node:fs";

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
    frame.timestampSource === "host-chunk-arrival";
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
        const context = frame.captureContext ? JSON.stringify(frame.captureContext) : null;
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

// A bounded acquisition batch ends at the limit while the bus keeps receiving.
// It never drops an earlier batch frame and claims the export is complete.
export function createCanFrameBatch({ limit = 25000, now = () => new Date().toISOString() } = {}) {
  let frames = [], latest = new Map(), total = 0, endedAt = null, interruptions = [], omittedInterruptions = 0;
  const startedAt = now();
  return {
    ingest(input) {
      if (!Array.isArray(input) || input.length > 512 || !input.every(validCanFrame)) throw new Error("internal-frame-invalid");
      for (const frame of input) {
        total++;
        const key = `${frame.canId}:${frame.extended}`;
        if (latest.has(key)) latest.delete(key);
        latest.set(key, frame);
        if (latest.size > 256) latest.delete(latest.keys().next().value);
        if (!endedAt) {
          frames.push(frame);
          if (frames.length >= limit) endedAt = now();
        }
      }
    },
    interrupt(reason) { if (!endedAt) { interruptions.push({ at: now(), reason: String(reason).slice(0, 160) }); if (interruptions.length > 128) { interruptions.shift(); omittedInterruptions++; } } },
    view() { return { startedAt, endedAt, frameCount: total, retainedFrames: frames.length, frameLimit: limit,
      batchComplete: true, batchClosed: !!endedAt, interruptions, omittedInterruptions, latest: [...latest.values()], verifiedSignalCount: 0 }; },
    export() { const { latest: _latest, ...summary } = this.view();
      return { kind: "internal-can-acquisition", ...summary, frameCount: frames.length, receivedTotal: total,
        endedAt: endedAt || now(), frames, timestampSource: "host-chunk-arrival", physicalSilenceVerified: false }; },
  };
}
