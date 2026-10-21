/** Production gateway + independent source-link reads, with modeled I/O. */
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import path from 'node:path';
import {pathToFileURL} from 'node:url';
import {createHash} from 'node:crypto';
const host=path.resolve(import.meta.dirname,'..'),root=path.resolve(process.env.PORSCHE981_ACCEPT_APP_ROOT||host);
const side=root===host?'source':'installed',task=path.join(host,'.local/obd-soundactuator-coding-20261019');
const at=n=>import(pathToFileURL(path.join(root,'apps/desktop/electron',n)));
const {validateCompleteCodingRead,validateCodingVehicleAnchor}=await at('coding-read-evidence.mjs');
const {normalizeSourceCodingBackup}=await at('source-coding-backup.mjs');
const {createCodingWorkspace}=await at('coding-workspace.mjs');
const {udsCodingSourceForUnit}=await at('uds-coding-sources.mjs');
const fixtureSide=process.env.PORSCHE981_CODING_FIXTURE_SIDE||side;
const dir=path.join(task,fixtureSide+'-models/models');
const files=(await fs.readdir(dir)).filter(n=>/^sound-\d+-(981|982)-[01]-(elm|vnci)\.json$/.test(n)).sort();
assert.equal(files.length,16);
const sha=b=>createHash('sha256').update(b).digest('hex');
if(side==='installed'&&fixtureSide==='source'){
  const p=JSON.parse(await fs.readFile(path.join(task,'source-models/result.json'),'utf8'));
  assert.equal(p.passed,true);
  for(const[n,h]of Object.entries(p.sourceCode))assert.equal(sha(await fs.readFile(path.join(root,n))),h,n);
}
function proof(m){
  let observed=new Map(),anchor;
  for(const f of m.frames){
    if(f.event==='diagnostic-pdu'&&!f.result.pdu.duringCleanup){const p=f.result.pdu;observed.set(p.requestHex,[...(observed.get(p.requestHex)||[]),p.pduHex]);}
    else if(f.event==='coding-vehicle-anchor'){anchor=validateCodingVehicleAnchor(f.result,observed,m.result.vehicleGeneration,m.result.vehicleIdentitySha256);observed=new Map();}
  }
  return {observed,anchor};
}
let checks=0;const samples=new Map();
for(const name of files){
  const m=JSON.parse(await fs.readFile(path.join(dir,name),'utf8')),d=m.result,c=d.codingRead;
  assert.equal(m.syntheticPort,true);
  const source=udsCodingSourceForUnit(c.profileId,c.ecuId),{observed,anchor}=proof(m);
  const identity=validateCompleteCodingRead(d,observed,anchor);
  assert.equal(identity.software,'ASAM/ODX:'+source.identityConstraints.versionRules[0]);
  assert.match(identity.hardware,/^PN:/);checks+=2;
  const backup={schemaVersion:3,kind:'ecu-coding-backup',identity:{...identity,ecu:source.workspaceEcu},profileId:c.profileId,sourceHash:c.definitionSha256,scopeHash:c.completeScopeSource.sha256,blocks:c.blocks,capturedUtc:c.blocks.at(-1).receivedUtc,provenance:'source-vehicle-read',freshReadProven:true,completeness:'source-qualified-complete-record-set',liveVerified:false};
  assert.deepEqual(normalizeSourceCodingBackup(backup).identity,backup.identity);checks++;
  for(const bad of [{...backup,identity:{...backup.identity,software:'1234'}},{...backup,identity:{...backup.identity,ecu:'dme'}},{...backup,blocks:backup.blocks.slice(1)}]){assert.throws(()=>normalizeSourceCodingBackup(bad));checks++;}
  assert.throws(()=>validateCompleteCodingRead(d,observed));
  assert.throws(()=>validateCompleteCodingRead(d,observed,JSON.parse(JSON.stringify(anchor))));checks+=2;
  for(const mutate of [v=>delete v.sourceRouteDiscovery,v=>v.sourceRouteDiscovery.selectedSourceLink=1-v.sourceRouteDiscovery.selectedSourceLink,v=>v.sourceRouteDiscovery.selectedSourceLink=String(v.sourceRouteDiscovery.selectedSourceLink),v=>v.sourceRouteDiscovery.uniqueMatch=false,v=>v.sourceRouteDiscovery.probes.reverse(),v=>v.sourceRouteDiscovery.probes[0].ok=!v.sourceRouteDiscovery.probes[0].ok,v=>v.sourceRouteDiscovery.probes[0].error='driver-failed',v=>v.readContract.txId++,v=>v.readContract.rxId++,v=>v.identityInfo.pop(),v=>v.readContract.identityRequests[3].records.pop(),v=>v.readContract.versionSelectors[0].selector.formulaId=0,v=>v.codingRead.blocks.pop(),v=>v.codingRead.blocks[0].dataHex+='00',v=>v.codingRead.completeOriginalRead=false,v=>v.codingRead.codingWriteQualified=true,v=>v.codingRead.codingRestoreQualified=true,v=>v.codingRead.fields[0].editable=true]){
    const bad=structuredClone(d);mutate(bad);assert.throws(()=>validateCompleteCodingRead(bad,observed,anchor));checks++;
  }
  for(const field of [...source.identityRequests,...source.versionSelectors.map(r=>r.requestHex)]){
    const bad=structuredClone(observed);bad.set(field,bad.get(field).slice(1));
    assert.throws(()=>validateCompleteCodingRead(d,bad,anchor));checks++;
    const changed=structuredClone(observed);changed.get(field)[1]+='00';
    assert.throws(()=>validateCompleteCodingRead(d,changed,anchor));checks++;
  }
  const ghost=structuredClone(observed);ghost.set('22F190',['62F190'+Buffer.from(identity.vin).toString('hex').toUpperCase()]);assert.throws(()=>validateCompleteCodingRead(d,ghost,anchor));checks++;
  if(!samples.has(c.profileId)&&c.generation==='981')samples.set(c.profileId,{m,identity,source});
}
assert.equal(samples.size,2);
for(const {m,identity,source} of samples.values()){
  const temp=await fs.mkdtemp(path.join(task,'sound-store-')),desktop=path.join(temp,'Desktop');await fs.mkdir(desktop);let failDesktop=true;
  try{
    const ctl=createCodingWorkspace({directory:path.join(temp,'store'),desktopDirectory:()=>failDesktop?desktop+'/missing':desktop,readClient:async()=>({ok:true,result:structuredClone(m.result),identity,releaseVerified:true})});
    const ecu=source.workspaceEcu;assert.deepEqual(await ctl.handle({action:'capability',ecu},1),{ok:true,readReady:true,backupReady:true});
    assert.equal((await ctl.handle({action:'read',ecu},1)).error,'coding-backup-failed');failDesktop=false;
    const saved=await ctl.handle({action:'backup',ecu},1);assert.equal(saved.saved,true);assert.equal(saved.snapshot,undefined);
    const bytes=await fs.readFile(saved.filePath),read=await ctl.handle({action:'read',ecu},1);
    assert.equal(read.ok,true);assert.equal(read.snapshot.identity.ecu,ecu);assert.equal(read.snapshot.canWrite,false);assert.equal(read.snapshot.canRestore,false);
    assert.equal(read.snapshot.fields.length,m.result.codingRead.definitionFieldCount);assert.deepEqual(await fs.readFile(saved.filePath),bytes);assert.equal((await fs.readdir(desktop)).length,1);checks+=11;
  }finally{await fs.rm(temp,{recursive:true,force:true});}
}
const code=await Promise.all(['uds-coding-sources.mjs','coding-read-evidence.mjs','source-coding-backup.mjs','coding-workspace.mjs','manufacturer-stream.mjs'].map(async name=>({name,sha256:sha(await fs.readFile(path.join(root,'apps/desktop/electron',name)))})));
await fs.writeFile(path.join(task,(side==='installed'?'installed-':'')+'desktop-result.json'),JSON.stringify({passed:true,models:files.length,checks,code,fixtureSide,sourceFixtureReuse:side==='installed'&&fixtureSide==='source',deviceIO:false,vehicleIO:false},null,2)+'\n');
console.log('PASS',files.length,'sound source-route models /',checks,'interface checks; modeled I/O only');
