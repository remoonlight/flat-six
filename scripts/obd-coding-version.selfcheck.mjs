/** Cross-language source selectors, original field order and complete replies. */
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import path from 'node:path';
import {pathToFileURL} from 'node:url';
import {createHash} from 'node:crypto';
const root=path.resolve(process.env.PORSCHE981_ACCEPT_APP_ROOT||'.');
const task=path.resolve(process.env.PORSCHE981_CODING_CHECK_OUTPUT||'.local/obd-rdk-coding-source-20261018');
const file=path.join(root,'apps/desktop/electron/uds-coding-sources.mjs');
const {UDS_CODING_SOURCES,matchesUdsCodingVersion}=await import(pathToFileURL(file));
const fixture=JSON.parse(await fs.readFile(path.join(task,'selector-cases.json'),'utf8'));
const sha=b=>createHash('sha256').update(b).digest('hex');
assert.equal(fixture.scopesSha256,sha(await fs.readFile(path.join(root,'scripts/diagnostics/uds-coding-scopes.json'))));
const profiles=new Set();
for(const c of fixture.cases){
 const source=UDS_CODING_SOURCES[c.profileId],contract=structuredClone(source),seen=new Map(Object.entries(c.pdus).map(([h,p])=>[h,[p]]));
 if(c.duplicate)seen.set(c.duplicate,[c.pdus[c.duplicate],c.pdus[c.duplicate]]);
 if(c.changedContract)contract.versionSelectors[0].selector.byteOffsetSigned++;
 assert.equal(matchesUdsCodingVersion(source,contract,seen),c.expected,`${c.profileId} ${c.rule} ${c.label}`);
 profiles.add(c.profileId);
}
assert.equal(profiles.size,fixture.profiles);
await fs.writeFile(path.join(task,process.env.PORSCHE981_ACCEPT_APP_ROOT?'installed-selector-node-result.json':'selector-node-result.json'),JSON.stringify({passed:true,profiles:profiles.size,cases:fixture.cases.length,fixtureSha256:sha(await fs.readFile(path.join(task,'selector-cases.json'))),codeSha256:sha(await fs.readFile(file)),deviceIO:false,vehicleIO:false},null,2)+'\n');
console.log('PASS coding source selector/rule cross-check',profiles.size,'profiles',fixture.cases.length,'cases');
