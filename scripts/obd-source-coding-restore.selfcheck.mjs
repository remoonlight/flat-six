/** Real reader DTOs over modeled peers; compare originals, never execute a write. */
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import path from 'node:path';
import {pathToFileURL} from 'node:url';
import {createHash} from 'node:crypto';
const host=path.resolve(import.meta.dirname,'..'),root=path.resolve(process.env.PORSCHE981_ACCEPT_APP_ROOT||host);
const at=n=>import(pathToFileURL(path.join(root,'apps/desktop/electron',n)));
const {codingRestorePlan,normalizeCodingBackup}=await at('diagnostic-preparation.mjs');
const {normalizeSourceCodingBackup}=await at('source-coding-backup.mjs');
const {validateCodingVehicleAnchor,validateCompleteCodingRead}=await at('coding-read-evidence.mjs');
const {udsCodingSourceForUnit}=await at('uds-coding-sources.mjs');
const task=path.join(host,'.local/obd-coding-write-bindings-20261019');
const folders=['obd-coding-desktop-20261018','obd-gateway-coding-source-20261018','obd-dme-coding-source-20261018','obd-pdk-coding-source-20261018','obd-body-coding-source-20261018','obd-chassis-coding-source-20261018','obd-cabin-coding-source-20261018','obd-rdk-coding-source-20261018','obd-media-coding-source-20261018','obd-media-kwp-coding-source-20261018','obd-headunit-coding-source-20261018','obd-assist-coding-source-20261018','obd-anchored-coding-source-20261018','obd-remaining-coding-source-20261018','obd-engine-grouped-coding-20261018','obd-seat-door-coding-20261019','obd-soundactuator-coding-20261019','obd-lane-coding-20261019'];
const sha=b=>createHash('sha256').update(b).digest('hex');
const seen=new Set(),profiles=new Set(),units=new Set(),inputs=[];let checks=0;
function proof(m){
  let observed=new Map(),anchor;
  const add=p=>{if(!p.duringCleanup)observed.set(p.requestHex,[...(observed.get(p.requestHex)||[]),p.pduHex]);};
  if(m.frames){for(const f of m.frames){if(f.event==='diagnostic-pdu')add(f.result.pdu);else if(f.event==='coding-vehicle-anchor'){anchor=validateCodingVehicleAnchor(f.result,observed,m.result.vehicleGeneration,m.result.vehicleIdentitySha256);observed=new Map();}}}
  else for(const p of m.pdus)add(p);
  return validateCompleteCodingRead(m.result,observed,anchor);
}
for(const folder of folders){
  const directory=path.join(host,'.local',folder,folder.includes('gateway-coding')?'source-gateway-models/models':'source-models/models');
  const files=(await fs.readdir(directory)).filter(n=>n.endsWith('.json')).sort();
  for(const name of files){
    const file=path.join(directory,name),raw=await fs.readFile(file),m=JSON.parse(raw),c=m.result?.codingRead;
    if(!c?.completeOriginalRead)continue;
    const key=[c.profileId,c.ecuId,c.generation].join(':');if(seen.has(key))continue;seen.add(key);
    assert.notEqual(m.deviceIO,true);assert.notEqual(m.vehicleIO,true);
    const id=proof(m),source=udsCodingSourceForUnit(c.profileId,c.ecuId),ecu=source?.workspaceEcu||({1:'dme',2:'pdk',9:'gateway',47:'headlamp-left',48:'headlamp-right'}[c.ecuId]);assert.ok(ecu);
    const baseline=normalizeSourceCodingBackup({schemaVersion:3,kind:'ecu-coding-backup',identity:{...id,ecu},targetEcuId:c.ecuId,profileId:c.profileId,sourceHash:c.definitionSha256,scopeHash:c.completeScopeSource.sha256,blocks:c.blocks,capturedUtc:c.blocks.at(-1).receivedUtc,provenance:'source-vehicle-read',freshReadProven:true,completeness:'source-qualified-complete-record-set',liveVerified:false});
    assert.equal(codingRestorePlan(baseline,baseline).changedBlocks,0);checks++;
    const current=structuredClone(baseline),b=current.blocks[0],bytes=Buffer.from(b.dataHex,'hex');bytes[0]^=1;b.dataHex=bytes.toString('hex').toUpperCase();b.pduHex=(b.identifierKind==='LID'?'61':'62')+b.identifierHex+b.dataHex;b.sha256=sha(bytes);current.capturedUtc=new Date(Date.parse(baseline.capturedUtc)+1000).toISOString();
    const untouched=structuredClone([baseline,current]),plan=codingRestorePlan(baseline,current);
    assert.equal(plan.changedBlocks,1);assert.equal(plan.blocks.length,baseline.blocks.length);assert.equal(plan.blocks[0].beforeHex,b.dataHex);assert.equal(plan.blocks[0].targetHex,baseline.blocks[0].dataHex);assert.equal(plan.executionEnabled,false);assert.equal(plan.writePayload,null);assert.deepEqual([baseline,current],untouched);checks+=7;
    for(let i=1;i<plan.blocks.length;i++){assert.equal(plan.blocks[i].changed,false);assert.equal(plan.blocks[i].beforeHex,baseline.blocks[i].dataHex);assert.equal(plan.blocks[i].targetHex,baseline.blocks[i].dataHex);}checks++;
    const badVin=structuredClone(current);badVin.identity.vin=id.vin.endsWith('654321')?'WP0ZZZ98ZES123456':'WP0ZZZ98ZES654321';assert.throws(()=>codingRestorePlan(baseline,badVin),/identity-version-mismatch/);checks++;
    for(const mutate of [v=>v.identity.hardware+='other',v=>v.sourceHash='0'.repeat(64),v=>v.scopeHash='0'.repeat(64),v=>v.blocks.pop(),v=>v.blocks[0].sha256='0'.repeat(64),v=>v.blocks[0].pduHex+='00']){const bad=structuredClone(current);mutate(bad);assert.throws(()=>codingRestorePlan(baseline,bad));checks++;}
    const imported=normalizeCodingBackup({schemaVersion:2,kind:'ecu-coding-backup',identity:baseline.identity,profileId:baseline.profileId,expectedBlocks:baseline.expectedBlocks,blocks:baseline.blocks,capturedUtc:baseline.capturedUtc});
    assert.throws(()=>codingRestorePlan(imported,current),/provenance-mismatch/);assert.throws(()=>codingRestorePlan(baseline,imported),/provenance-mismatch/);checks+=2;
    inputs.push({path:path.relative(host,file),sha256:sha(raw)});profiles.add(c.profileId);units.add(c.ecuId);
  }
}
assert.equal(profiles.size,201);assert.equal(units.size,33);
const code=await Promise.all(['diagnostic-preparation.mjs','source-coding-backup.mjs','coding-read-evidence.mjs','uds-coding-sources.mjs'].map(async name=>({name,sha256:sha(await fs.readFile(path.join(root,'apps/desktop/electron',name)))})));
const side=root===host?'source':'installed';await fs.writeFile(path.join(task,side+'-restore-result.json'),JSON.stringify({passed:true,profiles:profiles.size,targetUnits:units.size,contexts:seen.size,checks,code,inputs,modeledReaderEvidenceReused:true,restoreExecutionQualified:false,deviceIO:false,vehicleIO:false},null,2)+'\n');console.log('PASS source restore comparison',profiles.size,'versions /',units.size,'units /',seen.size,'contexts /',checks,'checks; no write execution');
