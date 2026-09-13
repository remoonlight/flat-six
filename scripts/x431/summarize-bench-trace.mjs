// Offline-only extractor for the observed V24.58 bench wrapper. No device access.
import { readFileSync, writeFileSync } from 'node:fs';
import { createHash } from 'node:crypto';
import { pathToFileURL } from 'node:url';

const API = '_Z40ApkApi_SendDataToSmartBoxGetBinaryStringPhiS_ii';
function hex(buffer) { return [...buffer].map(x => x.toString(16).padStart(2, '0')).join(' ').toUpperCase(); }
function parseBytes(capture) {
  if (!capture || typeof capture.hex !== 'string' || !/^(?:[0-9a-f]{2})(?: [0-9a-f]{2})*$/i.test(capture.hex)) return null;
  const value = Buffer.from(capture.hex.replaceAll(' ', ''), 'hex');
  return value.length === capture.length ? value : null;
}

export function decodeObservedSingleFrame(capture) {
  const value = parseBytes(capture);
  // Only this exact legacy 11-bit, eight-data-byte envelope is understood.
  if (!value || value.length !== 19 || hex(value.subarray(0, 9)) !== '64 00 01 FF 02 0D 61 01 08') return null;
  const length = value[11];
  if (length < 1 || length > 7 || (value.readUInt16BE(9) & 31) !== 0) return null;
  return {
    encodedAddress: hex(value.subarray(9, 11)),
    candidateCanId: '0x' + (value.readUInt16BE(9) >>> 5).toString(16).toUpperCase(),
    addressInterpretation: 'SJA1000-style 11-bit packing hypothesis; not bus-verified',
    isoTpSingleFrame: hex(value.subarray(11)),
    diagnosticPayload: hex(value.subarray(12, 12 + length)),
  };
}

export function summarizeTrace(source) {
  const rows = source.split(/\r?\n/).filter(x => x.trim()).map(x => JSON.parse(x));
  const events = rows.filter(x => x.type === 'send').map(x => x.payload);
  const leaves = new Map(events.filter(x => x.event === 'leave').map(x => [x.call, x]));
  const requests = [];
  const filters = [];
  for (const event of events) {
    if (event.event !== 'enter') continue;
    if (event.name === API) {
      const decoded = decodeObservedSingleFrame(event.vci_tx);
      if (decoded) {
        const leave = leaves.get(event.call);
        requests.push({ sourceSeq: event.seq, ...decoded,
          durationMs: leave ? leave.time - event.time : null,
          vciReturnInt: leave?.return_int ?? null,
          vciReturnBytes: parseBytes(leave?.vci_rx) ? hex(parseBytes(leave.vci_rx)) : null,
          ecuResponseStatus: 'not decoded or validated by this extractor',
        });
      }
    }
    if (event.name === 'CanSetParameter' && event.args?.[0] === '0x60' && event.args?.[1] === '0x1') {
      const value = parseBytes(event.parameter_prefix);
      if (value && value.length >= 12 && value.readUInt32LE(0) === 0 && value[9] === 1 && (value.readUInt16BE(10) & 31) === 0) {
        filters.push({ sourceSeq: event.seq, encodedAddress: hex(value.subarray(10, 12)),
          candidateCanId: '0x' + (value.readUInt16BE(10) >>> 5).toString(16).toUpperCase(),
          status: 'candidate receive filter; not bus-verified' });
      }
    }
  }
  return {
    schemaVersion: 1, purpose: 'offline review of a no-vehicle X431 trace',
    executable: false, productionReady: false,
    sourceSha256: createHash('sha256').update(source).digest('hex'),
    eventCount: events.length,
    traceErrors: rows.filter(x => x.type === 'error').length,
    vciCallCount: events.filter(x => x.event === 'enter' && x.name === API).length,
    diagnosticRequests: requests, receiveFilterCandidates: filters,
    bitRate: null, obdPins: null, vehicleResponse: null,
    limitations: [
      'Function buffers are app-to-VCI traffic, not a CAN bus capture.',
      'A VCI return length or status is not proof of an ECU response.',
      'The address packing remains a hypothesis; parameter value 03 has no verified bit-rate mapping.',
      'Observed requests are evidence, not an approved replay sequence.',
    ],
  };
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  const [input, output] = process.argv.slice(2);
  if (!input || !output) throw new Error('Usage: node scripts/x431/summarize-bench-trace.mjs <input.jsonl> <new-output.json>');
  const result = summarizeTrace(readFileSync(input, 'utf8'));
  writeFileSync(output, JSON.stringify(result, null, 2) + '\n', { flag: 'wx' });
  console.log(`Saved ${result.diagnosticRequests.length} observed requests; no vehicle validation implied.`);
}
