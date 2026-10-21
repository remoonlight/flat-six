/** Typed platform scopes using actual Python fixture outputs; no device IO. */
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import path from 'node:path';
import {pathToFileURL} from 'node:url';
import {EventEmitter} from 'node:events';
import {PassThrough} from 'node:stream';
const root=path.resolve(process.env.PORSCHE981_ACCEPT_APP_ROOT||path.join(import.meta.dirname,'..'));
const task=path.resolve(process.env.PORSCHE981_GENERATION_MODELS||path.join(root,'.local/obd-982-reader-20261016'));
const output=path.resolve(process.env.PORSCHE981_ACCEPT_OUTPUT||path.join(task,'source-generation-node'));await fs.mkdir(output,{recursive:true});
const {createManufacturerStream}=await import(pathToFileURL(path.join(root,'apps/desktop/electron/manufacturer-stream.mjs')));
const {createTransportGate}=await import(pathToFileURL(path.join(root,'apps/desktop/electron/obd-connection.mjs')));
const model=JSON.parse(await fs.readFile(path.join(task,'identity-model-982-1.json'),'utf8'));assert.equal(model.generation,'982');assert.equal(model.syntheticPort,true);assert.equal(model.vehicleIO,false);
function rig(){
 let worker,spawns=0,reserved=0,suspended=0,proof;
 const manager=createManufacturerStream({repoRoot:root,env:{},allowInjectedLive:true,gate:createTransportGate(),
  connection:{snapshot:()=>({purpose:'diagnostic',model:'ELM327',serialBaud:null}),selectedId:()=> 'bt:001122334455',
   reserveDispatch(){reserved++;return true;},releaseDispatch(){reserved--;},async suspendForSession(){suspended++;return{ok:true};},
   async resumeAfterSession({nativeReleaseVerified}){proof=nativeReleaseVerified;reserved--;return{ok:true};}},
  spawnFn(){spawns++;worker=new EventEmitter();worker.stdin=new PassThrough();worker.stdout=new PassThrough();worker.stderr=new PassThrough();worker.input='';worker.stdin.on('data',r=>worker.input+=r);worker.kill=()=>{};return worker;}});
 return{manager,get worker(){return worker;},get spawns(){return spawns;},get reserved(){return reserved;},get suspended(){return suspended;},get proof(){return proof;}};
}
const request={action:'identify',generation:'982',ecuId:1,confirmedReadOnly:true,x431Inactive:true};
async function emit(r,mutate){
 const start=await r.manager.handle(request,1);assert.equal(start.ok,true,JSON.stringify(start));
 const input=JSON.parse(r.worker.input);assert.equal(input.generation,'982');assert.equal(input.profileId,model.requestProfileId);assert.equal(input.sourceBound,true);assert.deepEqual(input.parameterIds,[]);
 r.worker.emit('spawn');const frames=structuredClone(model.frames);mutate?.(frames);
 for(const frame of frames)r.worker.stdout.write(JSON.stringify(frame)+'\n');r.worker.emit('close',0);await new Promise(resolve=>setImmediate(resolve));
 return r.manager.handle({action:'status',sessionId:start.sessionId,cursor:0,connectionCursor:0},1);
}
let passed=false;const checks=[];
try{
 {
  const r=rig(),s=await emit(r);assert.equal(s.error,null,JSON.stringify(s));assert.equal(s.released,true);assert.equal(s.result.identifiedProfileId,model.profileId);
  assert.equal(r.proof,true);assert.equal(s.result.readContract.generation,'982');assert.equal(s.result.transport.qualification.generation,'982');assert.deepEqual(s.events,[]);assert.equal(r.reserved,0);await r.manager.shutdown();
  checks.push('982 chooses its own nominal DME, preserves the typed scope through fresh identity and waits for release without collecting values');
 }
 for(const mutate of [
  f=>{f.at(-1).result.readContract.generation='981';},
  f=>{f.at(-1).result.transport.qualification.generation='981';},
  f=>{const r=f.at(-1).result;r.identifiedProfileId='9x1:DME_BDE_Continental:SDI9_1_981_3_4L_EU5';r.readContract.profileId=r.identifiedProfileId;r.transport.qualification.variantLink.profile_id=r.identifiedProfileId;},
 ]){
  const r=rig(),s=await emit(r,mutate);assert.ok(s.error);assert.equal(r.proof,false);assert.equal(s.result?.identifiedProfileId,undefined);assert.deepEqual(s.events,[]);assert.equal(r.reserved,0);await r.manager.shutdown();
 }
 checks.push('wrong platform in the contract or qualification and a DME profile belonging to the other platform cannot publish an identity');
 {
  const r=rig();const read=(generation,profileId)=>({action:'start-read',generation,ecuId:1,profileId,parameterIds:['a'.repeat(64)],continuous:true,confirmedReadOnly:true,x431Inactive:true});
  for(const q of [read('981',model.profileId),read('982','9x1:DME_BDE_Continental:SDI9_1_981_3_4L_EU5'),read('982',undefined),read('982',{}),{...request,generation:'983'},{...request,generation:null},{...request,generation:true},{...request,generation:{}}])assert.equal((await r.manager.handle(q,1)).ok,false);
  assert.equal(r.spawns,0);assert.equal(r.reserved,0);assert.equal(r.suspended,0);await r.manager.shutdown();
  checks.push('unknown platform, malformed profile and a DME selected under the wrong platform are rejected before device handoff');
 }
 passed=true;
}finally{await fs.writeFile(path.join(output,'result.json'),JSON.stringify({passed,checks,actualPythonIdentityOutput:true,portsAndWorkersSynthetic:true,deviceIO:false,vehicleIO:false},null,2)+'\n');}
console.log('PASS typed generation: '+checks.length+' checks');
