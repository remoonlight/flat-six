import {readFileSync} from 'node:fs';
import {isDeepStrictEqual} from 'node:util';
const document = JSON.parse(readFileSync(new URL('../../../scripts/diagnostics/uds-coding-scopes.json',import.meta.url),'utf8'));
if (document.schemaVersion !== 1 || document.kind !== 'reviewed-source-coding-read-scopes' || document.profiles.length !== 154)
  throw new Error('coding-source-manifest-invalid');
const decorate = row => ({...row,scopeSha256:row.completeScopeSource.sha256,identityRequests:row.identityFields.map(v=>v.requestHex)});
export const UDS_CODING_SOURCES = Object.freeze(Object.fromEntries(document.profiles.map(row=>[row.profileId,decorate(row)])));
export const UDS_CODING_CONTEXTS = Object.freeze(document.profiles.flatMap(row => {
  if (!row.contexts) return [decorate(row)];
  if (!Array.isArray(row.ecuIds) || new Set(row.ecuIds).size !== row.ecuIds.length
    || !isDeepStrictEqual(row.contexts.map(context=>context.ecuId),row.ecuIds)) throw new Error('coding-source-context-invalid');
  return row.contexts.map(context=>decorate({...row,...context}));
}));
export const udsCodingSourceForUnit = (profileId,ecuId) => UDS_CODING_CONTEXTS.find(row=>row.profileId===profileId&&row.ecuId===ecuId);

/** The reviewed selectors retain their original order: it differs by unit.
 * Formula aliases are the exact source formats, not version-number guesses. */
export function matchesUdsCodingVersion(source, contract, observed) {
  if (!source.versionSelectors || !isDeepStrictEqual(contract.versionSelectors, source.versionSelectors)) return false;
  const values = [];
  for (const field of source.versionSelectors) {
    const replies = observed.get(field.requestHex), selector = field.selector;
    if (replies?.length !== 1 || !replies[0].startsWith('62' + field.requestHex.slice(2))) return false;
    const vector = Buffer.from(replies[0].slice(2), 'hex'), offset = selector.byteOffsetSigned;
    if (!Number.isInteger(offset) || offset < 0 || offset > vector.length) return false;
    let value;
    if (selector.formulaId === 0xA0000001 && [0, 2, 6].includes(selector.bitOffsetByte)) {
      if (offset === vector.length) return false;
      value = vector[offset] & (1 << selector.bitOffsetByte) ? 'ja' : 'nein';
    } else {
      if (selector.bitOffsetByte !== 0) return false;
      const widths = {0x80000001:5, 0x80000002:6, 0x80000007:4, 0x8000000A:3, 0xF0000009:3, 0x40000008:3};
      const width = widths[selector.formulaId];
      let bytes;
      if (width) {
        if (vector.length - offset < width) return false;
        bytes = vector.subarray(offset, offset + width);
      } else if ([0x8000002A,0x8000021D,0x80000423,0xF000000D,0x400001E4].includes(selector.formulaId)) {
        const [minimum, maximum] = [0x8000021D,0xF000000D,0x400001E4].includes(selector.formulaId) ? [3,25]
          : selector.formulaId === 0x80000423 ? [6,31] : [6,26];
        const window = vector.subarray(offset, offset + maximum), end = window.indexOf(0);
        // Match the source's MinMax semantics using only observed bytes.
        // Below MinLength it yields an empty value; a DSN-only rule leaves
        // that later field unconstrained. No terminator is invented.
        if (window.length < minimum) bytes = Buffer.alloc(0);
        else bytes = window.subarray(0, end < 0 ? window.length : end);
      } else return false;
      if ([...bytes].some(byte => byte >= 128)) return false;
      value = bytes.toString('ascii').split('\0')[0];
    }
    if (value.length >= 256) return false;
    values.push(value);
  }
  const matches = rules => rules.some(rule => {
    const parts = rule.split('#');
    if (rule.endsWith('#')) parts.pop();
    return parts.length > 0 && parts.length <= values.length
      && parts.every((part, index) => part === '' || part === values[index]);
  });
  return matches(source.identityConstraints.versionRules)
    && !matches(source.identityConstraints.conflictingVersionRules || []);
}
