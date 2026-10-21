/** Actual source worker outputs and private controller; byte/native I/O modeled. */
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import path from 'node:path';
import {pathToFileURL} from 'node:url';
import {EventEmitter} from 'node:events';
import {PassThrough} from 'node:stream';
import {createHash} from 'node:crypto';
const host=path.resolve(import.meta.dirname,'..'),root=path.resolve(process.env.PORSCHE981_ACCEPT_APP_ROOT||host);
const moduleAt=name=>import(pathToFileURL(path.join(root,'apps/desktop/electron',name)));
const {UDS_CODING_SOURCES}=await moduleAt('uds-coding-sources.mjs');
const hardwareRequest=doc=>UDS_CODING_SOURCES[doc.codingRead.profileId].hardwareIdentity?.requestHex||'22F1A3';
const {validateCompleteCodingRead}=await moduleAt('coding-read-evidence.mjs');
const {createCodingWorkspace}=await moduleAt('coding-workspace.mjs');
const {createManufacturerStream}=await moduleAt('manufacturer-stream.mjs');
const {createTransportGate}=await moduleAt('obd-connection.mjs');
const {normalizeCodingBackup}=await moduleAt('diagnostic-preparation.mjs');
const {normalizeSourceCodingBackup}=await moduleAt('source-coding-backup.mjs');
const {acceptCodingSnapshot}=await import(pathToFileURL(path.join(host,'apps/desktop/src/obd/coding-workspace-state.mjs')));
const task=path.join(host,'.local/obd-rdk-coding-source-20261018');
const dir=path.join(task,process.env.PORSCHE981_ACCEPT_APP_ROOT?'installed-models':'source-models');
const files=(await fs.readdir(path.join(dir,'models'))).filter(n=>/^rdk-\d+-\d+-(8|54|55)-(elm|vnci)\.json$/.test(n)).sort();
assert.equal(files.length,64);
const load=async n=>JSON.parse(await fs.readFile(path.join(dir,'models',n),'utf8'));
const observed=m=>{const map=new Map();for(const p of m.pdus){if(p.duringCleanup)continue;const list=map.get(p.requestHex)||[];list.push(p.pduHex);map.set(p.requestHex,list);}return map;};
const sha=b=>createHash('sha256').update(b).digest('hex');
const ecuName=id=>({8:'cluster',54:'steering-column',55:'rdk'}[id]);
const groupName=id=>({"54": "转向盘电子设备", "55": "轮胎压力监控 （TPM）", "8": "IC(仪表)"}[id]);
let checks=0;
for(const file of files){
 const m=await load(file);assert.equal(m.syntheticPort,true);const doc=m.result,seen=observed(m),ecu=ecuName(doc.codingRead.ecuId);
 const identity=validateCompleteCodingRead(doc,seen);assert.equal(identity.generation,doc.vehicleGeneration);checks++;
 const value={schemaVersion:3,kind:'ecu-coding-backup',identity:{...identity,ecu},profileId:doc.codingRead.profileId,
  sourceHash:doc.codingRead.definitionSha256,scopeHash:doc.codingRead.completeScopeSource.sha256,blocks:doc.codingRead.blocks,
  capturedUtc:doc.codingRead.blocks.at(-1).receivedUtc,provenance:'source-vehicle-read',freshReadProven:true,
  completeness:'source-qualified-complete-record-set',liveVerified:false};
 assert.equal(normalizeSourceCodingBackup(value).blocks.length,doc.codingRead.blocks.length);checks++;
 assert.throws(()=>normalizeSourceCodingBackup({...value,identity:{...value.identity,ecu:'bcm-front'}}));checks++;
 for(const mutate of [d=>d.codingRead.blocks.pop(),d=>d.codingRead.blocks[0].dataHex+='00',d=>d.codingRead.completeScopeSource.requestLengths[0].bytes++,
  d=>d.codingRead.completeOriginalRead=false,d=>d.codingRead.definitionSha256='0'.repeat(64),d=>d.codingRead.codingWriteQualified=true,
  d=>d.codingRead.ecuId=32,d=>d.readContract.txId=0x710,d=>d.readContract.rxId=0x77A,
  d=>d.readContract.identityConstraints={},d=>d.vehicleIdentitySha256='0'.repeat(64),d=>d.identityInfo.pop(),
  d=>d.identityInfo.find(r=>r.requestHex===hardwareRequest(d)).value='other',d=>d.identityInfo.find(r=>r.requestHex==='22F189').value='other',
  d=>d.codingRead.fields[0].editable=true,d=>d.readContract.sourceCommands=[],d=>d.readContract.identityRequests[0].record.at++,d=>d.readContract.session={},d=>d.readContract.versionSelectors[0].selector.formulaId=0]){
  const bad=structuredClone(doc);mutate(bad);assert.throws(()=>validateCompleteCodingRead(bad,seen),undefined,file);checks++;
 }
 const missing=structuredClone(seen);missing.delete(hardwareRequest(doc));assert.throws(()=>validateCompleteCodingRead(doc,missing));checks++;
 const changed=structuredClone(seen);changed.get('22F1A2')[0]='62F1A2585858585858';assert.throws(()=>validateCompleteCodingRead(doc,changed));checks++;
 const source=UDS_CODING_SOURCES[doc.codingRead.profileId];
 if(source.hardwareIdentity){assert.ok(identity.hardware.startsWith('PN:'));assert.throws(()=>normalizeSourceCodingBackup({...value,identity:{...value.identity,hardware:'H01'}}));checks+=2;}
 for(const extra of source.factoryOnlyReadRequests||[]){
  const absent=structuredClone(value);absent.blocks=absent.blocks.filter(b=>b.requestHex!==extra.requestHex);assert.throws(()=>normalizeSourceCodingBackup(absent));
  const altered=structuredClone(doc);altered.codingRead.blocks.find(b=>b.requestHex===extra.requestHex).dataHex+='00';assert.throws(()=>validateCompleteCodingRead(altered,seen));checks+=2;
 }
 const badVin=structuredClone(seen),badDoc=structuredClone(doc);badVin.get('22F190')[0]='62F190D7'+badVin.get('22F190')[0].slice(8);
 badDoc.identityInfo.find(r=>r.requestHex==='22F190').pduSha256=sha(Buffer.from(badVin.get('22F190')[0],'hex'));assert.throws(()=>validateCompleteCodingRead(badDoc,badVin));checks++;
}
for(const id of [8,54,55]){
 const model=await load(files.find(n=>n.includes(`-981-${id}-elm`))),identity=validateCompleteCodingRead(model.result,observed(model)),ecu=ecuName(id);
 const temp=await fs.mkdtemp(path.join(task,'rdk-store-')),desktop=path.join(temp,'Desktop');await fs.mkdir(desktop);
 let failDesktop=true,unavailable=null;
 const workspace=createCodingWorkspace({directory:path.join(temp,'store'),desktopDirectory:()=>failDesktop?desktop+'/missing':desktop,
  readClient:async()=>unavailable?{ok:false,error:unavailable}:{ok:true,releaseVerified:true,result:structuredClone(model.result),identity}});
 assert.deepEqual(await workspace.handle({action:'capability',ecu},1),{ok:true,readReady:true,backupReady:true});
 assert.equal((await workspace.handle({action:'read',ecu,dataHex:'00'},1)).error,'coding-workspace-invalid');
 assert.equal((await workspace.handle({action:'read',ecu},1)).error,'coding-backup-failed');failDesktop=false;
 const backup=await workspace.handle({action:'backup',ecu},1);assert.equal(backup.saved,true);assert.equal(backup.snapshot,undefined);
 const raw=await fs.readFile(backup.filePath),internal=await fs.readdir(path.join(temp,'store'));
 const current=await workspace.handle({action:'read',ecu},1);assert.equal(current.ok,true);const snapshot=acceptCodingSnapshot(current.snapshot,ecu);
 assert.equal(snapshot.fields.length,model.result.codingRead.definitionFieldCount);assert.equal(snapshot.canWrite,false);assert.equal(snapshot.canRestore,false);
 assert.ok(snapshot.fields.every(f=>f.group===groupName(id)&&!f.editable));
 assert.deepEqual(await fs.readFile(backup.filePath),raw);assert.deepEqual(await fs.readdir(path.join(temp,'store')),internal);assert.equal((await fs.readdir(desktop)).length,1);
 const stored=JSON.parse(raw);assert.equal(stored.provenance,'source-vehicle-read');assert.throws(()=>normalizeCodingBackup(stored));
 assert.throws(()=>normalizeSourceCodingBackup({...stored,profileId:'toString',sourceHash:undefined,scopeHash:undefined}));
 for(const error of ['coding-complete-scope-unqualified','manufacturer-coding-definition-incomplete','manufacturer-coding-source-unqualified']){
  unavailable=error;assert.equal((await workspace.handle({action:'read',ecu},1)).error,'coding-client-unavailable');assert.deepEqual(await fs.readdir(path.join(temp,'store')),internal);
 }
 assert.equal((await workspace.handle({action:'write',ecu,reviewId:'invented'},1)).error,'coding-client-unavailable');checks+=20;
 for(const native of [false,true]){
  const fixture=await load(files.find(n=>n.includes(`-981-${id}-`)&&n.endsWith(native?'-vnci.json':'-elm.json')));
  const vehicle=JSON.parse(await fs.readFile(path.join(host,'.local/obd-ecm-uds-reader-20261016/vehicle-source-981.json')));
  const gate=createTransportGate();let calls=0,resumes=0,unsupportedNext=false;
  const manager=createManufacturerStream({repoRoot:root,env:{},allowInjectedLive:true,gate,
   connection:{snapshot:()=>({purpose:'diagnostic',model:native?'VNCI':'ELM327',serialBaud:null}),selectedId:()=>native?'vnci:10001':'bt:001122334455',reserveDispatch:()=>true,releaseDispatch(){},suspendForSession:async()=>({ok:true}),resumeAfterSession:async proof=>{assert.equal(proof.nativeReleaseVerified,true);resumes++;return{ok:true};}},
   spawnFn(){const child=new EventEmitter();child.stdin=new PassThrough();child.stdout=new PassThrough();child.stderr=new PassThrough();child.kill=()=>{};
    child.stdin.once('data',raw=>{const request=JSON.parse(raw),coding=++calls%2===0;assert.equal(request.action,coding?'manufacturer-read-coding':'manufacturer-identify-vehicle');
     if(coding){assert.equal(request.ecuId,id);assert.equal(request.sourceBound,undefined);assert.equal(request.completeOriginalRequired,true);assert.equal(request.expectedVehicleIdentitySha256,fixture.result.vehicleIdentitySha256);}
     setImmediate(()=>{child.emit('spawn');let frames,code=0;
      if(!coding)frames=vehicle.frames;else{const result=structuredClone(fixture.result);delete result.transport.rawLog;
       if(unsupportedNext){result.ok=false;result.error='coding-complete-scope-unqualified';result.codingRead=null;code=1;}
       frames=(unsupportedNext?[]:fixture.pdus).map(pdu=>({event:'diagnostic-pdu',result:{executionEnabled:true,liveVerified:false,writePayload:null,simulation:false,sourceKind:'independent-manufacturer-read',pdu}})).concat({event:'finished',result});}
      for(const frame of frames)child.stdout.write(JSON.stringify(frame)+'\n');setImmediate(()=>child.emit('close',code));});});return child;}});
  assert.equal((await manager.handle({action:'read-coding',ecuId:id},1)).ok,false);
  const result=await manager.readCoding(id,1);assert.equal(result.ok,true,JSON.stringify(result));assert.equal(result.identity.vin,identity.vin);assert.equal(gate.owner(),null);assert.equal(manager.isIdle(),true);
  unsupportedNext=true;assert.equal((await manager.readCoding(id,1)).error,'coding-complete-scope-unqualified');assert.equal(resumes,4);assert.equal(manager.isIdle(),true);assert.equal(gate.owner(),null);await manager.shutdown();checks+=12;
 }
}
assert.equal(checks,1828);
const code=await Promise.all(['uds-coding-sources.mjs','coding-read-evidence.mjs','source-coding-backup.mjs','coding-workspace.mjs','manufacturer-stream.mjs'].map(async name=>({name,sha256:sha(await fs.readFile(path.join(root,'apps/desktop/electron',name)))})));
await fs.writeFile(path.join(path.resolve(process.env.PORSCHE981_CODING_CHECK_OUTPUT||task),process.env.PORSCHE981_ACCEPT_APP_ROOT?'installed-desktop-result.json':'desktop-result.json'),JSON.stringify({passed:true,models:files.length,checks,code,deviceIO:false,vehicleIO:false},null,2)+'\n');
console.log('PASS64 rdk/stalk/instrument source models /1828 interface checks; modeled transport only');
