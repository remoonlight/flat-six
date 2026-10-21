import { createHash } from 'node:crypto';
import { SWSG_CODING_SOURCES } from './swsg-coding-sources.mjs';
import { GATEWAY_CODING_SOURCES } from './gateway-coding-sources.mjs';
import { DME_CODING_SOURCES } from './dme-coding-sources.mjs';
import { PDK_CODING_SOURCES } from './pdk-coding-sources.mjs';
import { UDS_CODING_SOURCES, udsCodingSourceForUnit } from './uds-coding-sources.mjs';
const sha = value => createHash('sha256').update(value).digest('hex');
const keys = ['generation', 'vin', 'ecu', 'hardware', 'software'];
const text = v => typeof v === 'string' && v.length > 0 && v.length <= 240;
const same = (a, b) => JSON.stringify(a) === JSON.stringify(b);

/** Stored source reads have their own version. The file-import normalizer does
 * not accept it; only the owned reader can create these records. */
export function normalizeSourceCodingBackup(input) {
  const gateway = Object.hasOwn(GATEWAY_CODING_SOURCES,input?.profileId) && GATEWAY_CODING_SOURCES[input.profileId];
  const dme = Object.hasOwn(DME_CODING_SOURCES,input?.profileId) && DME_CODING_SOURCES[input.profileId];
  const pdk = Object.hasOwn(PDK_CODING_SOURCES,input?.profileId) && PDK_CODING_SOURCES[input.profileId];
  const declared = Object.hasOwn(UDS_CODING_SOURCES,input?.profileId) && UDS_CODING_SOURCES[input.profileId];
  const uds = declared?.contexts ? udsCodingSourceForUnit(input.profileId,input.targetEcuId) : declared;
  const kwp = !!(dme || pdk || uds?.sourceQualification === 'kwp-source-version-and-identity');
  const source = uds || dme || pdk || gateway || (Object.hasOwn(SWSG_CODING_SOURCES,input?.profileId) && SWSG_CODING_SOURCES[input.profileId]);
  if (!source || input.schemaVersion !== 3 || input.kind !== 'ecu-coding-backup'
    || !input.identity || !keys.every(k => text(input.identity[k]))
    || !['981', '982'].includes(input.identity.generation) || !/^[A-HJ-NPR-Z0-9]{17}$/.test(input.identity.vin)
    || !(uds ? [uds.workspaceEcu] : dme ? ['dme'] : pdk ? ['pdk'] : gateway ? ['gateway'] : ['headlamp-left', 'headlamp-right']).includes(input.identity.ecu)
    || dme && input.identity.generation !== '981'
    || uds && !uds.generations.includes(input.identity.generation)
    || input.provenance !== 'source-vehicle-read' || input.freshReadProven !== true || input.liveVerified !== false
    || input.completeness !== 'source-qualified-complete-record-set'
    || input.sourceHash !== source.definitionSha256 || input.scopeHash !== source.scopeSha256
    || uds?.hardwareIdentity && (!input.identity.hardware.startsWith(uds.hardwareIdentity.prefix)
      || input.identity.hardware.length <= uds.hardwareIdentity.prefix.length)
    || uds?.softwareIdentityKind === 'diagnostic-version'
      && input.identity.software !== 'KWP-DSN:' + uds.identityConstraints.dsnHex
    || uds?.softwareIdentityKind === 'asam-odx-version'
      && input.identity.software !== 'ASAM/ODX:' + uds.identityConstraints.versionRules[0]
    || gateway && gateway.softwareIdentityKind === 'asam-odx-version'
      && input.identity.software !== 'ASAM/ODX:' + gateway.identityConstraints.versionRules[0].split('#')[0]
    || !text(input.capturedUtc) || !Number.isFinite(Date.parse(input.capturedUtc))) throw new Error('backup-source-invalid');
  const all = uds || kwp || gateway ? Object.fromEntries(source.completeScopeSource.requestLengths.map(v=>[v.requestHex.slice(2),v.bytes]))
    : {'04A1':137,'04A2':27,'04A3':57,'04A4':29,'04A5':17,'04A6':42,'04A7':11,'04A8':54,'04A9':33,'04AA':26,'04AB':61,'04B0':30,'04FE':3,'0600':3};
  if (!uds && !gateway && !kwp && input.profileId.endsWith('_VR06')) delete all['04AB'];
  if (!uds && !gateway && !kwp && input.profileId.includes(':SWSG_Slave_')) delete all['04B0'];
  const kinds = new Map((source.completeScopeSource?.requestLengths || [])
    .map(r=>[r.requestHex.slice(2),r.requestHex.startsWith('21') ? 'LID' : 'DID']));
  if (!Array.isArray(input.blocks) || input.blocks.length !== Object.keys(all).length) throw new Error('backup-source-coverage-invalid');
  const blocks = input.blocks.map(b => {
    const kind = kinds.get(b?.identifierHex) || 'DID';
    if (!b || b.identifierKind !== kind || !Object.hasOwn(all, b.identifierHex)
      || typeof b.dataHex !== 'string' || !/^(?:[0-9A-F]{2})+$/.test(b.dataHex) || b.dataHex.length !== all[b.identifierHex] * 2
      || b.pduHex !== (kind === 'LID' ? '61' : '62') + b.identifierHex + b.dataHex || b.sha256 !== sha(Buffer.from(b.dataHex, 'hex')))
      throw new Error('backup-source-block-invalid');
    return { identifierKind:kind, identifierHex:b.identifierHex, dataHex:b.dataHex, pduHex:b.pduHex, sha256:b.sha256 };
  }).sort((a, b) => a.identifierHex.localeCompare(b.identifierHex));
  if (!same(blocks.map(b => b.identifierHex), Object.keys(all).sort())) throw new Error('backup-source-coverage-invalid');
  return { schemaVersion:3, kind:'ecu-coding-backup', identity:Object.fromEntries(keys.map(k => [k, input.identity[k]])),
    ...(uds?.contexts ? {targetEcuId:uds.ecuId} : {}),
    profileId:input.profileId, sourceHash:input.sourceHash, scopeHash:input.scopeHash,
    expectedBlocks:blocks.map(({identifierKind,identifierHex}) => ({identifierKind,identifierHex})), blocks,
    capturedUtc:input.capturedUtc, provenance:'source-vehicle-read', freshReadProven:true,
    completeness:'source-qualified-complete-record-set', liveVerified:false };
}
