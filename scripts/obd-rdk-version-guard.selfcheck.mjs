/** Real Python conflict outcomes through the typed desktop manager. Worker and
 * ports are synthetic; no physical device calls or parameter-file writes. */
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import path from 'node:path';
import {pathToFileURL} from 'node:url';
import {EventEmitter} from 'node:events';
import {PassThrough} from 'node:stream';
const source=path.resolve(import.meta.dirname,'..'),root=path.resolve(process.env.PORSCHE981_ACCEPT_APP_ROOT||source);
const models=path.resolve(process.env.PORSCHE981_RDK_MODELS||path.join(source,'.local/obd-rdk-selection-20261017/normal/models'));
const output=path.resolve(process.env.PORSCHE981_ACCEPT_OUTPUT||path.join(models,'source-node'));await fs.mkdir(output,{recursive:true});
const {createManufacturerStream}=await import(pathToFileURL(path.join(root,'apps/desktop/electron/manufacturer-stream.mjs')));
const {createTransportGate}=await import(pathToFileURL(path.join(root,'apps/desktop/electron/obd-connection.mjs')));
const files=(await fs.readdir(models)).filter(name=>name.startsWith('rejected-')&&name.endsWith('.json'));assert.equal(files.length,12);
const checks=[];const tick=()=>new Promise(resolve=>setImmediate(resolve));
for(const name of files){
 const model=JSON.parse(await fs.readFile(path.join(models,name),'utf8'));assert.equal(model.syntheticPort,true);assert.equal(model.deviceIO,false);
 const final=model.frames.at(-1).result;assert.equal(final.ok,false);assert.equal(final.error,'identity-mismatch');final.transport.rawLog=[];
 let worker,resumed=0;const gate=createTransportGate(),native=model.transport==='vnci';
 const manager=createManufacturerStream({repoRoot:root,env:{},allowInjectedLive:true,gate,
  connection:{snapshot:()=>({purpose:'diagnostic',model:native?'VNCI':'ELM327',serialBaud:null}),selectedId:()=>native?'vnci:10001':'bt:001122334455',
   reserveDispatch:()=>true,releaseDispatch(){},suspendForSession:async()=>({ok:true}),resumeAfterSession({nativeReleaseVerified}){assert.equal(nativeReleaseVerified,true);resumed++;return Promise.resolve({ok:true});}},
  spawnFn(){worker=new EventEmitter();worker.stdin=new PassThrough();worker.stdout=new PassThrough();worker.stderr=new PassThrough();worker.input='';worker.stdin.on('data',v=>{worker.input+=v;});worker.kill=()=>{};return worker;}});
 try{
  const action=model.action==='manufacturer-identify'?'identify':model.action==='manufacturer-read-dtc'?'read-dtc':'start-read';
  const request={action,generation:model.generation,ecuId:55,confirmedReadOnly:true,x431Inactive:true};
  if(action==='start-read')Object.assign(request,{profileId:model.profileId,parameterIds:model.parameterIds,continuous:true});
  if(action==='read-dtc'){
   const positive=JSON.parse(await fs.readFile(path.join(models,`dtc-worker-${model.generation}-55-${model.transport}.json`),'utf8'));
   request.expectedVehicleIdentitySha256=positive.frames.at(-1).result.vehicleIdentitySha256;
  }
  const started=await manager.handle(request,1);assert.equal(started.ok,true,JSON.stringify(started));
  assert.equal(JSON.parse(worker.input).action,model.action);worker.emit('spawn');for(const frame of model.frames)worker.stdout.write(JSON.stringify(frame)+'\n');worker.emit('close',0);await tick();
  const status=manager.handle({action:'status',sessionId:started.sessionId,cursor:0,connectionCursor:0},1);
  assert.equal(status.state,'finished');assert.equal(status.result?.ok,false,name);assert.equal(status.result?.error,'identity-mismatch',name);
  assert.equal(status.released,true);assert.equal(resumed,1);assert.equal(gate.owner(),null);
  assert.deepEqual(status.events,[]);assert.equal((await manager.handle({action:'save',sessionId:started.sessionId},1)).ok,false);
  checks.push(name);
 }finally{await manager.shutdown();}
}
await fs.writeFile(path.join(output,'result.json'),JSON.stringify({passed:true,checks,sourceWorkerOutcomes:files.length,emptyParameterEvents:true,moduleRoot:root,workerAndPortsSynthetic:true,deviceIO:false,vehicleIO:false},null,2)+'\n');console.log('PASS twelve RDK version mismatch outcomes: released head, visible error, no parameter data or save');
