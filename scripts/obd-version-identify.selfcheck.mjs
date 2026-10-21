/** Actual Python identity-only model output through the typed desktop manager; no hardware. */
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import path from 'node:path';
import {pathToFileURL} from 'node:url';
import {EventEmitter} from 'node:events';
import {PassThrough} from 'node:stream';
const source=path.resolve(import.meta.dirname,'..'),root=path.resolve(process.env.PORSCHE981_ACCEPT_APP_ROOT||source);
const models=path.resolve(process.env.PORSCHE981_IDENTITY_MODELS||path.join(source,'.local/obd-version-identify-20261016'));
const generation=process.env.PORSCHE981_IDENTITY_MODEL_GENERATION||'981';assert.ok(['981','982'].includes(generation));
const output=path.resolve(process.env.PORSCHE981_ACCEPT_OUTPUT||path.join(models,'source-node'));
const {createManufacturerStream}=await import(pathToFileURL(path.join(root,'apps/desktop/electron/manufacturer-stream.mjs')));
const {createTransportGate}=await import(pathToFileURL(path.join(root,'apps/desktop/electron/obd-connection.mjs')));
await fs.mkdir(output,{recursive:true});const checks=[];let passed=false;
const tick=()=>new Promise(resolve=>setImmediate(resolve));
function rig(deviceId="bt:001122334455"){
 let worker,resumeResolve,proof,spawns=0;const raw=[],gate=createTransportGate();
 const manager=createManufacturerStream({repoRoot:root,env:{},allowInjectedLive:true,gate,closeWaitMs:15,
  connection:{snapshot:()=>({purpose:'diagnostic',model:deviceId.startsWith('vnci:')?'VNCI':'ELM327',serialBaud:null}),selectedId:()=> deviceId,
   reserveDispatch:()=>true,releaseDispatch(){},suspendForSession:async()=>({ok:true}),
   resumeAfterSession({nativeReleaseVerified}){proof=nativeReleaseVerified;return new Promise(resolve=>{resumeResolve=resolve;});}},
  onDiagnosticPdu:event=>raw.push(event),
  spawnFn(){spawns++;worker=new EventEmitter();worker.stdin=new PassThrough();worker.stdout=new PassThrough();worker.stderr=new PassThrough();
   worker.input='';worker.stdin.on('data',raw=>{worker.input+=raw;});worker.kill=()=>{};return worker;},
 });
 return {manager,gate,raw,get worker(){return worker;},get spawns(){return spawns;},get proof(){return proof;},resume:()=>resumeResolve?.({ok:true})};
}
const request=ecuId=>({action:'identify',ecuId,...(generation==='982'?{generation}:{}),confirmedReadOnly:true,x431Inactive:true});
const status=(r,id)=>r.manager.handle({action:'status',sessionId:id,cursor:0,connectionCursor:0},1);
async function emit(r,model,mutate){
 const start=await r.manager.handle(request(model.ecuId),1);assert.equal(start.ok,true,JSON.stringify(start));
 const payload=JSON.parse(r.worker.input);assert.equal(payload.action,'manufacturer-identify');assert.equal(payload.ecuId,model.ecuId);assert.equal(payload.profileId,model.requestProfileId);assert.deepEqual(payload.parameterIds,[]);assert.equal(payload.cycleCount,1);
 assert.equal(payload.generation,generation);
 assert.equal(payload.sourceBound, [1,9].includes(model.ecuId) ? true : undefined, 'source mode is added only by the desktop for DME/Gateway');
 r.worker.emit('spawn');const frames=structuredClone(model.frames);if(mutate)mutate(frames);
 for(const frame of frames)r.worker.stdout.write(JSON.stringify(frame)+'\n');r.worker.emit('close',0);await tick();return start.sessionId;
}
try{
 for(const ecuId of [1,9,2,5,32,35,54,18,50,68,55,8,4,7,19,69,29,70,75,65,64,74,81,165,39,95,96,87,88]){
  const model=JSON.parse(await fs.readFile(path.join(models,'identity-model-'+ecuId+'.json'),'utf8'));assert.equal(model.syntheticPort,true);assert.equal(model.deviceIO,false);assert.equal(model.vehicleIO,false);
  const r=rig(),id=await emit(r,model);
  let s=status(r,id);assert.equal(s.state,'closing');assert.equal(s.released,false);assert.equal(r.manager.isIdle(),false);assert.equal(r.proof,true);assert.equal(r.gate.owner(),null);
  assert.equal((await r.manager.handle(request(ecuId),1)).error,'stream_busy');assert.equal((await r.manager.handle({action:'discard',sessionId:id},1)).error,'stream_busy');
  assert.equal(r.manager.handle({action:'status',sessionId:id},2).error,'unknown_session');
  r.resume();await tick();s=status(r,id);assert.equal(s.error,null,JSON.stringify(s));assert.equal(s.state,'finished');assert.equal(s.released,true);assert.equal(r.manager.isIdle(),true);
  assert.equal(s.result.identifiedProfileId,model.profileId);assert.equal(s.result.identityOnly,true);assert.deepEqual(s.events,[]);assert.deepEqual(s.result.readContract.groups,[]);
  assert.equal(s.result.readContract.generation,generation);assert.equal(s.result.transport.qualification.generation,generation);
  assert.ok(r.raw.length>0);assert.ok(r.raw.every(event=>event.profileId===null),'raw discovery records never claim the nominal version');
  assert.equal((await r.manager.handle({action:'save',sessionId:id},1)).error,'manufacturer_recording_unavailable');await r.manager.shutdown();
  checks.push('typed identity-only dispatch, source model '+ecuId+', matched directory, no values/save, raw capture provenance and awaited monitor resume');
 }
 {
  const rawModels=path.resolve(process.env.PORSCHE981_MODE50_MODELS||path.join(source,'.local/obd-mode50-vnci-20261017/models',generation));
  const model=JSON.parse(await fs.readFile(path.join(rawModels,'identity-model-vnci-88.json'),'utf8'));
  assert.equal(model.syntheticPort,true);assert.equal(model.deviceIO,false);assert.equal(model.vehicleIO,false);
  const deviceId=model.frames.at(-1).result.transport.adapter.deviceId;
  assert.ok(deviceId.startsWith('vnci:'));
  const r=rig(deviceId),id=await emit(r,model);
  assert.equal(JSON.parse(r.worker.input).deviceId,deviceId);
  assert.equal(status(r,id).state,'closing');assert.equal(r.proof,true);
  r.resume();await tick();const s=status(r,id);
  assert.equal(s.error,null);assert.equal(s.state,'finished');assert.equal(s.released,true);
  assert.equal(s.result.identifiedProfileId,model.profileId);assert.deepEqual(s.events,[]);
  assert.ok(r.raw.length>0);assert.ok(r.raw.every(e=>e.pdu.transportKind==='d-pdu-mode50'
   &&e.pdu.observation==='reassembled-complete-pdu'&&e.pdu.primitive===e.pdu.expectedPrimitive));
  await r.manager.shutdown();
  checks.push('source mode50 identity over owned VNCI RAW primitives, explicit reassembly provenance and awaited release');
 }
 const model=JSON.parse(await fs.readFile(path.join(models,'identity-model-5.json'),'utf8'));
 const mutations=[
  f=>{f.at(-1).result.identifiedProfileId='9x1:Waehlhebel:WH';},
  f=>{f.at(-1).result.readContract.profileId=model.requestProfileId;},
  f=>{f.at(-1).result.readContract.generation=generation==='981'?'982':'981';},
  f=>{f.at(-1).result.readContract.groups=[{requestHex:'22F100'}];},
  f=>{f.at(-1).result.transport.qualification.freshRead=false;},
  f=>{f.at(-1).result.samples=[{value:42}];},
  f=>{f.at(-1).result.requestCount=1;},
  f=>{f.at(-1).result.completedCycles=1;},
  f=>{f.unshift({event:'cycle',result:{executionEnabled:true,liveVerified:false,writePayload:null}});},
 ];
 for(const mutate of mutations){const r=rig(),id=await emit(r,model,mutate);assert.equal(r.proof,false);r.resume();await tick();assert.ok(status(r,id).error);await r.manager.shutdown();}
 checks.push('wrong unit/version/generation, nonempty measurement recipe, missing freshness and forged value/cycle outputs rejected');
 {
  const r=rig();for(const req of [{...request(5),parameterIds:[]},{...request(5),profileId:model.profileId},{...request(5),continuous:true},{...request(1),sourceBound:true},request(999),request(true)])assert.equal((await r.manager.handle(req,1)).ok,false);
  const shared='9x1:BV_ContrUnitLaneChangAssis2UDS:EV_RearRadarS1BoschPO416_001';
  for(const ecuId of [undefined,94,true,'95']) assert.equal((await r.manager.handle({action:'start-read',generation,profileId:shared,
    ...(ecuId === undefined ? {} : {ecuId}),parameterIds:['a'.repeat(64)],continuous:true,confirmedReadOnly:true,x431Inactive:true},1)).ok,false);
  assert.equal(r.spawns,0);await r.manager.shutdown();checks.push('renderer cannot supply a version, parameter selection, continuous mode or unknown unit');
 }
 {
  const r=rig(),id=await emit(r,model);assert.equal((await r.manager.shutdown()).error,'port_release_pending');assert.equal(r.manager.isIdle(),false);
  assert.equal(status(r,id).released,false);r.resume();await tick();assert.equal(status(r,id).released,true);assert.equal(r.manager.isIdle(),true);
  assert.equal((await r.manager.handle(request(5),1)).error,'stream_shutdown');checks.push('shutdown timeout preserves a pending monitor resume and status; no new task starts');
 }
 passed=true;
}finally{await fs.writeFile(path.join(output,'result.json'),JSON.stringify({passed,checks,generation,moduleRoot:root,pythonOutputsReal:true,portsAndWorkersSynthetic:true,deviceIO:false,vehicleIO:false},null,2)+'\n');}
console.log('PASS identity-only discovery: '+checks.length+' checks, no parameter samples or device I/O');
