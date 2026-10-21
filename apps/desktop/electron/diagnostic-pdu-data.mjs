import fs from "node:fs";
import { validCaptureClock } from "./capture-clock.mjs";

// Wireshark Upper PDU (LINKTYPE 252), with the registered "data" dissector.
// No fabricated CAN, IP, transport headers or sent requests are added.
// https://www.wireshark.org/docs/wsar_html/exported__pdu__tlvs_8h.html
// https://www.iana.org/assignments/pcap/pcap.xhtml
const pad = (data) => Buffer.concat([data, Buffer.alloc((4 - data.length % 4) % 4)]);
function option(code, data) {
  const value = Buffer.isBuffer(data) ? data : Buffer.from(String(data));
  const head = Buffer.alloc(4); head.writeUInt16LE(code); head.writeUInt16LE(value.length, 2);
  return Buffer.concat([head, pad(value)]);
}
function block(type, body) {
  const head = Buffer.alloc(8), tail = Buffer.alloc(4);
  head.writeUInt32LE(type); head.writeUInt32LE(body.length + 12, 4); tail.writeUInt32LE(body.length + 12);
  return Buffer.concat([head, body, tail]);
}
const end = Buffer.alloc(4);
export function validDiagnosticPdu(pdu) {
  return !!pdu && (pdu.requestHex === null || (typeof pdu.requestHex === "string" && /^(?:[0-9A-F]{2}){1,7}$/.test(pdu.requestHex)))
    && typeof pdu.pduHex === "string" && /^(?:[0-9A-F]{2}){1,4095}$/.test(pdu.pduHex)
    && Number.isSafeInteger(pdu.timestampUs) && pdu.timestampUs > 0 && pdu.timestampSource === "host-pdu-observed"
    && (pdu.startedTimestampUs === undefined || (Number.isSafeInteger(pdu.startedTimestampUs)
      && pdu.startedTimestampUs > 0 && (pdu.startedTimestampUs <= pdu.timestampUs || pdu.startedMonotonicTimestampUs !== undefined)))
    && validCaptureClock(pdu)
    && typeof pdu.duringCleanup === "boolean"
    && ((["elm-iso-tp", "elm-mode30"].includes(pdu.transportKind) && pdu.observation === "reassembled-complete-pdu")
      || (((pdu.transportKind === "d-pdu-iso-tp" && pdu.observation === "native-result-data")
        || (["d-pdu-mode50", "d-pdu-mode30"].includes(pdu.transportKind) && pdu.observation === "reassembled-complete-pdu"))
        && [pdu.primitive, pdu.uniqueId].every(n => Number.isInteger(n) && n >= 0 && n <= 0xffffffff)
        && (pdu.expectedPrimitive === undefined || (Number.isInteger(pdu.expectedPrimitive) && pdu.expectedPrimitive >= 0 && pdu.expectedPrimitive <= 0xffffffff))
        && typeof pdu.flagsHex === "string" && /^(?:[0-9A-F]{2}){0,16}$/.test(pdu.flagsHex)));
}
export function createDiagnosticPduWriter(file, simulation, { open = fs.openSync, write = fs.writeSync, close = fs.closeSync } = {}) {
  let descriptor = open(file, "wx"), count = 0, writeFailed = false;
  const startedAt = new Date().toISOString();
  function put(data) {
    let offset = 0;
    try {
      while (offset < data.length) {
        const n = write(descriptor, data, offset, data.length - offset);
        if (!Number.isInteger(n) || n <= 0) throw new Error("pcap-short-write");
        offset += n;
      }
    } catch (error) { writeFailed = true; throw error; }
  }
  try {
    const shb = Buffer.alloc(16); shb.writeUInt32LE(0x1a2b3c4d); shb.writeUInt16LE(1, 4); shb.writeBigInt64LE(-1n, 8);
    put(block(0x0a0d0d0a, Buffer.concat([shb, option(1, `${simulation ? "SYNTHETIC; not vehicle evidence. " : ""}Received diagnostic application PDUs only. ELM: complete ISO-TP reassembly; D-PDU: native result bytes or source mode50 reassembly before qualification. No inferred TX or CAN frames. Host observation times; requested target is not proof of responder identity.`), end])));
    const idb = Buffer.alloc(8); idb.writeUInt16LE(252); idb.writeUInt32LE(8192, 4);
    put(block(1, Buffer.concat([idb, option(2, "diagnostic application RX"), option(9, Buffer.from([6])), end])));
  } catch (error) { close(descriptor); descriptor = null; throw error; }
  return {
    append(pdu, captureContext) {
      if (descriptor === null) throw new Error("pcap-closed");
      if (writeFailed) throw new Error("pcap-write-failed");
      if (!validDiagnosticPdu(pdu)) throw new Error("pcap-invalid-pdu");
      const { pduHex: _bytes, ...metadata } = pdu;
      const comment = JSON.stringify({ ...metadata, ...captureContext });
      if (Buffer.byteLength(comment) > 4096) throw new Error("pcap-context-limit");
      // Upper-PDU TLVs use big endian. Tag 12 selects the registered dissector;
      // the terminating tag is mandatory. The payload is exactly received bytes.
      const tag = Buffer.alloc(4); tag.writeUInt16BE(12); tag.writeUInt16BE(4, 2);
      const packet = Buffer.concat([tag, Buffer.from("data"), end, Buffer.from(pdu.pduHex, "hex")]);
      const stamp = BigInt(pdu.timestampUs), epb = Buffer.alloc(20);
      epb.writeUInt32LE(Number(stamp >> 32n), 4); epb.writeUInt32LE(Number(stamp & 0xffffffffn), 8);
      epb.writeUInt32LE(packet.length, 12); epb.writeUInt32LE(packet.length, 16);
      put(block(6, Buffer.concat([epb, pad(packet), option(1, comment), end]))); count++;
    },
    finish(reason) {
      if (descriptor === null) return;
      try {
        // Preserve the received prefix without certifying a file that lost a
        // packet to a failed write. The existing reader requires the ISB.
        if (writeFailed) return;
        const stamp = BigInt(Date.now()) * 1000n, isb = Buffer.alloc(12), received = Buffer.alloc(8);
        isb.writeUInt32LE(Number(stamp >> 32n), 4); isb.writeUInt32LE(Number(stamp & 0xffffffffn), 8);
        received.writeBigUInt64LE(BigInt(count));
        put(block(5, Buffer.concat([isb, option(4, received), option(1, JSON.stringify({ reason, startedAt, endedAt: new Date().toISOString() })), end])));
      } finally { close(descriptor); descriptor = null; }
    },
    get count() { return count; }, file, startedAt,
  };
}
