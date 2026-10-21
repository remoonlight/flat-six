/** Complete radar originals and separate side backups; modeled I/O only. */
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import path from 'node:path';
import {pathToFileURL} from 'node:url';
import {createHash} from 'node:crypto';
const host=path.resolve(import.meta.dirname,'..'),root=path.resolve(process.env.PORSCHE981_ACCEPT_APP_ROOT||host),side=root===host?'source':'installed';
const at=n=>import(pathToFileURL(path.join(root,'apps/desktop/electron',n)));
const {validateCompleteCodingRead,validateCodingVehicleAnchor}=await at('coding-read-evidence.mjs');
const {normalizeSourceCodingBackup}=await at('source-coding-backup.mjs');
const {udsCodingSourceForUnit}=await at('uds-coding-sources.mjs');
const {createCodingWorkspace}=await at('coding-workspace.mjs');
const task=path.join(host,'.local/obd-lane-coding-20261019'),fixtureSide=process.env.PORSCHE981_CODING_FIXTURE_SIDE||side,dir=path.join(task,fixtureSide+'-models/models');
const files=(await fs.readdir(dir)).filter(n=>/^lane-\d+-(981|982)-(95|96)-(elm|vnci)\.json$/.test(n)).sort();assert.equal(files.length,16);
const sha=b=>createHash('sha256').update(b).digest('hex');
if(side==='installed'&&fixtureSide==='source'){
  const p=JSON.parse(await fs.readFile(path.join(task,'source-models/result.json'),'utf8'));assert.equal(p.passed,true);
  for(const[n,h]of Object.entries(p.sourceCode))assert.equal(sha(await fs.readFile(path.join(root,n))),h,n);
}
const other=id=>id===95?96:95,workspace=id=>id===95?'swa-right':'swa-left';
function proof(m){
  let observed=new Map(),anchor;
  for(const f of m.frames){
    if(f.event==='diagnostic-pdu'&&!f.result.pdu.duringCleanup){const p=f.result.pdu;observed.set(p.requestHex,[...(observed.get(p.requestHex)||[]),p.pduHex]);}
    else if(f.event==='coding-vehicle-anchor'){anchor=validateCodingVehicleAnchor(f.result,observed,m.result.vehicleGeneration,m.result.vehicleIdentitySha256);observed=new Map();}
  }
  return{observed,anchor};
}
let checks=0;const samples=new Map();
for(const name of files){
  const m=JSON.parse(await fs.readFile(path.join(dir,name),'utf8'));assert.equal(m.syntheticPort,true);const d=m.result,c=d.codingRead,source=udsCodingSourceForUnit(c.profileId,c.ecuId),{observed,anchor}=proof(m);
  const identity=validateCompleteCodingRead(d,observed,anchor);assert.match(identity.hardware,/^PN:/);assert.equal(identity.software,'S001');checks+=2;
  const backup={schemaVersion:3,kind:'ecu-coding-backup',identity:{...identity,ecu:workspace(c.ecuId)},targetEcuId:c.ecuId,profileId:c.profileId,sourceHash:c.definitionSha256,scopeHash:c.completeScopeSource.sha256,blocks:c.blocks,capturedUtc:c.blocks.at(-1).receivedUtc,provenance:'source-vehicle-read',freshReadProven:true,completeness:'source-qualified-complete-record-set',liveVerified:false};
  assert.equal(normalizeSourceCodingBackup(backup).targetEcuId,c.ecuId);checks++;
  for(const bad of [{...backup,targetEcuId:other(c.ecuId)},{...backup,targetEcuId:undefined},{...backup,targetEcuId:String(c.ecuId)},{...backup,identity:{...backup.identity,ecu:workspace(other(c.ecuId))}},{...backup,blocks:backup.blocks.slice(1)}]){assert.throws(()=>normalizeSourceCodingBackup(bad));checks++;}
  assert.throws(()=>validateCompleteCodingRead(d,observed));assert.throws(()=>validateCompleteCodingRead(d,observed,JSON.parse(JSON.stringify(anchor))));checks+=2;
  for(const mutate of [v=>v.readContract.rxId++,v=>v.readContract.txId++,v=>v.readContract.ecuId=other(c.ecuId),v=>v.codingRead.ecuId=other(c.ecuId),v=>v.vehicleAnchor.closed=false,v=>v.identityInfo.pop(),v=>v.readContract.identityRequests[0].record.at++,v=>v.readContract.versionSelectors[0].selector.formulaId=0,v=>v.codingRead.blocks.pop(),v=>v.codingRead.blocks[0].dataHex+='00',v=>v.codingRead.completeOriginalRead=false,v=>v.codingRead.codingWriteQualified=true,v=>v.codingRead.codingRestoreQualified=true,v=>v.codingRead.fields[0].editable=true]){
    const bad=structuredClone(d);mutate(bad);assert.throws(()=>validateCompleteCodingRead(bad,observed,anchor));checks++;
  }
  const ghost=structuredClone(observed);ghost.set('22F190',['62F190'+Buffer.from(identity.vin).toString('hex').toUpperCase()]);assert.throws(()=>validateCompleteCodingRead(d,ghost,anchor));checks++;
  for(const h of [...source.identityRequests,...source.versionSelectors.map(f=>f.requestHex)]){
    const repeat=structuredClone(observed);repeat.set(h,[...repeat.get(h),repeat.get(h)[0]]);assert.throws(()=>validateCompleteCodingRead(d,repeat,anchor));checks++;
  }
  const swapped=structuredClone(d);swapped.codingRead.ecuId=swapped.readContract.ecuId=swapped.transport.qualification.ecuId=other(c.ecuId);assert.throws(()=>validateCompleteCodingRead(swapped,observed,anchor));checks++;
  if(!samples.has(c.ecuId)&&c.generation==='981')samples.set(c.ecuId,{m,identity});
}
assert.equal(samples.size,2);
const temp=await fs.mkdtemp(path.join(task,'lane-store-')),desktop=path.join(temp,'Desktop');await fs.mkdir(desktop);let failDesktop=true;
try{
  const ctl=createCodingWorkspace({directory:path.join(temp,'store'),desktopDirectory:()=>failDesktop?desktop+'/missing':desktop,readClient:async id=>{const row=samples.get(id);return{ok:true,result:structuredClone(row.m.result),identity:row.identity,releaseVerified:true};}});
  for(const[id,row]of samples){
    const ecu=workspace(id);assert.deepEqual(await ctl.handle({action:'capability',ecu},1),{ok:true,readReady:true,backupReady:true});failDesktop=true;
    assert.equal((await ctl.handle({action:'read',ecu},1)).error,'coding-backup-failed');failDesktop=false;
    const saved=await ctl.handle({action:'backup',ecu},1);assert.equal(saved.saved,true);assert.equal(saved.snapshot,undefined);
    const bytes=await fs.readFile(saved.filePath);assert.equal(JSON.parse(bytes).targetEcuId,id);
    const read=await ctl.handle({action:'read',ecu},1);assert.equal(read.ok,true);assert.equal(read.snapshot.identity.ecu,ecu);assert.equal(read.snapshot.canWrite,false);assert.equal(read.snapshot.canRestore,false);assert.equal(read.snapshot.fields.length,row.m.result.codingRead.definitionFieldCount);assert.deepEqual(await fs.readFile(saved.filePath),bytes);checks+=11;
  }
  assert.equal((await fs.readdir(desktop)).length,2);checks++;
}finally{await fs.rm(temp,{recursive:true,force:true});}
const code=await Promise.all(['uds-coding-sources.mjs','coding-read-evidence.mjs','source-coding-backup.mjs','coding-workspace.mjs','manufacturer-stream.mjs'].map(async name=>({name,sha256:sha(await fs.readFile(path.join(root,'apps/desktop/electron',name)))})));
await fs.writeFile(path.join(task,(side==='installed'?'installed-':'')+'desktop-result.json'),JSON.stringify({passed:true,models:files.length,checks,code,fixtureSide,sourceFixtureReuse:side==='installed'&&fixtureSide==='source',deviceIO:false,vehicleIO:false},null,2)+'\n');console.log('PASS',files.length,'lane models /',checks,'interface checks; modeled I/O only');
