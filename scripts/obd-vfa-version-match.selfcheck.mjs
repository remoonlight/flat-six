/** Original VFA source contract; identity response bytes are explicit models. */
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import path from 'node:path';
import {createHash} from 'node:crypto';
import {pathToFileURL} from 'node:url';
const host=path.resolve(import.meta.dirname,'..'),root=path.resolve(process.env.PORSCHE981_ACCEPT_APP_ROOT||host);
const task=path.resolve(process.env.PORSCHE981_VFA_MODELS_ROOT||path.join(host,'.local/obd-vfa-version-match-20261019'));
const code=path.join(root,'apps/desktop/electron/uds-coding-sources.mjs');
const {UDS_CODING_SOURCES,matchesUdsCodingVersion}=await import(pathToFileURL(code));
const fixturePath=path.join(task,'selector-cases.json'),fixture=JSON.parse(await fs.readFile(fixturePath,'utf8'));
const sha=bytes=>createHash('sha256').update(bytes).digest('hex');
assert.equal(fixture.passed,true);
assert.equal(sha(await fs.readFile(path.join(host,fixture.profileFile))),fixture.profileSha256);
assert.equal(fixture.profileId,'9x1:ERA_GLONASS_VFA:EV_ERAGLONAVFA_001');
assert.deepEqual(fixture.source.versionSelectors.map(f=>f.selector.formulaId),[0x400001E4,0x40000008]);
assert.equal(UDS_CODING_SOURCES[fixture.profileId],undefined,'version matching cannot qualify incomplete original-code coverage');
assert.equal(sha(await fs.readFile(path.join(root,'scripts/diagnostics/uds-coding-scopes.json'))),fixture.scopesSha256);
assert.equal(sha(await fs.readFile(path.join(root,'apps/desktop/electron/gateway-identity-sources.json'))),fixture.gatewayIdentitiesSha256);
let positive=0;
function verifyCases(original,cases){
for(const c of cases){
  const source=structuredClone(original),contract=structuredClone(source);
  const observed=new Map(Object.entries(c.pdus).map(([hex,pdu])=>[hex,[pdu]]));
  if(c.duplicate)observed.set(c.duplicate,[c.pdus[c.duplicate],c.pdus[c.duplicate]]);
  if(c.missing)observed.delete(c.missing);
  if(c.foreign)observed.set(c.foreign,['62FFFF'+c.pdus[c.foreign].slice(6)]);
  if(c.changedContract)contract.versionSelectors[0].selector.byteOffsetSigned++;
  if(c.unknownFormula){source.versionSelectors[0].selector.formulaId=0xFFFFFFFF;contract.versionSelectors[0].selector.formulaId=0xFFFFFFFF;}
  assert.equal(matchesUdsCodingVersion(source,contract,observed),c.expected,source.profileId+' '+c.label);
  if(c.expected)positive++;
}
}
verifyCases(fixture.source,fixture.cases);
assert.ok(positive>0&&positive<fixture.cases.length);
assert.equal(fixture.registeredSelectorProfiles,115);assert.equal(fixture.gatewaySelectorProfiles,16);
assert.equal(fixture.regressions.length,131);
let regressions=0;
for(let i=0;i<fixture.regressions.length;i++){
  const r=fixture.regressions[i];
  if(i<fixture.registeredSelectorProfiles){
    const registered=UDS_CODING_SOURCES[r.source.profileId];assert.ok(registered);
    assert.deepEqual(r.source,Object.fromEntries(['profileId','versionSelectors','identityConstraints'].map(k=>[k,registered[k]])));
  }
  verifyCases(r.source,r.cases);regressions+=r.cases.length;
}
const result={passed:true,checks:fixture.cases.length,regressionProfiles:131,regressionChecks:regressions,positiveMatches:positive,fixtureSha256:sha(await fs.readFile(fixturePath)),
  codeSha256:sha(await fs.readFile(code)),completeScopeRegistered:false,deviceIO:false,vehicleIO:false};
await fs.writeFile(path.join(task,process.env.PORSCHE981_ACCEPT_APP_ROOT?'installed-result.json':'source-result.json'),JSON.stringify(result,null,2)+'\n');
console.log(JSON.stringify(result));
