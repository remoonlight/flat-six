import {readFileSync} from 'node:fs';
const document = JSON.parse(readFileSync(new URL('../../../scripts/diagnostics/gateway-coding-scopes.json',import.meta.url),'utf8'));
if (document.schemaVersion !== 1 || document.kind !== 'reviewed-gateway-coding-read-scopes' || document.profiles.length !== 15)
  throw new Error('coding-source-manifest-invalid');
// These six source identity tables include the ECU software DID. The other
// reviewed tables identify the diagnostic version instead; do not invent a
// firmware version or send an identity request absent from that source.
const firmwareVersions = new Set(['CAN_CAN_Gateway_A3','CAN_CAN_Gateway_A4','CAN_CAN_Gateway_A4_1',
  'CAN_CAN_Gateway_A4_2','CAN_CAN_Gateway_A5','CAN_CAN_Gateway_A6'].map(name=>'9x1:CAN_CAN_Gateway:'+name));
const identities = ['22F1A2','22F1A3','22F187','22F18C','22F190','22F191','22F182'];
export const GATEWAY_CODING_SOURCES = Object.freeze(Object.fromEntries(document.profiles.map(row=>[row.profileId,
  {...row,scopeSha256:row.completeScopeSource.sha256,
    identityRequests:[...identities,...(firmwareVersions.has(row.profileId)?['22F189']:[])],
    softwareIdentityKind:firmwareVersions.has(row.profileId)?'ecu-software-version':'asam-odx-version'}])));
