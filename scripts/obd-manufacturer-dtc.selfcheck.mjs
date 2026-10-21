/** Source worker outputs across the typed desktop fault entry; modeled IO only. */
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import {createHash} from 'node:crypto';
import path from 'node:path';
import {EventEmitter} from 'node:events';
import {PassThrough} from 'node:stream';
import {pathToFileURL} from 'node:url';
const root=path.resolve(process.env.PORSCHE981_ACCEPT_APP_ROOT || path.join(import.meta.dirname,'..'));
const {createManufacturerStream}=await import(pathToFileURL(path.join(root,'apps/desktop/electron/manufacturer-stream.mjs')));
const {createTransportGate}=await import(pathToFileURL(path.join(root,'apps/desktop/electron/obd-connection.mjs')));
const {validManufacturerDtcResult,validManufacturerIdentityInfo}=await import(pathToFileURL(path.join(root,'apps/desktop/electron/manufacturer-evidence.mjs')));
const {FAULT_ONLY_PROFILES,liveReadTargets,faultReadTargets}=await import(pathToFileURL(path.join(root,'apps/desktop/src/obd/manufacturer-profiles.mjs')));
const directory=path.resolve(process.env.PORSCHE981_DTC_MODELS || '.local/obd-topology-details-20261017/models');
const files=(await fs.readdir(directory)).filter(name=>/^dtc-worker-\d+-\d+-(elm|vnci)\.json$/.test(name));
assert.ok(files.length);
const tick=()=>new Promise(resolve=>setImmediate(resolve));
function rig(native=false) {
  let child, resume, proof;
  const manager=createManufacturerStream({repoRoot:root,env:{},allowInjectedLive:true,gate:createTransportGate(),
    connection:{snapshot:()=>({purpose:'diagnostic',model:native?'VNCI':'ELM327',serialBaud:null}),
      selectedId:()=>native?'vnci:10001':'bt:001122334455',reserveDispatch:()=>true,releaseDispatch(){},
      suspendForSession:async()=>({ok:true}),resumeAfterSession(value){proof=value.nativeReleaseVerified;return new Promise(resolve=>{resume=resolve;});}},
    spawnFn(){child=new EventEmitter();child.stdin=new PassThrough();child.stdout=new PassThrough();child.stderr=new PassThrough();
      child.input='';child.stdin.on('data',value=>{child.input+=value;});child.kill=()=>{};return child;}});
  return {manager,get child(){return child;},get proof(){return proof;},resume:()=>resume?.({ok:true})};
}
const status=(r,id)=>r.manager.handle({action:'status',sessionId:id},1);
let checks=0;
for(const file of files) {
  const model=JSON.parse(await fs.readFile(path.join(directory,file),'utf8')),doc=model.frames.at(-1).result;
  // Direct client models retain byte logs; the stream worker supplies on_cycle
  // and has a zero-length ordinary log. Project that documented stream shape.
  doc.transport.rawLog=[];
  assert.equal(model.deviceIO,false);assert.equal(model.vehicleIO,false);assert.equal(validManufacturerDtcResult(doc),true,file);
  if (doc.readContract.sourceIdentityKind==='piwis-led-identity') {
    const observed=new Map(model.frames.filter(frame=>frame.event==='diagnostic-pdu'&&!frame.result.pdu.duringCleanup)
      .map(frame=>[frame.result.pdu.requestHex,createHash('sha256').update(Buffer.from(frame.result.pdu.pduHex,'hex')).digest('hex')]));
    assert.equal(validManufacturerIdentityInfo(doc,observed),true,file+' own PIWIS identity fields');checks++;
    for(const change of [doc=>{doc.readContract.sourceBridge.sources['0.0.0@SCHEINWERFER_LED.bv.db']='0'.repeat(64);},
      doc=>{doc.readContract.ecuId=48;},doc=>{doc.readContract.txId=0x754;},
      doc=>{doc.identityInfo.pop();},doc=>{doc.identityInfo[0].label='Invented identity field';},
      doc=>{doc.identityInfo[2].recordIndex=0;},doc=>{doc.readContract.sourceIdentityKind='other';}]) {
      const invalid=structuredClone(doc);change(invalid);
      assert.equal(validManufacturerIdentityInfo(invalid,observed),false,file+' invalid LED identity');checks++;
    }
  }
  if (doc.dtcRead.piwisReadSource) {
    for (const change of [doc=>{delete doc.dtcRead.piwisReadSource;},
      doc=>{doc.dtcRead.piwisReadSource.requestSha256='0'.repeat(64);},
      doc=>{doc.dtcRead.piwisReadSource.responseSha256='0'.repeat(64);},
      doc=>{doc.readContract.txId=0x700;},doc=>{doc.readContract.sourceBridge.version='unknown';},
      doc=>{doc.dtcRead.responses.at(-1).requestHex='19020C';},
      doc=>{delete doc.dtcRead.piwisReadSource;doc.dtcRead.descriptorHex='04FF0203FF0009';}]) {
      const invalid=structuredClone(doc);change(invalid);
      assert.equal(validManufacturerDtcResult(invalid),false,file+' invalid PIWIS source');checks++;
    }
  }
  if (doc.vehicleAnchor) {
    for (const change of [anchor=>{anchor.profileId='9x1:Steering_Control_Unit:unprogrammierte_EPS';},
      anchor=>{anchor.profileId+='-unqualified';},anchor=>{anchor.freshRead=false;}]) {
      const invalid=structuredClone(doc);change(invalid.vehicleAnchor);
      assert.equal(validManufacturerDtcResult(invalid),false,file+' invalid vehicle anchor');checks++;
    }
  }
  const r=rig(file.endsWith('-vnci.json'));
  if (FAULT_ONLY_PROFILES.has(model.profileId)) {
    assert.deepEqual(liveReadTargets(model.profileId),[]);
    assert.ok(faultReadTargets(model.profileId).includes(model.ecuId));
    const denied=await r.manager.handle({action:'start-read',generation:model.generation,ecuId:model.ecuId,
      profileId:model.profileId,parameterIds:['a'.repeat(64)],continuous:true,confirmedReadOnly:true,x431Inactive:true},1);
    assert.equal(denied.error,'manufacturer-profile-unqualified');assert.equal(r.child,undefined);
  }
  const request={action:'read-dtc',generation:doc.vehicleGeneration,ecuId:doc.dtcRead.ecuId,
    expectedVehicleIdentitySha256:doc.vehicleIdentitySha256,confirmedReadOnly:true,x431Inactive:true};
  const started=await r.manager.handle(request,1);assert.equal(started.ok,true,JSON.stringify(started));
  const payload=JSON.parse(r.child.input);assert.equal(payload.action,'manufacturer-read-dtc');assert.deepEqual(payload.parameterIds,[]);
  assert.equal(payload.expectedVehicleIdentitySha256,doc.vehicleIdentitySha256);assert.equal(payload.cycleCount,1);
  r.child.emit('spawn');for(const frame of model.frames)r.child.stdout.write(JSON.stringify(frame)+'\n');assert.equal(status(r,started.sessionId).error,null,file+' before close');r.child.emit('close',0);await tick();
  assert.equal(r.proof,true,file+JSON.stringify(status(r,started.sessionId)));assert.equal(status(r,started.sessionId).released,false);
  r.resume();await tick();const done=status(r,started.sessionId);
  assert.equal(done.error,null,JSON.stringify(done));assert.equal(done.releaseVerified,true);assert.deepEqual(done.events,[]);
  assert.equal((await r.manager.handle({action:'save',sessionId:started.sessionId},1)).ok,false);
  await r.manager.shutdown();checks++;
}
const base=JSON.parse(await fs.readFile(path.join(directory,files[0]),'utf8')).frames.at(-1).result;
{
 const model=JSON.parse(await fs.readFile(path.join(directory,files[0]),'utf8'));
 const observed=new Map(model.frames.filter(frame=>frame.event==='diagnostic-pdu'&&!frame.result.pdu.duringCleanup)
  .map(frame=>[frame.result.pdu.requestHex,createHash('sha256').update(Buffer.from(frame.result.pdu.pduHex,'hex')).digest('hex')]));
 assert.equal(validManufacturerIdentityInfo(base,observed),true);
 for(const change of [doc=>{doc.identityInfo[0].label='Invented field';},doc=>{doc.identityInfo[0].pduSha256='f'.repeat(64);},
  doc=>{doc.identityInfo[0].requestHex='22FFFF';},doc=>{doc.identityInfo[0].recordIndex=999;},
  doc=>{doc.identityInfo[0].rawPdu='private';},doc=>{doc.identityInfo.push({...doc.identityInfo[0]});}]) {
  const doc=structuredClone(base);change(doc);assert.equal(validManufacturerIdentityInfo(doc,observed),false);checks++;
 }
}
for(const change of [doc=>{doc.dtcRead.decoded.records=[];},doc=>{doc.dtcRead.responses.at(-1).pduHex='5902FF';},
 doc=>{doc.dtcRead.decoded.records[0].statusHex='FF';},doc=>{doc.vehicleGeneration='987';},doc=>{delete doc.vehicleIdentitySha256;},
 doc=>{doc.dtcRead.descriptorHex='04FF0203FF000C';},doc=>{doc.dtcRead.freshRead=false;},doc=>{doc.dtcRead.responses.at(-1).requestHex='14FFFFFF';}]) {
  const doc=structuredClone(base);change(doc);assert.equal(validManufacturerDtcResult(doc),false);checks++;
}
{
 const doc=structuredClone(base),row=doc.dtcRead.decoded.records[0];row.originalEnglish='Original factory English';
 assert.equal(validManufacturerDtcResult(doc),true,'original English fallback');row.originalEnglish='中文故障';assert.equal(validManufacturerDtcResult(doc),false);checks++;
}
const sourceCode={};
for(const name of ['apps/desktop/electron/manufacturer-stream.mjs','apps/desktop/electron/manufacturer-evidence.mjs','apps/desktop/src/obd/manufacturer-profiles.mjs'])sourceCode[name]=createHash('sha256').update(await fs.readFile(path.join(root,name))).digest('hex');
const output=path.resolve(process.env.PORSCHE981_ACCEPT_OUTPUT || directory);await fs.mkdir(output,{recursive:true});
await fs.writeFile(path.join(output,'dtc-node-result.json'),JSON.stringify({passed:true,checks,sourceCode,sourceWorkerModels:files.length,moduleRoot:root,deviceIO:false,vehicleIO:false},null,2)+'\n');
console.log(`PASS ${checks} source fault desktop checks (${files.length} source client models)`);
