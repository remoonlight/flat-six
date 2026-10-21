/** Production read/backup boundary with original-client modeled replies. No I/O to a vehicle. */
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import path from 'node:path';
import {EventEmitter} from 'node:events';
import {PassThrough} from 'node:stream';
import {createHash} from 'node:crypto';
import {pathToFileURL} from 'node:url';
const host=path.resolve(import.meta.dirname,'..'),root=path.resolve(process.env.PORSCHE981_ACCEPT_APP_ROOT || host);
const loadModule=name=>import(pathToFileURL(path.join(root,name)));
const {validateCompleteCodingRead}=await loadModule('apps/desktop/electron/coding-read-evidence.mjs');
const {createCodingWorkspace}=await loadModule('apps/desktop/electron/coding-workspace.mjs');
const {normalizeCodingBackup}=await loadModule('apps/desktop/electron/diagnostic-preparation.mjs');
const {createManufacturerStream}=await loadModule('apps/desktop/electron/manufacturer-stream.mjs');
const {createTransportGate}=await loadModule('apps/desktop/electron/obd-connection.mjs');
// This assertion uses the source DTO guard; the installed renderer is compiled
// into dist and is exercised separately by the Electron acceptance script.
const {acceptCodingSnapshot}=await import(pathToFileURL(path.join(host,'apps/desktop/src/obd/coding-workspace-state.mjs')));

const task=path.resolve(process.env.PORSCHE981_CODING_READ_MODELS || '.local/obd-coding-desktop-20261018');
const directory=path.join(task,'source-models/models');
const files=(await fs.readdir(directory)).filter(n=>/^coding-\d+-\d+-\d+-(elm|vnci)\.json$/.test(n)).sort();
assert.ok(files.length);
const digest=v=>createHash('sha256').update(v).digest('hex');
const observed=m=>{const rows=new Map();for(const p of m.pdus){if(p.duringCleanup)continue;const a=rows.get(p.requestHex)||[];a.push(p.pduHex);rows.set(p.requestHex,a);}return rows;};
const load=async n=>JSON.parse(await fs.readFile(path.join(directory,n),'utf8'));
let checks=0;
for(const file of files){
  const m=await load(file);assert.equal(m.syntheticPort,true);const doc=m.result,seen=observed(m);
  const identity=validateCompleteCodingRead(doc,seen);assert.equal(identity.generation,doc.vehicleGeneration);checks++;
  const emptyDisplay=structuredClone(doc);emptyDisplay.codingRead.fields[0].currentDisplay='';
  assert.deepEqual(validateCompleteCodingRead(emptyDisplay,seen),identity);checks++;
  for(const mutate of [d=>d.codingRead.blocks.pop(),d=>d.codingRead.blocks[0].dataHex+='00',
    d=>d.codingRead.blocks[0].dataHex='00'.repeat(d.codingRead.blocks[0].dataHex.length/2),
    d=>d.codingRead.completeScopeSource.sha256='0'.repeat(64),d=>d.codingRead.definitionSha256='0'.repeat(64),
    d=>d.codingRead.completeOriginalRead=false,d=>d.codingRead.codingWriteQualified=true,
    d=>d.transport.qualification.freshRead=false,d=>d.readContract.txId=0x700,
    d=>d.vehicleIdentitySha256='0'.repeat(64),d=>d.identityInfo[0].pduSha256='0'.repeat(64),d=>d.identityInfo.splice(3,1),
    d=>d.identityInfo.find(r=>r.requestHex==='22F1A3').value='other',d=>d.identityInfo.find(r=>r.requestHex==='22F189').value='other',
    d=>d.codingRead.fields[0].editable=true,d=>d.codingRead.fields.pop()]){
    const invalid=structuredClone(doc);mutate(invalid);assert.throws(()=>validateCompleteCodingRead(invalid,seen),undefined,file);checks++;
  }
  const missing=structuredClone(seen);missing.delete(doc.codingRead.blocks[0].requestHex);assert.throws(()=>validateCompleteCodingRead(doc,missing));checks++;
}
const m=await load(files[0]),identity=validateCompleteCodingRead(m.result,observed(m));
const temp=await fs.mkdtemp(path.join(task,'coding-store-'));const desktop=path.join(temp,'desktop');await fs.mkdir(desktop);
let reads=0,failDesktop=false,hold,scopeChanged=false;
const reader=async()=>{reads++;if(hold)await hold;const doc=structuredClone(m.result);if(scopeChanged)doc.codingRead.completeOriginalRead=false;return {ok:true,result:doc,identity,releaseVerified:true};};
const workspace=createCodingWorkspace({directory:path.join(temp,'store'),desktopDirectory:()=>failDesktop?path.join(temp,'missing'):desktop,readClient:reader});
assert.deepEqual(await workspace.handle({action:'capability',ecu:'headlamp-left'},1),{ok:true,readReady:true,backupReady:true});
assert.equal((await workspace.handle({action:'capability',ecu:'whole-vehicle'},1)).readReady,false);
for(const extra of [{identity},{blocks:m.result.codingRead.blocks},{id:'imported'},{sourceHash:'a'.repeat(64)},{ecuId:47}]){
  assert.equal((await workspace.handle({action:'read',ecu:'headlamp-left',...extra},1)).error,'coding-workspace-invalid');checks++;
}
for(const action of ['write','restore','preview-restore'])assert.equal((await workspace.handle({action,ecu:'headlamp-left',...(action==='preview-restore'?{readId:'r'}:{reviewId:'r'})},1)).error,'coding-client-unavailable');
failDesktop=true;const failed=await workspace.handle({action:'read',ecu:'headlamp-left'},1);assert.equal(failed.error,'coding-backup-failed');assert.equal(failed.snapshot,undefined);
failDesktop=false;const backup=await workspace.handle({action:'backup',ecu:'headlamp-left'},1);assert.equal(backup.saved,true);assert.equal(backup.snapshot,undefined);
const first=await fs.readFile(backup.filePath);const internalBefore=await fs.readdir(path.join(temp,'store'));
const result=await workspace.handle({action:'read',ecu:'headlamp-left'},1);
assert.equal(result.ok,true);const snapshot=acceptCodingSnapshot(result.snapshot,'headlamp-left');assert.equal(snapshot.canWrite,false);assert.equal(snapshot.canRestore,false);
assert.equal(snapshot.identity.vin,identity.vin);assert.equal(snapshot.fields.length,m.result.codingRead.definitionFieldCount);
assert.equal((await fs.readdir(desktop)).length,1);assert.deepEqual(await fs.readFile(backup.filePath),first);
assert.deepEqual(await fs.readdir(path.join(temp,'store')),internalBefore);
const saved=JSON.parse(first);assert.equal(saved.provenance,'source-vehicle-read');assert.equal(saved.freshReadProven,true);assert.throws(()=>normalizeCodingBackup(saved));
scopeChanged=true;assert.equal((await workspace.handle({action:'read',ecu:'headlamp-left'},1)).error,'coding-read-failed');scopeChanged=false;
let release;hold=new Promise(resolve=>{release=resolve;});const pending=workspace.handle({action:'read',ecu:'headlamp-left'},1);await new Promise(resolve=>setImmediate(resolve));
assert.equal(workspace.isIdle(),false);assert.equal((await workspace.handle({action:'read',ecu:'headlamp-right'},2)).error,'vehicle-busy');release();await pending;hold=null;
await fs.writeFile(backup.filePath,'damaged');assert.equal((await workspace.handle({action:'read',ecu:'headlamp-left'},1)).error,'coding-backup-failed');assert.equal(await fs.readFile(backup.filePath,'utf8'),'damaged');
checks+=16;

// Real manager handoff and process close. Native and serial branches share the
// same complete-read validator; no arbitrary raw request is exposed to IPC.
for(const native of [false,true]){
 const fixture=await load(files.find(n=>n.includes('-981-47-')&&n.endsWith(native?'-vnci.json':'-elm.json')));
 const vehicle=JSON.parse(await fs.readFile(path.join(host,'.local/obd-ecm-uds-reader-20261016/vehicle-source-981.json')));
 let calls=0,resumes=0,close=false,proofs=[];const gate=createTransportGate();
 const manager=createManufacturerStream({repoRoot:root,env:{},allowInjectedLive:true,gate,
  connection:{snapshot:()=>({purpose:'diagnostic',model:native?'VNCI':'ELM327',serialBaud:null}),selectedId:()=>native?'vnci:10001':'bt:001122334455',
   reserveDispatch:()=>true,releaseDispatch(){},suspendForSession:async()=>({ok:true}),resumeAfterSession:async v=>{resumes++;proofs.push(v.nativeReleaseVerified);return {ok:true};}},
  spawnFn(){const child=new EventEmitter();child.stdin=new PassThrough();child.stdout=new PassThrough();child.stderr=new PassThrough();child.kill=()=>{};
   child.stdin.once('data',data=>{const request=JSON.parse(data.toString());calls++;assert.equal(request.action,calls===1?'manufacturer-identify-vehicle':'manufacturer-read-coding');
    if(calls===2)assert.equal(request.expectedVehicleIdentitySha256,fixture.result.vehicleIdentitySha256);
    setImmediate(()=>{child.emit('spawn');const frames=calls===1?vehicle.frames:fixture.pdus.map(pdu=>({event:'diagnostic-pdu',result:{executionEnabled:true,liveVerified:false,writePayload:null,simulation:false,sourceKind:'independent-manufacturer-read',pdu}})).concat({event:'finished',result:fixture.result});
     for(const frame of frames)child.stdout.write(JSON.stringify(frame)+'\n');setImmediate(()=>{close=true;child.emit('close',0);});});});return child;}});
 assert.equal((await manager.handle({action:'read-coding',ecuId:47},1)).ok,false);
 const value=await manager.readCoding(47,1);assert.equal(value.ok,true,JSON.stringify(value));assert.equal(value.releaseVerified,true);
 assert.equal(value.identity.vin,identity.vin);assert.equal(calls,2);assert.equal(resumes,2);assert.deepEqual(proofs,[true,true]);assert.equal(close,true);assert.equal(gate.owner(),null);assert.equal(manager.isIdle(),true);await manager.shutdown();checks+=9;
}
const code=await Promise.all(['coding-read-evidence.mjs','coding-workspace.mjs','source-coding-backup.mjs','swsg-coding-sources.mjs','manufacturer-stream.mjs','coding-backup-store.mjs'].map(async n=>({name:n,sha256:digest(await fs.readFile(path.join(root,'apps/desktop/electron',n)))})));
await fs.writeFile(path.join(path.resolve(process.env.PORSCHE981_CODING_CHECK_OUTPUT||task),process.env.PORSCHE981_ACCEPT_APP_ROOT?'installed-desktop-read-result.json':'desktop-read-result.json'),JSON.stringify({passed:true,models:files.length,checks,reads,deviceIO:false,vehicleIO:false,code},null,2)+'\n');
console.log('PASS complete coding read / first backup',files.length,'source models',checks,'checks; no vehicle I/O');
