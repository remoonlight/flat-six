/** Actual Python manufacturer model output through the desktop stream and PCAPNG writer.
 * The byte port and worker are synthetic; no physical device API is called. */
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import path from 'node:path';
import {pathToFileURL} from 'node:url';
import {EventEmitter} from 'node:events';
import {PassThrough} from 'node:stream';

const source=path.resolve(import.meta.dirname,'..');
const root=path.resolve(process.env.PORSCHE981_ACCEPT_APP_ROOT||source);
const output=path.resolve(process.env.PORSCHE981_ACCEPT_OUTPUT||path.join(source,'.local/obd-pdk-read-20261016/source-stream'));
const input=JSON.parse(await fs.readFile(process.env.PORSCHE981_MODEL_INPUT||path.join(source,'.local/obd-pdk-read-20261016/pdk-worker-model.json'),'utf8'));
assert.equal(input.syntheticPort,true);assert.equal(input.deviceIO,false);assert.equal(input.vehicleIO,false);
const deviceId=input.transport==='vnci'?input.frames.at(-1).result.transport.adapter.deviceId:'bt:001122334455';
if(input.transport==='vnci')assert.ok(deviceId.startsWith('vnci:'));
const {createManufacturerStream}=await import(pathToFileURL(path.join(root,'apps/desktop/electron/manufacturer-stream.mjs')));
const {createTransportGate}=await import(pathToFileURL(path.join(root,'apps/desktop/electron/obd-connection.mjs')));
const {createParameterRecordingService}=await import(pathToFileURL(path.join(root,'apps/desktop/electron/parameter-recording.mjs')));
await fs.mkdir(output,{recursive:true});const desktop=await fs.mkdtemp(path.join(output,'synthetic-desktop-'));
const writer=createParameterRecordingService({desktopPath:desktop});const gate=createTransportGate();
let worker,reserved=0,resumed=0;
const manager=createManufacturerStream({repoRoot:root,env:{},allowInjectedLive:true,gate,
 connection:{snapshot:()=>({purpose:'diagnostic',model:input.transport==='vnci'?'VNCI':'ELM327',serialBaud:null}),selectedId:()=> deviceId,
  reserveDispatch(){reserved++;return true;},releaseDispatch(){reserved--;},
  async suspendForSession(){return {ok:true};},async resumeAfterSession({nativeReleaseVerified}){assert.equal(nativeReleaseVerified,true);resumed++;reserved--;}},
 spawnFn(_exe,args){assert.equal(args.at(-1),'scripts.diagnostics.manufacturer_read');
  worker=new EventEmitter();worker.stdin=new PassThrough();worker.stdout=new PassThrough();worker.stderr=new PassThrough();
  worker.input='';worker.stdin.on('data',v=>{worker.input+=v;});worker.kill=()=>{};return worker;},
 // Exercise production provenance matching in an owned synthetic Desktop.
 // The retained artifact is explicitly named SYNTHETIC below.
 saveRecording:(recording,owner)=>writer.save(recording,owner),
});
let passed=false;const checks=[];
try{
 const started=await manager.handle({action:'start-read',...(input.generation?{generation:input.generation}:{}),profileId:input.profileId,ecuId:input.ecuId??2,parameterIds:input.parameterIds,
  continuous:true,confirmedReadOnly:true,x431Inactive:true},1);assert.equal(started.ok,true,JSON.stringify(started));
 const request=JSON.parse(worker.input);assert.equal(request.ecuId,input.ecuId??2);assert.equal(request.profileId,input.profileId);
 assert.equal(request.sourceBound,[1,9].includes(input.ecuId)?true:undefined);
 assert.equal(request.deviceId,deviceId);
 assert.equal(request.generation,input.generation||'981');assert.equal(request.cycleCount,null);assert.equal((await manager.handle({action:'save',sessionId:started.sessionId},1)).error,'stream_busy');
 worker.emit('spawn');for(const frame of input.frames)worker.stdout.write(JSON.stringify(frame)+'\n');worker.emit('close',0);
 await new Promise(resolve=>setImmediate(resolve));
 const status=manager.handle({action:'status',sessionId:started.sessionId,cursor:0,connectionCursor:0},1);
 assert.equal(status.error,null,JSON.stringify(status));assert.equal(status.state,'finished');assert.equal(status.released,true);
 const cycles=input.frames.filter(frame=>frame.event==='cycle');const expected=cycles.flatMap(frame=>frame.result.samples);
 assert.equal(status.events.flatMap(event=>event.samples).length,expected.length);assert.equal(status.events.length,cycles.length);assert.equal(reserved,0);assert.equal(resumed,1);assert.equal(gate.owner(),null);
 assert.equal((await manager.handle({action:'save',sessionId:started.sessionId},2)).error,'unknown_session');
 checks.push('typed manufacturer worker dispatch, actual Python cycle evidence, complete retention and release, owner-bound save');
 const saved=await manager.handle({action:'save',sessionId:started.sessionId},1);assert.equal(saved.saved,true,JSON.stringify(saved));
 const unitName=input.profileId.split(':')[1];
 assert.equal(path.dirname(saved.filePath),desktop);assert.ok(path.basename(saved.filePath).includes(unitName==='CAN_CAN_Gateway'?'Gateway':unitName));assert.match(saved.filePath,/\.pcapng$/);
 const bytes=await fs.readFile(saved.filePath),rows=[];
 for(let offset=0;offset<bytes.length;){const kind=bytes.readUInt32LE(offset),size=bytes.readUInt32LE(offset+4);
  assert.ok(size>=12&&size%4===0&&offset+size<=bytes.length);assert.equal(bytes.readUInt32LE(offset+size-4),size);
  if(kind===6){const captured=bytes.readUInt32LE(offset+20);const payload=bytes.subarray(offset+28,offset+28+captured);
   rows.push(JSON.parse(payload.subarray(12).toString('utf8')));}offset+=size;}
 assert.equal(rows.length,expected.length);for(const row of rows)assert.deepEqual(Object.keys(row).sort(),['capturedUtc','name','unit','value']);
 assert.deepEqual(rows.map(x=>x.name),expected.map(x=>x.name));
 assert.deepEqual(rows.map(x=>x.capturedUtc),expected.map(x=>x.processedUtc));
 if((input.ecuId??2)===2){const pressure=rows.find(row=>row.name.includes('PSM 制动压力'));assert.equal(pressure.unit,'kPa');assert.equal(pressure.value,-3000);}
 assert.ok(!bytes.includes(Buffer.from('5A9F4210'))&&!bytes.includes(Buffer.from('readContract'))&&!bytes.includes(Buffer.from('WP0ZZZ98ZES123456')));
 checks.push('Desktop PCAPNG contains every received named parameter value, unit and reception time; no identity, requests or source metadata');
 await fs.copyFile(saved.filePath,path.join(output,'SYNTHETIC-'+input.profileId.split(':')[1]+'.pcapng'));passed=true;
}finally{await manager.shutdown();await fs.rm(desktop,{recursive:true,force:true});
 await fs.writeFile(path.join(output,'result.json'),JSON.stringify({passed,checks,moduleRoot:root,pythonOutputReal:true,bytePortAndWorkerSynthetic:true,deviceIO:false,vehicleIO:false},null,2)+'\n');}
console.log('PASS manufacturer Python output → desktop stream → parameter-only PCAPNG');
