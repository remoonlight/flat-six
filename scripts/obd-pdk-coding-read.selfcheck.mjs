/** Source worker outputs, owned manager and immutable storage; synthetic I/O. */
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import path from 'node:path';
import {pathToFileURL} from 'node:url';
import {EventEmitter} from 'node:events';
import {PassThrough} from 'node:stream';
import {createHash} from 'node:crypto';
const host=path.resolve(import.meta.dirname,'..'),root=path.resolve(process.env.PORSCHE981_ACCEPT_APP_ROOT||host);
const loadModule=name=>import(pathToFileURL(path.join(root,name)));
const {validateCompleteCodingRead}=await loadModule('apps/desktop/electron/coding-read-evidence.mjs');
const {createCodingWorkspace}=await loadModule('apps/desktop/electron/coding-workspace.mjs');
const {createManufacturerStream}=await loadModule('apps/desktop/electron/manufacturer-stream.mjs');
const {createTransportGate}=await loadModule('apps/desktop/electron/obd-connection.mjs');
const {normalizeCodingBackup}=await loadModule('apps/desktop/electron/diagnostic-preparation.mjs');
const {normalizeSourceCodingBackup}=await loadModule('apps/desktop/electron/source-coding-backup.mjs');
const {acceptCodingSnapshot}=await import(pathToFileURL(path.join(host,'apps/desktop/src/obd/coding-workspace-state.mjs')));
const task=path.resolve('.local/obd-pdk-coding-source-20261018');
const dir=path.join(task,process.env.PORSCHE981_ACCEPT_APP_ROOT?'installed-models':'source-models');
const files=(await fs.readdir(path.join(dir,'models'))).filter(n=>/^pdk-\d+-\d+-(elm|vnci)\.json$/.test(n)).sort();
assert.ok(files.length);
const load=async n=>JSON.parse(await fs.readFile(path.join(dir,'models',n),'utf8'));
const observed=m=>{const map=new Map();for(const p of m.pdus){if(p.duringCleanup)continue;const rows=map.get(p.requestHex)||[];rows.push(p.pduHex);map.set(p.requestHex,rows);}return map;};
let checks=0;
for(const name of files){
 const m=await load(name);assert.equal(m.syntheticPort,true);const doc=m.result,seen=observed(m);
 const identity=validateCompleteCodingRead(doc,seen);assert.equal(identity.generation,doc.vehicleGeneration);checks++;
 const normalized=normalizeSourceCodingBackup({schemaVersion:3,kind:'ecu-coding-backup',identity:{...identity,ecu:'pdk'},
  profileId:doc.codingRead.profileId,sourceHash:doc.codingRead.definitionSha256,scopeHash:doc.codingRead.completeScopeSource.sha256,
  blocks:doc.codingRead.blocks,capturedUtc:doc.codingRead.blocks.at(-1).receivedUtc,provenance:'source-vehicle-read',freshReadProven:true,
  completeness:'source-qualified-complete-record-set',liveVerified:false});
 assert.equal(normalized.identity.software,identity.software);
 assert.equal(identity.software,'1234');assert.equal(identity.hardware,'9G130103000');checks++;
 for(const mutate of [d=>d.codingRead.blocks.pop(),d=>d.codingRead.blocks[0].dataHex+='00',d=>d.codingRead.completeScopeSource.requestLengths[0].bytes++,
  d=>d.codingRead.completeOriginalRead=false,d=>d.codingRead.definitionSha256='0'.repeat(64),d=>d.codingRead.codingWriteQualified=true,
  d=>d.codingRead.ecuId=47,d=>d.readContract.txId=0x725,d=>d.readContract.identityConstraints={versionRules:['invented']},
  d=>d.vehicleIdentitySha256='0'.repeat(64),d=>d.identityInfo.pop(),d=>d.identityInfo.find(r=>r.requestHex==='1A91').value='other',
  d=>d.identityInfo.find(r=>r.requestHex==='1A95').value='other',d=>d.codingRead.fields[0].editable=true,d=>d.readContract.identityRequests[0].record.at++]){
  const invalid=structuredClone(doc);mutate(invalid);assert.throws(()=>validateCompleteCodingRead(invalid,seen),undefined,name);checks++;
 }
 const missing=structuredClone(seen);missing.delete('1A9A');assert.throws(()=>validateCompleteCodingRead(doc,missing));checks++;
 const conflict=structuredClone(seen);conflict.get('1A9F')[0]='5A9F58323030';assert.throws(()=>validateCompleteCodingRead(doc,conflict));checks++;
 const incomplete=structuredClone(seen);incomplete.get('1A9F').pop();assert.throws(()=>validateCompleteCodingRead(doc,incomplete));checks++;
 const badVin=structuredClone(seen),invalidDoc=structuredClone(doc);badVin.set('1A90',badVin.get('1A90').map(pdu=>'5A90D7'+pdu.slice(6)));
 invalidDoc.identityInfo.find(r=>r.requestHex==='1A90').pduSha256=createHash('sha256').update(Buffer.from(badVin.get('1A90')[0],'hex')).digest('hex');
 assert.throws(()=>validateCompleteCodingRead(invalidDoc,badVin));checks++;
}
const model=await load(files[0]);const identity=validateCompleteCodingRead(model.result,observed(model));
const temp=await fs.mkdtemp(path.join(task,'pdk-store-')),desktop=path.join(temp,'Desktop');await fs.mkdir(desktop);
let failDesktop=true,unsupported=false,reads=0;
const workspace=createCodingWorkspace({directory:path.join(temp,'store'),desktopDirectory:()=>failDesktop?desktop+'/missing':desktop,
 readClient:async()=>{reads++;return unsupported?{ok:false,error:'coding-complete-scope-unqualified'}:{ok:true,releaseVerified:true,result:structuredClone(model.result),identity};}});
assert.deepEqual(await workspace.handle({action:'capability',ecu:'pdk'},1),{ok:true,readReady:true,backupReady:true});
assert.equal((await workspace.handle({action:'read',ecu:'pdk',dataHex:'00'},1)).error,'coding-workspace-invalid');
assert.equal((await workspace.handle({action:'read',ecu:'pdk'},1)).error,'coding-backup-failed');
failDesktop=false;const backed=await workspace.handle({action:'backup',ecu:'pdk'},1);assert.equal(backed.saved,true);assert.equal(backed.snapshot,undefined);
const original=await fs.readFile(backed.filePath),internal=await fs.readdir(path.join(temp,'store'));
const read=await workspace.handle({action:'read',ecu:'pdk'},1);assert.equal(read.ok,true);const snapshot=acceptCodingSnapshot(read.snapshot,'pdk');
assert.equal(snapshot.fields.length,model.result.codingRead.definitionFieldCount);assert.equal(snapshot.canWrite,false);assert.equal(snapshot.canRestore,false);assert.ok(snapshot.fields.every(f=>f.group==='变速箱'&&!f.editable));
assert.deepEqual(await fs.readFile(backed.filePath),original);assert.deepEqual(await fs.readdir(path.join(temp,'store')),internal);assert.equal((await fs.readdir(desktop)).length,1);
const backup=JSON.parse(original);assert.equal(backup.provenance,'source-vehicle-read');assert.equal(backup.freshReadProven,true);assert.throws(()=>normalizeCodingBackup(backup));
assert.throws(()=>normalizeSourceCodingBackup({...backup,profileId:'toString',sourceHash:undefined,scopeHash:undefined}));checks++;
unsupported=true;assert.equal((await workspace.handle({action:'read',ecu:'pdk'},1)).error,'coding-client-unavailable');assert.deepEqual(await fs.readdir(path.join(temp,'store')),internal);
assert.equal((await workspace.handle({action:'write',ecu:'pdk',reviewId:'invented'},1)).error,'coding-client-unavailable');checks+=17;
for(const native of [false,true]){
 const fixture=await load(files.find(n=>n.includes('-981-')&&n.endsWith(native?'-vnci.json':'-elm.json')));
 const vehicle=JSON.parse(await fs.readFile(path.join(host,'.local/obd-ecm-uds-reader-20261016/vehicle-source-981.json')));
 const gate=createTransportGate();let calls=0,resumes=0,unsupportedNext=false;
 const manager=createManufacturerStream({repoRoot:root,env:{},allowInjectedLive:true,gate,
  connection:{snapshot:()=>({purpose:'diagnostic',model:native?'VNCI':'ELM327',serialBaud:null}),selectedId:()=>native?'vnci:10001':'bt:001122334455',reserveDispatch:()=>true,releaseDispatch(){},suspendForSession:async()=>({ok:true}),resumeAfterSession:async proof=>{assert.equal(proof.nativeReleaseVerified,true);resumes++;return {ok:true};}},
  spawnFn(){const child=new EventEmitter();child.stdin=new PassThrough();child.stdout=new PassThrough();child.stderr=new PassThrough();child.kill=()=>{};
   child.stdin.once('data',raw=>{const request=JSON.parse(raw);const coding=++calls%2===0;
    assert.equal(request.action,coding?'manufacturer-read-coding':'manufacturer-identify-vehicle');
    if(coding){assert.equal(request.ecuId,2);assert.equal(request.sourceBound,undefined);assert.equal(request.completeOriginalRequired,true);assert.equal(request.expectedVehicleIdentitySha256,fixture.result.vehicleIdentitySha256);}
    setImmediate(async()=>{child.emit('spawn');let frames,exitCode=0;
     if(!coding)frames=vehicle.frames;
     else{
      const current=unsupportedNext?{result:{...fixture.result,ok:false,error:'coding-complete-scope-unqualified',codingRead:null},pdus:[]}:fixture;
      const result=structuredClone(current.result);delete result.transport.rawLog;exitCode=result.ok?0:1;
      frames=current.pdus.map(pdu=>({event:'diagnostic-pdu',result:{executionEnabled:true,liveVerified:false,writePayload:null,simulation:false,sourceKind:'independent-manufacturer-read',pdu}})).concat({event:'finished',result});
     }
     for(const frame of frames)child.stdout.write(JSON.stringify(frame)+'\n');setImmediate(()=>child.emit('close',exitCode));});});return child;}});
 assert.equal((await manager.handle({action:'read-coding',ecuId:2},1)).ok,false);
 let result=await manager.readCoding(2,1);assert.equal(result.ok,true,JSON.stringify(result));assert.equal(result.identity.vin,identity.vin);assert.equal(gate.owner(),null);assert.equal(manager.isIdle(),true);
 unsupportedNext=true;result=await manager.readCoding(2,1);assert.equal(result.error,'coding-complete-scope-unqualified');assert.equal(resumes,4);assert.equal(manager.isIdle(),true);assert.equal(gate.owner(),null);await manager.shutdown();checks+=12;
}
const code=await Promise.all(['pdk-coding-sources.mjs','coding-read-evidence.mjs','source-coding-backup.mjs','coding-workspace.mjs','manufacturer-stream.mjs'].map(async name=>({name,sha256:createHash('sha256').update(await fs.readFile(path.join(root,'apps/desktop/electron',name))).digest('hex')})));
await fs.writeFile(path.join(path.resolve(process.env.PORSCHE981_CODING_CHECK_OUTPUT||task),process.env.PORSCHE981_ACCEPT_APP_ROOT?'installed-desktop-result.json':'desktop-result.json'),JSON.stringify({passed:true,models:files.length,checks,reads,code,deviceIO:false,vehicleIO:false},null,2)+'\n');
console.log('PASS PDK complete read and immutable first backup',files.length,'source models',checks,'checks; no device/vehicle I/O');
