import {readFileSync} from 'node:fs';
const document = JSON.parse(readFileSync(new URL('../../../scripts/diagnostics/pdk-coding-scopes.json',import.meta.url),'utf8'));
if (document.schemaVersion !== 1 || document.kind !== 'reviewed-pdk-coding-read-scopes' || document.profiles.length !== 8)
  throw new Error('coding-source-manifest-invalid');
export const PDK_CODING_SOURCES = Object.freeze(Object.fromEntries(document.profiles.map(row=>[row.profileId,
  {...row,scopeSha256:row.completeScopeSource.sha256,identityRequests:row.identityFields.map(v=>v.requestHex)}])));
