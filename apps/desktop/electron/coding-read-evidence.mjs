import { createHash } from 'node:crypto';
import { readFileSync } from 'node:fs';
import { SWSG_CODING_SOURCES } from './swsg-coding-sources.mjs';
import { GATEWAY_CODING_SOURCES } from './gateway-coding-sources.mjs';
import { DME_CODING_SOURCES } from './dme-coding-sources.mjs';
import { PDK_CODING_SOURCES } from './pdk-coding-sources.mjs';
import { udsCodingSourceForUnit, matchesUdsCodingVersion } from './uds-coding-sources.mjs';
import { validManufacturerIdentityInfo } from './manufacturer-evidence.mjs';

const sha = value => createHash('sha256').update(value).digest('hex');
const catalog = '31bf93245fffc375b757fc19ba03491e101ccabc838c9ee742d70a88cb7805ba';
const hex = value => typeof value === 'string' && /^(?:[0-9A-F]{2})+$/.test(value);
const text = value => typeof value === 'string' && value.length > 0 && value.length <= 4096;
const canonical = value => Array.isArray(value) ? '[' + value.map(canonical).join(', ') + ']'
  : value && typeof value === 'object' ? '{' + Object.keys(value).sort().map(k => JSON.stringify(k) + ': ' + canonical(value[k])).join(', ') + '}' : JSON.stringify(value);
const gatewayIdentities = JSON.parse(readFileSync(new URL('./gateway-identity-sources.json', import.meta.url), 'utf8'));
if (gatewayIdentities.schemaVersion !== 1 || gatewayIdentities.kind !== 'reviewed-gateway-identity-contracts'
  || gatewayIdentities.sourceCatalogId !== catalog || gatewayIdentities.profiles.length !== 16)
  throw new Error('coding-gateway-identity-manifest-invalid');
const gatewayIdentitySources = new Map(gatewayIdentities.profiles.map(row => [row.profileId, row]));
const codingAnchors = new WeakMap();

/** Private worker boundary: qualify gateway bytes before starting the target.
 * This does not need a complete gateway coding scope (including A7_1).
 * A JSON DTO cannot recreate the returned, process-local proof token. */
export function validateCodingVehicleAnchor(doc, observed, generation, expectedVehicleHash) {
  const contract = doc?.readContract, q = doc?.transport?.qualification;
  const source = gatewayIdentitySources.get(doc?.identifiedProfileId);
  if (!source || !contract || !q || doc.ok !== true || doc.identityOnly !== true
    || doc.codingOnly === true || doc.dtcOnly === true || doc.simulation !== false
    || doc.executionEnabled !== true || doc.liveVerified !== false || doc.writePayload !== null
    || doc.sourceKind !== 'independent-manufacturer-read' || doc.vehicleDataCollected !== false
    || doc.completedCycles !== 0 || doc.requestCount !== 0 || doc.vehicleGeneration !== generation
    || !['981','982'].includes(generation) || !/^[a-f0-9]{64}$/.test(expectedVehicleHash || '')
    || doc.vehicleIdentitySha256 !== expectedVehicleHash
    || doc.transport.openAttempted !== true || doc.transport.closed !== true
    || !Array.isArray(doc.transport.closeErrors) || doc.transport.closeErrors.length
    || q.ecuId !== 9 || q.generation !== '981' || q.sourceCatalogId !== catalog
    || q.freshRead !== true || q.observedProfileMatch !== true || q.variantLink?.profile_id !== doc.identifiedProfileId
    || contract.ecuId !== 9 || contract.generation !== '981' || contract.profileId !== doc.identifiedProfileId
    || contract.sourceCatalogId !== catalog || !Array.isArray(contract.groups) || contract.groups.length
    || sha(canonical(Object.fromEntries(gatewayIdentities.contractFields.map(key => [key, contract[key]])))) !== source.identityContractSha256
    || !matchesUdsCodingVersion(contract, contract, observed)) throw new Error('coding-read-anchor-invalid');
  const hashes = new Map([...observed].map(([key, rows]) => [key, sha(Buffer.from(rows.at(-1), 'hex'))]));
  if (!validManufacturerIdentityInfo(doc, hashes) || doc.identityInfo.length !== contract.identityRequests.length
    || contract.identityRequests.some(row => observed.get(row.requestHex)?.length !== 1
      || !observed.get(row.requestHex)[0].startsWith('62' + row.requestHex.slice(2))
      || doc.identityInfo.filter(info => info.requestHex === row.requestHex && info.recordIndex === 0).length !== 1))
    throw new Error('coding-read-anchor-invalid');
  const pdus = observed.get('22F190');
  if (pdus?.length !== 1 || !/^62F190(?:[0-9A-F]{2}){17}$/.test(pdus[0])) throw new Error('coding-read-anchor-invalid');
  const vin = Buffer.from(pdus[0].slice(6), 'hex').toString('latin1');
  const model = vin.slice(6,8) + vin[11];
  const observedGeneration = /^WP0[A-HJ-NPR-Z0-9]{14}$/.test(vin)
    ? ['981','A81'].includes(model) && 'DEFG'.includes(vin[9]) ? '981'
      : ['982','A82'].includes(model) && 'HJKLMNPRSTVW'.includes(vin[9]) ? '982' : null : null;
  if (observedGeneration !== generation || sha(vin) !== expectedVehicleHash) throw new Error('coding-read-anchor-invalid');
  const token = Object.freeze({ generation, vin });
  codingAnchors.set(token, Object.freeze({ecuId:9,profileId:doc.identifiedProfileId,sourceCatalogId:catalog,
    vehicleGeneration:generation,vehicleIdentitySha256:expectedVehicleHash,freshRead:true,observedProfileMatch:true,closed:true}));
  return token;
}

/** Called on the owned worker's frames, before any persistence or renderer DTO.
 * Display labels never establish provenance, VIN or complete scope. */
export function validateCompleteCodingRead(doc, observed, anchorToken) {
  const c = doc?.codingRead, contract = doc?.readContract, q = doc?.transport?.qualification;
  const gateway = Object.hasOwn(GATEWAY_CODING_SOURCES,c?.profileId) && GATEWAY_CODING_SOURCES[c.profileId];
  const dme = Object.hasOwn(DME_CODING_SOURCES,c?.profileId) && DME_CODING_SOURCES[c.profileId];
  const pdk = Object.hasOwn(PDK_CODING_SOURCES,c?.profileId) && PDK_CODING_SOURCES[c.profileId];
  const uds = udsCodingSourceForUnit(c?.profileId,c?.ecuId);
  const kwp = !!(dme || pdk || uds?.sourceQualification === 'kwp-source-version-and-identity');
  const media = uds && [65,70,75].includes(uds.ecuId);
  const udsEngine = uds?.ecuId === 1;
  const sourceLink = uds?.sourceLinks?.find(link => link.linkIndex === doc?.sourceRouteDiscovery?.selectedSourceLink);
  const source = uds || dme || pdk || gateway || (Object.hasOwn(SWSG_CODING_SOURCES,c?.profileId) && SWSG_CODING_SOURCES[c.profileId]);
  if (!source || !c || !contract || !q || doc.ok !== true || doc.codingOnly !== true || doc.identityOnly !== false
    || doc.simulation !== false || doc.executionEnabled !== true || doc.liveVerified !== false || doc.writePayload !== null
    || doc.sourceKind !== 'independent-manufacturer-read' || doc.vehicleDataCollected !== true
    || c.ok !== true || c.freshReadProven !== true || c.completeOriginalRead !== true
    || c.allDefinedGroupsReceived !== true || c.completeVehicleCodingScopeQualified !== true
    || c.completeOriginalBackup !== false || c.baselineCreated !== false || c.codingWriteQualified !== false
    || c.codingRestoreQualified !== false || c.liveVerified !== false || c.writePayload !== null
    || !Array.isArray(c.missing) || c.missing.length || !['981', '982'].includes(c.generation)
    || !(uds ? [uds.ecuId] : dme ? [1] : pdk ? [2] : gateway ? [9] : [47, 48]).includes(c.ecuId) || dme && c.generation !== '981'
    || uds && !uds.generations.includes(c.generation)
    || c.profileId !== doc.identifiedProfileId || contract.profileId !== c.profileId
    || c.ecuId !== contract.ecuId || c.generation !== contract.generation || c.generation !== doc.vehicleGeneration
    || c.sourceCatalogId !== catalog || contract.sourceCatalogId !== catalog || q.sourceCatalogId !== catalog
    || q.freshRead !== true || q.observedProfileMatch !== true || q.generation !== c.generation
    || q.ecuId !== c.ecuId || q.variantLink?.profile_id !== c.profileId
    || contract.sourceQualification !== (uds ? uds.sourceQualification : dme ? 'kwp-source-version-and-identity' : pdk ? 'pdk-dsn-and-full-identity' : 'uds-source-version-and-identity')
    || contract.applicationProtocol !== (uds ? uds.applicationProtocol : dme ? 'kwp' : pdk ? undefined : 'uds')
    || contract.txId !== (uds ? (sourceLink || uds).txId : dme ? 0x7E0 : pdk ? 0x71E : gateway ? 0x710 : c.ecuId === 47 ? 0x725 : 0x754)
    || contract.rxId !== (uds ? (sourceLink || uds).rxId : dme ? 0x7E8 : pdk ? 0x788 : gateway ? 0x77A : c.ecuId === 47 ? 0x78F : 0x7BE)
    || uds?.transportAddressing && canonical(contract.transportAddressing) !== canonical(uds.transportAddressing)
    || c.definitionSha256 !== source.definitionSha256 || c.definitionFieldCount !== source.definitionFieldCount
    || !Array.isArray(contract.groups) || contract.groups.length || doc.completedCycles !== 0 || doc.requestCount !== 0)
    throw new Error('coding-read-evidence-invalid');
  const scope = c.completeScopeSource;
  if (!scope || scope.sha256 !== source.scopeSha256 || sha(canonical(Object.fromEntries(Object.entries(scope).filter(([key]) => key !== 'sha256')))) !== source.scopeSha256
    || !Array.isArray(scope.requestLengths) || !Array.isArray(c.blocks) || c.blocks.length !== scope.requestLengths.length)
    throw new Error('coding-read-scope-invalid');
  const hashes = new Map([...observed].map(([key, rows]) => [key, sha(Buffer.from(rows.at(-1), 'hex'))]));
  const identityRequests = uds ? uds.identityRequests : dme ? dme.identityRequests : pdk ? pdk.identityRequests : gateway ? gateway.identityRequests
    : ['22F1A2','22F190','22F1A3','22F187','220906','22F189'];
  if (dme || udsEngine) {
    const discovery = doc.sourceRouteDiscovery, probes = discovery?.probes;
    const rejected = ['NO DATA','no-data','negative-response','identity-mismatch','identity-decode-failed','wrong-sid-or-did-echo'];
    if (discovery?.applicationProtocol !== (udsEngine ? 'uds' : 'kwp') || discovery.uniqueMatch !== true || !Array.isArray(probes)
      || probes.length !== 2 || probes.some((probe,i) => probe.applicationProtocol !== (i ? 'uds' : 'kwp')
        || probe.ok !== (udsEngine ? i === 1 : i === 0)
        || (probe.ok ? probe.error !== null : !rejected.includes(probe.error))))
      throw new Error('coding-read-identity-invalid');
  }
  if (media) {
    // Media families share a CAN pair. The worker probes each original
    // protocol independently, then opens the unique matching route afresh.
    const discovery = doc.sourceRouteDiscovery, probes = discovery?.probes;
    const bose = c.profileId === '9x1:BV_SoundSysteUDS:EV_AMPMst16C4Gen2BOSE_002';
    const expected = [{applicationProtocol:'kwp',ok:kwp},
      {applicationProtocol:'uds',ok:!kwp && !bose}];
    if (uds.ecuId === 65) expected.push({applicationProtocol:'uds',sourceBridge:'piwis-bose002',ok:bose});
    const rejected = ['NO DATA','no-data','negative-response','identity-mismatch',
      'identity-decode-failed','wrong-sid-or-did-echo'];
    if (discovery?.applicationProtocol !== (kwp ? 'kwp' : 'uds') || discovery.uniqueMatch !== true
      || discovery.sourceBridge !== (bose ? 'piwis-bose002' : undefined)
      || !Array.isArray(probes) || probes.length !== expected.length
      || probes.some((probe,i) => probe.applicationProtocol !== expected[i].applicationProtocol
        || probe.sourceBridge !== expected[i].sourceBridge || probe.ok !== expected[i].ok
        || (probe.ok ? probe.error !== null : !rejected.includes(probe.error))))
      throw new Error('coding-read-identity-invalid');
  }
  if (uds?.sourceLinks) {
    const discovery = doc.sourceRouteDiscovery, probes = discovery?.probes;
    const rejected = ['NO DATA','no-data','negative-response','identity-mismatch','identity-decode-failed','wrong-sid-or-did-echo'];
    if (!sourceLink || discovery.uniqueMatch !== true || !Array.isArray(probes) || probes.length !== uds.sourceLinks.length
      || probes.some((probe,i) => probe.sourceLink !== uds.sourceLinks[i].linkIndex
        || probe.ok !== (i === sourceLink.linkIndex)
        || (probe.ok ? probe.error !== null : !rejected.includes(probe.error))))
      throw new Error('coding-read-identity-invalid');
  }
  const identityCopies = dme || media || udsEngine || sourceLink ? 2 : 1;
  const identityFields = uds?.identityLayout
    ? uds.identityLayout.flatMap(group => (group.records || [group.record]).map((record, recordIndex) => ({ requestHex:group.requestHex, recordIndex })))
    : identityRequests.map(requestHex => ({ requestHex, recordIndex:0 }));
  if (!validManufacturerIdentityInfo(doc, hashes) || doc.identityInfo.length !== identityFields.length
    || JSON.stringify(contract.identityRequests.map(r => r.requestHex)) !== JSON.stringify(identityRequests)
    || uds?.identityLayout && canonical(contract.identityRequests) !== canonical(uds.identityLayout)
    || identityFields.some(field => doc.identityInfo.filter(r => r.requestHex === field.requestHex && r.recordIndex === field.recordIndex).length !== 1)
    || identityRequests.some(h => observed.get(h)?.length !== identityCopies
      || observed.get(h).some(pdu=>pdu !== observed.get(h)[0])
      || !observed.get(h)[0].startsWith((kwp ? '5A' : '62') + h.slice(2)))) throw new Error('coding-read-identity-invalid');
  const unitVin = identityRequests.includes(kwp ? '1A90' : '22F190');
  if (doc.vehicleAnchor !== undefined || !unitVin || anchorToken !== undefined) {
    const proof = codingAnchors.get(anchorToken);
    if (!proof || canonical(proof) !== canonical(doc.vehicleAnchor)
      || proof.vehicleGeneration !== c.generation || proof.vehicleIdentitySha256 !== doc.vehicleIdentitySha256)
      throw new Error('coding-read-vin-invalid');
  }
  const vinPdus = observed.get(kwp ? '1A90' : '22F190');
  if (unitVin ? !vinPdus || vinPdus.length !== identityCopies
      || !(kwp ? /^5A90(?:[0-9A-F]{2}){17}$/ : /^62F190(?:[0-9A-F]{2}){17}$/).test(vinPdus[0])
    : observed.has('1A90') || observed.has('22F190')) throw new Error('coding-read-vin-invalid');
  const vin = unitVin ? Buffer.from(vinPdus[0].slice(kwp ? 4 : 6), 'hex').toString('latin1') : anchorToken.vin;
  // Python performs the model-year/981/982 rule; independently bind its full VIN.
  if (!/^[A-HJ-NPR-Z0-9]{17}$/.test(vin) || sha(vin) !== doc.vehicleIdentitySha256
    || doc.vehicleIdentitySource !== (unitVin ? 'unit-vin' : 'gateway-vin')) throw new Error('coding-read-vin-invalid');
  const get = request => doc.identityInfo.filter(r => r.requestHex === request && r.recordIndex === 0);
  const hardwareRequest = uds?.hardwareIdentity?.requestHex || (kwp ? '1A91' : '22F1A3');
  const softwareRequest = uds?.softwareIdentity?.requestHex || (kwp ? '1A95' : '22F189');
  const hw = get(hardwareRequest), sw = get(softwareRequest);
  const revisionMatches = (request, row, width, maximumWidth = width) => {
    const bytes = Buffer.from(observed.get(request)[0].slice(kwp ? 4 : 6), 'hex');
    return bytes.length >= width && new TextDecoder('iso-8859-2', {fatal:true}).decode(bytes.subarray(0,maximumWidth)).split('\0')[0] === row?.value;
  };
  const variant = c.profileId.split(':').at(-1);
  const dsn = (variant.includes('_Master_') ? '1' : '0') + (variant.includes('_ohne_AFS_') ? '05' : '00')
    + (variant.endsWith('_1310') ? '010' : variant.endsWith('_VR06') ? '006' : '015');
  if (uds) {
    if (kwp) {
      if (contract.identityLayoutResolvedByDsn !== true || uds.identityLayoutResolvedByDsn !== true
        || observed.get('1A9F')[0] !== '5A9F' + uds.identityConstraints.dsnHex
        || get('1A9F')[0].value.toUpperCase() !== uds.identityConstraints.dsnHex)
        throw new Error('coding-read-identity-invalid');
    } else if (uds.versionSelectors) {
      let selectors = observed;
      if (media || udsEngine || sourceLink) {
        selectors = new Map(observed);
        for (const field of uds.versionSelectors) {
          const rows = observed.get(field.requestHex), copies = uds.ecuId === 65 ? 3 : 2;
          if (rows?.length !== copies || rows.some(pdu => pdu !== rows[0]))
            throw new Error('coding-read-identity-invalid');
          selectors.set(field.requestHex,[rows[0]]);
        }
      }
      if (!matchesUdsCodingVersion(uds, contract, selectors)) throw new Error('coding-read-identity-invalid');
    } else {
    const version = Buffer.from(observed.get('22F1A2')[0].slice(6),'hex').subarray(0,6).toString('latin1');
    const rules = uds.identityConstraints.dsnText ? [uds.identityConstraints.dsnText] : uds.identityConstraints.versionRules;
    let selectedVersion = version;
    if (rules.every(rule=>rule.includes('#'))) {
      const replies=observed.get('22F19E');
      if (replies?.length!==1 || !replies[0].startsWith('62F19E')) throw new Error('coding-read-identity-invalid');
      const bytes=Buffer.from(replies[0].slice(6),'hex'),end=bytes.indexOf(0);
      if (end<=0) throw new Error('coding-read-identity-invalid');
      selectedVersion += '#' + bytes.subarray(0,end).toString('latin1');
    }
    if (canonical(contract.identityConstraints) !== canonical(uds.identityConstraints)
      || canonical(contract.sourceCommands) !== canonical(uds.sourceCommands)
      || canonical(contract.session) !== canonical(uds.session)
      || get('22F1A2')[0].value !== version || !rules.includes(selectedVersion)) throw new Error('coding-read-identity-invalid');
    }
    if (canonical(contract.identityConstraints) !== canonical(uds.identityConstraints)
      || canonical(contract.sourceCommands) !== canonical(uds.sourceCommands)
      || canonical(contract.session) !== canonical(uds.session)
      || (uds.identityLayout ? canonical(contract.identityRequests) !== canonical(uds.identityLayout)
        : contract.identityRequests.some((r,i)=>r.record?.at !== uds.identityFields[i].sourceOffset))) throw new Error('coding-read-identity-invalid');
  } else if (pdk) {
    if (canonical(contract.identityConstraints) !== canonical(source.identityConstraints)
      || canonical(contract.session) !== canonical(source.session) || contract.sourceCommands !== undefined
      || observed.get('1A9F')[0] !== '5A9F' + source.identityConstraints.dsnHex
      || get('1A9F')[0].value.toUpperCase() !== source.identityConstraints.dsnHex
      || contract.identityRequests.some((r,i)=>r.record?.at !== source.identityFields[i].sourceOffset)) throw new Error('coding-read-identity-invalid');
  } else if (dme) {
    const version = Buffer.from(observed.get('1A9F')[0].slice(4),'hex').subarray(0,4).toString('latin1');
    if (canonical(contract.identityConstraints) !== canonical(source.identityConstraints)
      || canonical(contract.sourceCommands) !== canonical(source.sourceCommands)
      || canonical(contract.session) !== canonical(source.session)
      || get('1A9F')[0].value !== version || !source.identityConstraints.versionRules.includes(version)
      || contract.identityRequests.some((r,i)=>r.record?.at !== source.identityFields[i].sourceOffset)) throw new Error('coding-read-identity-invalid');
  } else if (gateway) {
    const rules = source.identityConstraints.versionRules;
    const version = Buffer.from(observed.get('22F1A2')[0].slice(6),'hex').subarray(0,6).toString('latin1').split('\0')[0];
    const modulePdu = observed.get('22F19E');
    const module = modulePdu?.length === 1 && modulePdu[0].startsWith('62F19E')
      ? Buffer.from(modulePdu[0].slice(6),'hex').toString('latin1').split('\0')[0] : null;
    if (canonical(contract.identityConstraints) !== canonical(source.identityConstraints)
      || get('22F1A2')[0].value !== version
      || !rules.some(rule=>{const parts=rule.split('#');return parts.length===2 && parts[0]===version && parts[1]===module;})) throw new Error('coding-read-identity-invalid');
  } else if (contract.identityConstraints?.dsnText !== dsn || observed.get('22F1A2')?.at(-1) !== '62F1A2' + Buffer.from(dsn).toString('hex').toUpperCase()) throw new Error('coding-read-identity-invalid');
  const firmware = uds?.softwareIdentityKind !== 'diagnostic-version'
    && uds?.softwareIdentityKind !== 'asam-odx-version'
    && (!gateway || gateway.softwareIdentityKind === 'ecu-software-version');
  if (hw.length !== 1 || !text(hw[0].value) || !revisionMatches(hardwareRequest,hw[0],uds?.hardwareIdentity?.bytes || (kwp ? 11 : 3))
    || firmware && (sw.length !== 1 || !text(sw[0].value) || !revisionMatches(softwareRequest,sw[0],uds?.softwareIdentity?.bytes || (dme ? 15 : 4),uds?.softwareIdentity?.maximumBytes || uds?.softwareIdentity?.bytes || (dme ? 15 : 4)))) throw new Error('coding-read-identity-invalid');
  const software = uds?.softwareIdentityKind === 'asam-odx-version'
    ? 'ASAM/ODX:' + uds.identityConstraints.versionRules[0]
    : firmware ? sw[0].value : uds?.softwareIdentityKind === 'diagnostic-version'
    ? 'KWP-DSN:' + get('1A9F')[0].value.toUpperCase() : 'ASAM/ODX:' + get('22F1A2')[0].value;
  for (let i = 0; i < c.blocks.length; i++) {
    const block = c.blocks[i], expected = scope.requestLengths[i], seen = observed.get(expected.requestHex);
    const local = expected.requestHex.startsWith('21');
    if (block.requestHex !== expected.requestHex || block.identifierKind !== (local ? 'LID' : 'DID') || block.identifierHex !== expected.requestHex.slice(2)
      || !hex(block.dataHex) || block.dataHex.length !== expected.bytes * 2 || block.pduHex !== (local ? '61' : '62') + block.identifierHex + block.dataHex
      || block.sha256 !== sha(Buffer.from(block.dataHex, 'hex')) || block.completeLengthQualified !== true
      || !Number.isFinite(Date.parse(block.receivedUtc)) || !seen || seen.length !== 1 || seen[0] !== block.pduHex)
      throw new Error('coding-read-block-invalid');
  }
  const ids = new Set();
  if (!Array.isArray(c.fields) || c.fields.length !== source.definitionFieldCount || c.fields.some(f => !f || !text(f.id) || ids.has(f.id) || !ids.add(f.id)
    || !text(f.name) || !text(f.group) || typeof f.currentDisplay !== 'string' || f.currentDisplay.length > 4096 || f.editable !== false || f.currentOptionId !== null
    || !Array.isArray(f.options) || f.options.length || !text(f.description)
    || Object.keys(f).some(k => !['id', 'name', 'group', 'description', 'editable', 'currentOptionId', 'currentDisplay', 'options'].includes(k)))) throw new Error('coding-read-fields-invalid');
  return { generation: c.generation, vin, hardware: (uds?.hardwareIdentity?.prefix || '') + hw[0].value, software };
}
