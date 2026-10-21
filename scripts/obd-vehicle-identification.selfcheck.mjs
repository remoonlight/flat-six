/** Actual Python identity outputs, modeled worker/port; no device or vehicle IO. */
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import path from 'node:path';
import {EventEmitter} from 'node:events';
import {PassThrough} from 'node:stream';
import {pathToFileURL} from 'node:url';
const root=path.resolve(process.env.PORSCHE981_ACCEPT_APP_ROOT || path.join(import.meta.dirname,'..'));
const {createManufacturerStream}=await import(pathToFileURL(path.join(root,'apps/desktop/electron/manufacturer-stream.mjs')));
const {createTransportGate}=await import(pathToFileURL(path.join(root,'apps/desktop/electron/obd-connection.mjs')));

const directory = path.resolve(process.env.PORSCHE981_VEHICLE_MODELS || '.local/obd-ecm-uds-reader-20261016');
const request = {action:'identify-vehicle', confirmedReadOnly:true, x431Inactive:true};
const tick = () => new Promise(resolve => setImmediate(resolve));
function rig() {
  let worker, resume, spawns = 0, releaseProof;
  const manager = createManufacturerStream({repoRoot:root, env:{}, allowInjectedLive:true, gate:createTransportGate(),
    connection:{snapshot:()=>({purpose:'diagnostic',model:'ELM327',serialBaud:null}), selectedId:()=> 'bt:001122334455',
      reserveDispatch:()=>true, releaseDispatch(){}, suspendForSession:async()=>({ok:true}),
      resumeAfterSession({nativeReleaseVerified}){releaseProof=nativeReleaseVerified;return new Promise(resolve=>{resume=resolve;});}},
    spawnFn(){spawns++;worker=new EventEmitter();worker.stdin=new PassThrough();worker.stdout=new PassThrough();worker.stderr=new PassThrough();
      worker.input='';worker.stdin.on('data',data=>{worker.input+=data;});worker.kill=()=>{};return worker;}});
  return {manager,get worker(){return worker;},get spawns(){return spawns;},get proof(){return releaseProof;},resume:()=>resume?.({ok:true})};
}
const status = (r,id) => r.manager.handle({action:'status',sessionId:id,cursor:0,connectionCursor:0},1);
const vehicleModelFile=async generation=>{const preferred=path.join(directory,'vehicle-source-'+generation+'.json');try{await fs.access(preferred);return preferred;}catch{return path.join(directory,'vehicle-'+generation+'.json');}};
const checks = [];
for (const generation of ['981','982']) {
  const model = JSON.parse(await fs.readFile(await vehicleModelFile(generation),'utf8'));
  assert.equal(model.syntheticPort,true);assert.equal(model.vehicleIO,false);assert.equal(model.deviceIO,false);
  const r=rig(), start=await r.manager.handle(request,1);assert.equal(start.ok,true,JSON.stringify(start));
  const payload=JSON.parse(r.worker.input);
  assert.equal(payload.action,'manufacturer-identify-vehicle');
  for(const field of ['generation','profileId','ecuId','parameterIds','cycleCount','sourceBound'])assert.equal(payload[field],undefined);
  r.worker.emit('spawn');for(const frame of model.frames)r.worker.stdout.write(JSON.stringify(frame)+'\n');r.worker.emit('close',0);await tick();
  assert.equal(status(r,start.sessionId).released,false);assert.equal(r.proof,true);assert.equal(r.manager.isIdle(),false);
  r.resume();await tick();const done=status(r,start.sessionId);assert.equal(done.error,null,JSON.stringify(done));
  assert.equal(done.released,true);assert.equal(done.result.vehicleGeneration,generation);assert.deepEqual(done.events,[]);
  assert.equal((await r.manager.handle({action:'save',sessionId:start.sessionId},1)).ok,false);
  await r.manager.shutdown();checks.push(generation+': fresh gateway output, typed automatic probe, release awaited, identity cannot be saved as data');
}
const base=JSON.parse(await fs.readFile(await vehicleModelFile('981'),'utf8'));
for(const change of [doc=>{delete doc.vehicleGeneration;},doc=>{doc.vehicleGeneration='987';},doc=>{doc.vehicleIdentitySha256='wrong';},doc=>{doc.readContract.ecuId=1;}]) {
  const r=rig(), start=await r.manager.handle(request,1), frames=structuredClone(base.frames);change(frames.at(-1).result);
  r.worker.emit('spawn');for(const frame of frames)r.worker.stdout.write(JSON.stringify(frame)+'\n');r.worker.emit('close',0);await tick();
  assert.equal(r.proof,false);r.resume();await tick();assert.ok(status(r,start.sessionId).error);await r.manager.shutdown();
}
checks.push('missing/unsupported vehicle model, invalid vehicle identity digest and wrong ECU rejected');
{
  const r=rig();for(const [key,value] of [['generation','982'],['ecuId',9],['profileId','injected'],['parameterIds',[]],['continuous',true]])
    assert.equal((await r.manager.handle({...request,[key]:value},1)).ok,false);
  assert.equal(r.spawns,0);await r.manager.shutdown();checks.push('renderer cannot choose a model or source recipe in automatic identification');
}
const output=path.resolve(process.env.PORSCHE981_ACCEPT_OUTPUT || directory);await fs.mkdir(output,{recursive:true});
await fs.writeFile(path.join(output,'vehicle-node-result.json'),JSON.stringify({passed:true,checks,moduleRoot:root,deviceIO:false,vehicleIO:false},null,2)+'\n');
console.log('PASS '+checks.length+' automatic vehicle identification checks');
