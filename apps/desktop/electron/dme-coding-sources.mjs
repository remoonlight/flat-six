import {readFileSync} from 'node:fs';
const document = JSON.parse(readFileSync(new URL('../../../scripts/diagnostics/dme-coding-scopes.json',import.meta.url),'utf8'));
if (document.schemaVersion !== 1 || document.kind !== 'reviewed-dme-coding-read-scopes' || document.profiles.length !== 12)
  throw new Error('coding-source-manifest-invalid');
export const DME_CODING_SOURCES = Object.freeze(Object.fromEntries(document.profiles.map(row=>[row.profileId,
  {...row,scopeSha256:row.completeScopeSource.sha256,identityRequests:row.identityFields.map(v=>v.requestHex)}])));
