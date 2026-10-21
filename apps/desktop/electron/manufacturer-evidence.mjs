import { createHash } from "node:crypto";
import { faultReadTargets } from "../src/obd/manufacturer-profiles.mjs";

const digest = bytes => createHash("sha256").update(bytes).digest("hex");
const requestHex = value => typeof value === "string" && /^(21|22)([0-9A-F]{2}){1,6}$/.test(value);
const utc = value => typeof value === "string" && value.length <= 40 && Number.isFinite(Date.parse(value));
const unavailableReply = (request, pdu) => pdu.length === 3 && pdu[0] === 0x7F
  && pdu[1] === request[0] && [0x11, 0x31].includes(pdu[2]);
const invalidReasons = new Set(["invalid_reserved", "zero_denominator", "nan", "encoding_bcd_invalid",
  "out_of_bounds", "out_of_range", "texttable_unmapped"]);

const piwisDtcFamily = profileId => /^9x1:SWSG:SWSG_(Master|Slave)_(mit_AFS|ohne_AFS)_ab_(1310|VR06|VR15)$/.test(profileId)
  ? 'SWSG' : profileId==='9x1:BV_SoundSysteUDS:EV_AMPMst16C4Gen2BOSE_002' ? 'BOSE002' : null;

function validPiwisDtcSource(value, contract) {
  const family=piwisDtcFamily(value.profileId), swsg=family==='SWSG', bose=family==='BOSE002';
  if (!(swsg && [47,48].includes(contract.ecuId) || bose && contract.ecuId===65)
    || !faultReadTargets(value.profileId).includes(contract.ecuId)) return false;
  const expected=swsg ? {
    project:'9x1',version:'2830.1.0',sourceFile:'0.0.0@SWSG.bv.db',
    sourceSha256:'a39358f6958af044d4585f0093dd1832c11d192e45bb8f9dddd6a8b4122cf8c7',
    requestSha256:'b86940f0b1b9b17870a6598337bdcc47ca05416df0a078b325570086e06fa4e7',
    responseSha256:'361cde30cc89a6fd49ad5f1c1c165929a50eeafc3059971e336f3193fb4c7132',
  } : {
    project:'9x1',version:'2830.1.0',sourceFile:'0.0.0@BV_SoundSysteUDS.bv.db',
    sourceSha256:'1e3b6fb7c4fb24067fe81ae53c7dbf08ae32038df2102495c8a7a4357fb08ebc',
    requestSha256:'3b98ac0365c01e87935ab76ee1ea4ef0ec34c6ef54a3aa3e5710a0cc6512163b',
    responseSha256:'acbfd21824a07c1d470ce95d425200f0accfff0526102d8d5e045adf8df574ba',
  };
  const source=value.piwisReadSource;
  return !!source && Object.keys(source).sort().join(',')===Object.keys(expected).sort().join(',')
    && Object.entries(expected).every(([k,v])=>source[k]===v)
    && contract.applicationProtocol==='uds' && contract.sourceQualification==='uds-source-version-and-identity'
    && contract.sourceBridge?.project===expected.project && contract.sourceBridge?.version===expected.version
    && contract.sourceBridge?.sources?.[expected.sourceFile]===expected.sourceSha256
    && contract.txId===(swsg ? contract.ecuId===47?0x725:0x754 : 0x76F)
    && contract.rxId===(swsg ? contract.ecuId===47?0x78F:0x7BE : 0x7D9);
}

/** Source labels and reply hashes accompany already-qualified identity values. */
export function validManufacturerIdentityInfo(doc, observed) {
  if (!Array.isArray(doc?.identityInfo) || doc.identityInfo.length>256 || !Array.isArray(doc.readContract?.identityRequests)) return false;
  const contract=doc.readContract, led=contract.sourceIdentityKind==='piwis-led-identity';
  const ledFields=[['22F19E',0,'ASAM 标识'],['22F1A2',0,'诊断主版本'],['22F1A2',1,'诊断次版本'],
    ['22F187',0,'零件号'],['22F189',0,'软件版本'],['22F191',0,'硬件号']];
  if (led && (!/^9x1:SCHEINWERFER_LED:Scheinwerfer_LED_(DK2F|DK3|Links|Rechts)_001$/.test(contract.profileId)
    || contract.ecuId!==47 || contract.txId!==0x725 || contract.rxId!==0x78F
    || contract.applicationProtocol!=='uds' || contract.sourceQualification!=='uds-source-version-and-identity'
    || contract.sourceBridge?.project!=='9x1' || contract.sourceBridge?.version!=='2830.1.0'
    || contract.sourceBridge?.sources?.['0.0.0@SCHEINWERFER_LED.bv.db']!=='07dee0d7e404ea175ab4c6695ef76b3db5fe8d9102184731b6e4f0230efa1fb5'
    || doc.identityInfo.length!==ledFields.length)) return false;
  const seen=new Set();
  for (const row of doc.identityInfo) {
    if (!row || Object.keys(row).sort().join(',')!=='label,pduSha256,recordIndex,requestHex,value'
      || typeof row.label!=='string' || !row.label.trim() || typeof row.value!=='string'
      || row.value.length>4096 || !Number.isSafeInteger(row.recordIndex) || row.recordIndex<0
      || !/^[a-f0-9]{64}$/.test(row.pduSha256 || '') || observed.get(row.requestHex)!==row.pduSha256) return false;
    const group=doc.readContract.identityRequests.find(item=>item.requestHex===row.requestHex);
    const record=(group?.records || (group?.record?[group.record]:[]))[row.recordIndex];
    const key=row.requestHex+':'+row.recordIndex;
    const explicitName=typeof record?.name==='string' && record.name && !record.name.startsWith('SubIndexNum=') ? record.name : null;
    const sourceLabel=explicitName || (Array.isArray(record?.labels) ? record.labels
      .filter(label=>typeof label?.text==='string' && label.text).map(label=>label.text).join(' / ') : '');
    const knownLabel=led ? group && ledFields.some(([request,index,label])=>request===row.requestHex
      && index===row.recordIndex && label===row.label) : sourceLabel && sourceLabel===row.label;
    if (!knownLabel || seen.has(key)) return false;
    seen.add(key);
  }
  return true;
}

/** Independently check fault bytes/counts; a malformed reply cannot mean zero faults. */
export function validManufacturerDtcResult(doc) {
  const value=doc?.dtcRead, contract=doc?.readContract, qualified=doc?.transport?.qualification;
  if (!contract || !qualified || doc?.dtcOnly!==true || doc.identityOnly!==false || doc.vehicleDataCollected!==true
    || doc.vehicleGeneration!==contract?.generation || !/^[a-f0-9]{64}$/.test(doc.vehicleIdentitySha256 || "")
    || !value || value.ok!==true || value.freshRead!==true || value.liveVerified!==false || value.writePayload!==null
    || value.profileId!==doc.identifiedProfileId || value.profileId!==contract?.profileId
    || value.ecuId!==contract.ecuId || value.generation!==contract.generation
    || value.sourceCatalogId!==contract.sourceCatalogId || value.sourceCatalogId!==qualified?.sourceCatalogId
    || !/^[a-f0-9]{64}$/.test(value.sourceCatalogId || "") || qualified.freshRead!==true
    || qualified.observedProfileMatch!==true || qualified.ecuId!==contract.ecuId || qualified.generation!==contract.generation
    || qualified.variantLink?.profile_id!==value.profileId
    || !/^[a-f0-9]{64}$/.test(value.definitionSha256 || "")) return false;
  const unitVin=Array.isArray(contract.identityRequests) && contract.identityRequests.some(row=>['1A90','22F190'].includes(row.requestHex));
  if (doc.vehicleIdentitySource!==(unitVin?'unit-vin':'gateway-vin')) return false;
  const anchor=doc.vehicleAnchor;
  if (!unitVin || anchor!==undefined) {
    if (anchor?.ecuId!==9 || !faultReadTargets(anchor.profileId).includes(9) || anchor.sourceCatalogId!==value.sourceCatalogId
      || anchor.vehicleGeneration!==doc.vehicleGeneration || anchor.vehicleIdentitySha256!==doc.vehicleIdentitySha256
      || anchor.freshRead!==true || anchor.observedProfileMatch!==true || anchor.closed!==true) return false;
  }
  const layouts={"03000102FF0001":"kwp-dtc-18","03000102FF0009":"kwp-dtc-18","04FF0203FF0009":"uds-dtc-19-02",
    "03FF0102FF000B":"kwp-dtc-18-length"};
  const piwis=value.descriptorHex===null;
  if (piwis!==!!piwisDtcFamily(value.profileId)) return false;
  if (piwis ? !validPiwisDtcSource(value,contract) : value.piwisReadSource!==undefined) return false;
  const type=piwis?'uds-dtc-19-02':layouts[value.descriptorHex], decoded=value.decoded, responses=value.responses;
  if (!type || value.decodeType!==type || decoded?.ok!==true || decoded.type!==type
    || !Array.isArray(decoded.records) || !Array.isArray(responses)
    || responses.length!==(["03000102FF0001","03FF0102FF000B"].includes(value.descriptorHex)?1:2)) return false;
  if(type==='kwp-dtc-18-length' && (![30,31,76,78].includes(contract.ecuId)
    || contract.transportAddressing?.kind!=='porsche-tp20'))return false;
  if (responses.length===2) {
    const first=responses[0], request=contract.session?.requestHex;
    if (!['1003','1089'].includes(request) || first?.role!=="sessionPrecondition" || first.requestHex!==request
      || typeof first.pduHex!=="string" || !/^(?:[0-9A-F]{2}){2,4095}$/.test(first.pduHex)
      || !first.pduHex.startsWith("50"+request.slice(2))) return false;
  }
  const last=responses.at(-1);
  if (piwis && last?.requestHex!=='190208') return false;
  if (last?.role!=="dtcRead" || (type==='kwp-dtc-18'?last.requestHex!=='1800FF00'
    :type==='kwp-dtc-18-length'?last.requestHex!=='1802FF00':!['190208','19020C','19022C'].includes(last.requestHex))
    || typeof last.pduHex!=="string" || !/^(?:[0-9A-F]{2}){2,4095}$/.test(last.pduHex)
    || decoded.rawHex!==last.pduHex) return false;
  const pdu=Buffer.from(last.pduHex,'hex'), kwp=type.startsWith('kwp-dtc-18');
  if (kwp ? pdu[0]!==0x58 || (type==='kwp-dtc-18-length'?(pdu.length-2)%3!==0:pdu.length!==2+pdu[1]*3)
    : pdu.length<3 || pdu[0]!==0x59 || pdu[1]!==2 || (pdu.length-3)%4!==0
      || decoded.statusAvailabilityMaskHex!==pdu.subarray(2,3).toString('hex').toUpperCase()) return false;
  const stride=kwp?3:4, start=kwp?2:3, count=(pdu.length-start)/stride;
  if (decoded.records.length!==count) return false;
  for (let i=0;i<count;i++) {
    const bytes=pdu.subarray(start+i*stride,start+(i+1)*stride), row=decoded.records[i];
    const code=bytes.subarray(0,stride-1).toString('hex').toUpperCase(), status=bytes.subarray(stride-1).toString('hex').toUpperCase();
    const display=kwp?'PCBU'[bytes[0]>>6]+String((bytes[0]>>4)&3)+(bytes[0]&15).toString(16).toUpperCase()+bytes.subarray(1,2).toString('hex').toUpperCase():code;
    if (row?.dtcHex!==code || row.statusHex!==status || row.displayCode!==display
      || typeof row.dictionaryMatch!=="boolean" || row.originalEnglish!==null && (typeof row.originalEnglish!=="string"
        || !/[A-Za-z]/.test(row.originalEnglish) || /[\u3400-\u9fff]/.test(row.originalEnglish))) return false;
  }
  return true;
}

/** Check worker evidence before retaining values or removing ordinary raw PDUs.
 * This proves internal request/response association, not an independent decode
 * or vehicle qualification. The Python client still owns the pinned read plan.
 */
export function validateManufacturerEvidence(cycle, prior = {}) {
  if (!Array.isArray(cycle?.transactions) || !Array.isArray(cycle.samples)) return null;
  const requests = new Map(), bindings = new Map(prior.parameterRequests || []);
  const unavailable = new Map(prior.unavailableParameters || []);
  const invalid = new Map(prior.invalidParameters || []), seen = new Set();
  const rejected = cycle.unavailableParameters ?? [];
  const invalidValues = cycle.invalidParameters ?? [];
  if (!Array.isArray(rejected) || !Array.isArray(invalidValues)) return null;
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
    requests.set(transaction.requestHex, { transaction, samples: 0, unavailable: 0, invalid: 0,
      unavailableReply: !transaction.ok && unavailableReply(request, pdu), nrc: pdu[2] });
    elapsedMs = transaction.elapsedMs; connectionEpoch = transaction.connectionEpoch;
  }
  for (const sample of cycle.samples) {
    const group = requests.get(sample?.requestHex), transaction = group?.transaction;
    if (!transaction?.ok || typeof sample.parameterId !== "string" || seen.has(sample.parameterId)
      || unavailable.has(sample.parameterId) || sample.pduHex !== undefined || sample.pduSha256 !== transaction.pduSha256
      || sample.processedUtc !== transaction.processedUtc || sample.elapsedMs !== transaction.elapsedMs
      || sample.connectionEpoch !== transaction.connectionEpoch || sample.timestampSource !== transaction.timestampSource
      || sample.sourceKind !== cycle.sourceKind
      || bindings.has(sample.parameterId) && bindings.get(sample.parameterId) !== sample.requestHex) return null;
    seen.add(sample.parameterId); invalid.delete(sample.parameterId);
    bindings.set(sample.parameterId, sample.requestHex); group.samples++;
  }
  for (const entry of rejected) {
    const group = requests.get(entry?.requestHex), transaction = group?.transaction;
    if (!group?.unavailableReply || transaction.error !== "parameter-unavailable"
      || entry.reason !== "parameter-unavailable" || entry.nrc !== group.nrc
      || typeof entry.parameterId !== "string" || seen.has(entry.parameterId) || unavailable.has(entry.parameterId)
      || entry.pduHex !== undefined || entry.pduSha256 !== transaction.pduSha256
      || entry.processedUtc !== transaction.processedUtc || entry.elapsedMs !== transaction.elapsedMs
      || entry.connectionEpoch !== transaction.connectionEpoch || entry.timestampSource !== transaction.timestampSource
      || entry.cycle !== cycle.cycle || entry.sourceKind !== cycle.sourceKind
      || bindings.has(entry.parameterId) && bindings.get(entry.parameterId) !== entry.requestHex) return null;
    seen.add(entry.parameterId); invalid.delete(entry.parameterId);
    bindings.set(entry.parameterId, entry.requestHex); unavailable.set(entry.parameterId, entry); group.unavailable++;
  }
  for (const entry of invalidValues) {
    const group = requests.get(entry?.requestHex), transaction = group?.transaction;
    if (!transaction?.ok || !invalidReasons.has(entry.reason)
      || typeof entry.parameterId !== "string" || seen.has(entry.parameterId) || unavailable.has(entry.parameterId)
      || entry.pduHex !== undefined || entry.pduSha256 !== transaction.pduSha256
      || entry.processedUtc !== transaction.processedUtc || entry.elapsedMs !== transaction.elapsedMs
      || entry.connectionEpoch !== transaction.connectionEpoch || entry.timestampSource !== transaction.timestampSource
      || entry.cycle !== cycle.cycle || entry.sourceKind !== cycle.sourceKind
      || bindings.has(entry.parameterId) && bindings.get(entry.parameterId) !== entry.requestHex) return null;
    seen.add(entry.parameterId); bindings.set(entry.parameterId, entry.requestHex);
    invalid.set(entry.parameterId, entry); group.invalid++;
  }
  if ([...requests.values()].some(group => group.transaction.ok !== (group.samples + group.invalid > 0))) return null;
  if (cycle.ok && (!requests.size || [...requests.values()].some(group => !group.transaction.ok && !group.unavailable))) return null;
  return { parameterRequests: bindings, unavailableParameters: unavailable, invalidParameters: invalid, elapsedMs, connectionEpoch };
}
