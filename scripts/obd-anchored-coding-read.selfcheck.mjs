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
const {validateCompleteCodingRead,validateCodingVehicleAnchor}=await moduleAt('coding-read-evidence.mjs');
const {UDS_CODING_SOURCES,matchesUdsCodingVersion}=await moduleAt('uds-coding-sources.mjs');
const {createCodingWorkspace}=await moduleAt('coding-workspace.mjs');
const {createManufacturerStream}=await moduleAt('manufacturer-stream.mjs');
const {createTransportGate}=await moduleAt('obd-connection.mjs');
const {normalizeCodingBackup}=await moduleAt('diagnostic-preparation.mjs');
const {normalizeSourceCodingBackup}=await moduleAt('source-coding-backup.mjs');
const {acceptCodingSnapshot}=await import(pathToFileURL(path.join(host,'apps/desktop/src/obd/coding-workspace-state.mjs')));
const task=path.resolve(process.env.PORSCHE981_CODING_MODELS_ROOT||path.join(host,'.local/obd-anchored-coding-source-20261018'));
const dir=path.join(task,process.env.PORSCHE981_ACCEPT_APP_ROOT?'installed-models':'source-models');
const files=(await fs.readdir(path.join(dir,'models'))).filter(n=>/^anchored-\d+-\d+-(81|165)-(elm|vnci)\.json$/.test(n)).sort();
assert.equal(files.length,8);
const load=async n=>JSON.parse(await fs.readFile(path.join(dir,'models',n),'utf8'));
const observedFrames=frames=>{const map=new Map();for(const f of frames){const p=f.result?.pdu;if(f.event!=='diagnostic-pdu'||p.duringCleanup)continue;const a=map.get(p.requestHex)||[];a.push(p.pduHex);map.set(p.requestHex,a);}return map;};
const ledger=m=>{const i=m.frames.findIndex(f=>f.event==='coding-vehicle-anchor');assert.ok(i>0);return {anchorDoc:m.frames[i].result,anchorObserved:observedFrames(m.frames.slice(0,i)),targetObserved:observedFrames(m.frames.slice(i+1))};};
const observed=m=>ledger(m).targetObserved;
const tokenFor=m=>{const v=ledger(m);return validateCodingVehicleAnchor(v.anchorDoc,v.anchorObserved,m.result.vehicleGeneration,m.result.vehicleIdentitySha256);};
const qualified=m=>validateCompleteCodingRead(m.result,observed(m),tokenFor(m));
const sha=b=>createHash('sha256').update(b).digest('hex');
const ecuName=id=>({81:'reverse-camera',165:'source-紧急呼叫系统 (ERA Glonass)'}[id]);
const groupName=id=>({"81": "倒车摄像头", "165": "紧急呼叫系统 （ERA Glonass）"}[id]);
const selectorFixture=JSON.parse(await fs.readFile(path.join(task,'selector-cases.json'),'utf8'));
assert.equal(selectorFixture.scopesSha256,sha(await fs.readFile(path.join(root,'scripts/diagnostics/uds-coding-scopes.json'))));
assert.equal(selectorFixture.gatewayIdentitiesSha256,sha(await fs.readFile(path.join(root,'apps/desktop/electron/gateway-identity-sources.json'))));
const selectorProfiles=new Set();
for(const c of selectorFixture.cases){
 const source=UDS_CODING_SOURCES[c.profileId]||selectorFixture.additionalProfiles.find(p=>p.profileId===c.profileId),contract=structuredClone(source),seen=new Map(Object.entries(c.pdus).map(([h,p])=>[h,[p]]));
 if(c.duplicate)seen.set(c.duplicate,[c.pdus[c.duplicate],c.pdus[c.duplicate]]);
 if(c.changedContract)contract.versionSelectors[0].selector.byteOffsetSigned++;
 assert.equal(matchesUdsCodingVersion(source,contract,seen),c.expected,c.profileId+' '+c.rule+' '+c.label);selectorProfiles.add(c.profileId);
}
assert.equal(selectorProfiles.size,18);
await fs.writeFile(path.join(task,process.env.PORSCHE981_ACCEPT_APP_ROOT?'installed-selector-node-result.json':'selector-node-result.json'),JSON.stringify({passed:true,profiles:18,cases:selectorFixture.cases.length,fixtureSha256:sha(await fs.readFile(path.join(task,'selector-cases.json'))),codeSha256:sha(await fs.readFile(path.join(root,'apps/desktop/electron/uds-coding-sources.mjs'))),deviceIO:false,vehicleIO:false},null,2)+'\n');
let checks=0;
for(const file of files){
 const m=await load(file);assert.equal(m.syntheticPort,true);const doc=m.result,seen=observed(m),ecu=ecuName(doc.codingRead.ecuId),token=tokenFor(m);
 const identity=validateCompleteCodingRead(doc,seen,token);assert.equal(identity.generation,doc.vehicleGeneration);checks++;
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
  d=>d.identityInfo.find(r=>r.requestHex==='22F1A3').value='other',d=>d.identityInfo.find(r=>r.requestHex==='22F189').value='other',
  d=>d.codingRead.fields[0].editable=true,d=>d.readContract.sourceCommands=[],d=>d.readContract.identityRequests[0].record.at++,d=>d.readContract.session={},d=>d.readContract.versionSelectors[0].selector.formulaId=0]){
  const bad=structuredClone(doc);mutate(bad);assert.throws(()=>validateCompleteCodingRead(bad,seen,token),undefined,file);checks++;
 }
 const missing=structuredClone(seen);missing.delete('22F1A3');assert.throws(()=>validateCompleteCodingRead(doc,missing,token));checks++;
 const changed=structuredClone(seen);const versionRequest=doc.codingRead.ecuId===64?'22F1A2':'22F189';changed.get(versionRequest)[0]='62'+versionRequest.slice(2)+'585858585858';assert.throws(()=>validateCompleteCodingRead(doc,changed,token));checks++;
 const {anchorDoc,anchorObserved}=ledger(m);
 assert.throws(()=>validateCompleteCodingRead(doc,seen));checks++;
 assert.throws(()=>validateCompleteCodingRead(doc,seen,{...token}));checks++;
 for(const mutate of [d=>d.ok=false,d=>d.identityOnly=false,d=>d.executionEnabled=false,d=>d.simulation=true,
  d=>d.transport.closed=false,d=>d.transport.closeErrors.push('failed'),d=>d.transport.qualification.freshRead=false,
  d=>d.transport.qualification.ecuId=81,d=>d.readContract.txId=0x711,d=>d.readContract.rxId=0x77B,
  d=>d.readContract.identityRequests[0].record.at++,d=>d.readContract.sourceCommands=[],d=>d.vehicleIdentitySha256='0'.repeat(64),
  d=>d.identifiedProfileId='9x1:CAN_CAN_Gateway:CAN_CAN_Gateway_BaseSys',d=>d.identityInfo.pop()]){
  const bad=structuredClone(anchorDoc);mutate(bad);assert.throws(()=>validateCodingVehicleAnchor(bad,anchorObserved,doc.vehicleGeneration,doc.vehicleIdentitySha256));checks++;
 }
 for(const hx of ['22F190','22F1A3','22F1A2']){
  const twice=structuredClone(anchorObserved);twice.get(hx).push(twice.get(hx)[0]);assert.throws(()=>validateCodingVehicleAnchor(anchorDoc,twice,doc.vehicleGeneration,doc.vehicleIdentitySha256));checks++;
 }
 const mixed=structuredClone(seen);mixed.get('22F1A3').push(anchorObserved.get('22F1A3')[0]);assert.throws(()=>validateCompleteCodingRead(doc,mixed,token));checks++;
 const invented=structuredClone(seen);invented.set('22F190',anchorObserved.get('22F190'));assert.throws(()=>validateCompleteCodingRead(doc,invented,token));checks++;
 const wrongScope=structuredClone(doc);wrongScope.vehicleAnchor.profileId='other';assert.throws(()=>validateCompleteCodingRead(wrongScope,seen,token));checks++;
 const vinChanged=structuredClone(anchorObserved);vinChanged.set('22F190',['62F190'+Buffer.from('WP0ZZZ98ZES654321').toString('hex').toUpperCase()]);assert.throws(()=>validateCodingVehicleAnchor(anchorDoc,vinChanged,doc.vehicleGeneration,doc.vehicleIdentitySha256));checks++;
 if(doc.vehicleGeneration==='981')assert.ok(anchorDoc.identifiedProfileId.endsWith('Gateway_A7_1'));
 assert.equal(identity.hardware,'U01');assert.equal(identity.software,'U123');checks+=2;
}
for(const id of [81,165]){
 const model=await load(files.find(n=>n.includes(`-981-${id}-elm`))),identity=qualified(model),ecu=ecuName(id);
 const temp=await fs.mkdtemp(path.join(task,'anchored-store-')),desktop=path.join(temp,'Desktop');await fs.mkdir(desktop);
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
 for(const native of [false,true]){for(const fault of [null,'omit-boundary','duplicate-boundary','late-boundary']){
  const fixture=await load(files.find(n=>n.includes(`-981-${id}-`)&&n.endsWith(native?'-vnci.json':'-elm.json')));
  const vehicle=JSON.parse(await fs.readFile(path.join(host,'.local/obd-ecm-uds-reader-20261016/vehicle-source-981.json')));
  const gate=createTransportGate();let calls=0,resumes=0,unsupportedNext=false;
  const manager=createManufacturerStream({repoRoot:root,env:{},allowInjectedLive:true,gate,
   connection:{snapshot:()=>({purpose:'diagnostic',model:native?'VNCI':'ELM327',serialBaud:null}),selectedId:()=>native?'vnci:10001':'bt:001122334455',reserveDispatch:()=>true,releaseDispatch(){},suspendForSession:async()=>({ok:true}),resumeAfterSession:async proof=>{assert.equal(proof.nativeReleaseVerified,!(fault&&calls%2===0));resumes++;return{ok:true};}},
   spawnFn(){const child=new EventEmitter();child.stdin=new PassThrough();child.stdout=new PassThrough();child.stderr=new PassThrough();child.kill=()=>{};
    child.stdin.once('data',raw=>{const request=JSON.parse(raw),coding=++calls%2===0;assert.equal(request.action,coding?'manufacturer-read-coding':'manufacturer-identify-vehicle');
     if(coding){assert.equal(request.ecuId,id);assert.equal(request.sourceBound,undefined);assert.equal(request.completeOriginalRequired,true);assert.equal(request.expectedVehicleIdentitySha256,fixture.result.vehicleIdentitySha256);}
     setImmediate(()=>{child.emit('spawn');let frames,code=0;
      if(!coding)frames=vehicle.frames;else{const result=structuredClone(fixture.result);delete result.transport.rawLog;
       if(unsupportedNext){result.ok=false;result.error='coding-complete-scope-unqualified';result.codingRead=null;code=1;}
       frames=unsupportedNext?[{event:'finished',result}]:structuredClone(fixture.frames);frames.at(-1).result=result;
       if(fault){const boundary=frames.findIndex(f=>f.event==='coding-vehicle-anchor'),frame=frames[boundary];
        if(fault==='omit-boundary')frames.splice(boundary,1);
        else if(fault==='duplicate-boundary')frames.splice(boundary,0,structuredClone(frame));
        else{frames.splice(boundary,1);frames.splice(frames.length-1,0,frame);}}}
      for(const frame of frames)child.stdout.write(JSON.stringify(frame)+'\n');setImmediate(()=>child.emit('close',code));});});return child;}});
  assert.equal((await manager.handle({action:'read-coding',ecuId:id},1)).ok,false);
  const result=await manager.readCoding(id,1);
  if(fault){assert.equal(result.ok,false);assert.equal(result.identity,undefined);assert.equal(gate.owner(),null);assert.equal(manager.isIdle(),true);assert.equal(resumes,2);await manager.shutdown();checks+=5;continue;}
  assert.equal(result.ok,true,JSON.stringify(result));assert.equal(result.identity.vin,identity.vin);assert.equal(gate.owner(),null);assert.equal(manager.isIdle(),true);
  unsupportedNext=true;assert.equal((await manager.readCoding(id,1)).error,'coding-complete-scope-unqualified');assert.equal(resumes,4);assert.equal(manager.isIdle(),true);assert.equal(gate.owner(),null);await manager.shutdown();checks+=12;
 }}
}
assert.ok(checks>=400);
const code=await Promise.all(['uds-coding-sources.mjs','coding-read-evidence.mjs','source-coding-backup.mjs','coding-workspace.mjs','manufacturer-stream.mjs','gateway-identity-sources.json'].map(async name=>({name,sha256:sha(await fs.readFile(path.join(root,'apps/desktop/electron',name)))})));
await fs.writeFile(path.join(path.resolve(process.env.PORSCHE981_CODING_CHECK_OUTPUT||task),process.env.PORSCHE981_ACCEPT_APP_ROOT?'installed-desktop-result.json':'desktop-result.json'),JSON.stringify({passed:true,models:files.length,checks,code,deviceIO:false,vehicleIO:false},null,2)+'\n');
console.log('PASS8 anchored models; private gateway/source/backup/interface checks passed');
