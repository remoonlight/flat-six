import { createHash } from "node:crypto";

const digest = bytes => createHash("sha256").update(bytes).digest("hex");
const requestHex = value => typeof value === "string" && /^(21|22)([0-9A-F]{2}){1,6}$/.test(value);
const utc = value => typeof value === "string" && value.length <= 40 && Number.isFinite(Date.parse(value));

/** Check worker evidence before retaining values or removing ordinary raw PDUs.
 * This proves internal request/response association, not an independent decode
 * or vehicle qualification. The Python client still owns the pinned read plan.
 */
export function validateManufacturerEvidence(cycle, prior = {}) {
  if (!Array.isArray(cycle?.transactions) || !Array.isArray(cycle.samples)) return null;
  const requests = new Map(), bindings = new Map(prior.parameterRequests || []);
  let elapsedMs = prior.elapsedMs ?? 0, connectionEpoch = prior.connectionEpoch ?? 1;
  const qualifiedEpochs = prior.qualifiedEpochs || new Set([1]);
  for (const transaction of cycle.transactions) {
    if (!transaction || !requestHex(transaction.requestHex) || requests.has(transaction.requestHex)
      || typeof transaction.ok !== "boolean" || transaction.cycle !== cycle.cycle
      || transaction.sourceKind !== cycle.sourceKind || transaction.timestampSource !== "host-response-observed"
      || !utc(transaction.processedUtc) || !Number.isFinite(transaction.elapsedMs) || transaction.elapsedMs < elapsedMs
      || !Number.isSafeInteger(transaction.connectionEpoch) || transaction.connectionEpoch < connectionEpoch
      || !qualifiedEpochs.has(transaction.connectionEpoch)
      || typeof transaction.pduHex !== "string" || !/^(?:[0-9A-F]{2}){1,4095}$/.test(transaction.pduHex)) return null;
    const pdu = Buffer.from(transaction.pduHex, "hex"), request = Buffer.from(transaction.requestHex, "hex");
    if (digest(pdu) !== transaction.pduSha256 || transaction.ok && (pdu[0] !== request[0] + 0x40
      || !pdu.subarray(1, request.length).equals(request.subarray(1)))) return null;
    requests.set(transaction.requestHex, { transaction, samples: 0 });
    elapsedMs = transaction.elapsedMs; connectionEpoch = transaction.connectionEpoch;
  }
  for (const sample of cycle.samples) {
    const group = requests.get(sample?.requestHex), transaction = group?.transaction;
    if (!transaction?.ok || sample.pduHex !== undefined || sample.pduSha256 !== transaction.pduSha256
      || sample.processedUtc !== transaction.processedUtc || sample.elapsedMs !== transaction.elapsedMs
      || sample.connectionEpoch !== transaction.connectionEpoch || sample.timestampSource !== transaction.timestampSource
      || sample.sourceKind !== cycle.sourceKind
      || bindings.has(sample.parameterId) && bindings.get(sample.parameterId) !== sample.requestHex) return null;
    bindings.set(sample.parameterId, sample.requestHex); group.samples++;
  }
  if ([...requests.values()].some(group => group.transaction.ok !== (group.samples > 0))) return null;
  if (cycle.ok && (!requests.size || [...requests.values()].some(group => !group.transaction.ok))) return null;
  return { parameterRequests: bindings, elapsedMs, connectionEpoch };
}
