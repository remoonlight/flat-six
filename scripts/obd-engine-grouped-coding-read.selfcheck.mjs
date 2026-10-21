/** Actual source worker results and controller; byte/native IO modeled only. */
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import path from 'node:path';
import {pathToFileURL} from 'node:url';
import {createHash} from 'node:crypto';
import {EventEmitter} from 'node:events';
import {PassThrough} from 'node:stream';
const host=path.resolve(import.meta.dirname,'..'),root=path.resolve(process.env.PORSCHE981_ACCEPT_APP_ROOT||host);
const at=name=>import(pathToFileURL(path.join(root,'apps/desktop/electron',name)));
const {validateCompleteCodingRead}=await at('coding-read-evidence.mjs');
const {normalizeSourceCodingBackup}=await at('source-coding-backup.mjs');
const {createCodingWorkspace}=await at('coding-workspace.mjs');
const {createManufacturerStream}=await at('manufacturer-stream.mjs');
const {createTransportGate}=await at('obd-connection.mjs');
const {UDS_CODING_SOURCES}=await at('uds-coding-sources.mjs');
const task=path.join(host,'.local/obd-engine-grouped-coding-20261018'),side=process.env.PORSCHE981_ACCEPT_APP_ROOT?'installed':'source';
const fixtureSide=process.env.PORSCHE981_CODING_FIXTURE_SIDE||side;assert.ok(['source','installed'].includes(fixtureSide));
if(side==='installed'&&fixtureSide==='source'){
 const proof=JSON.parse(await fs.readFile(path.join(task,'source-models/result.json'),'utf8'));assert.equal(proof.passed,true);assert.equal(proof.runs.length,40);
 for(const [name,hash] of Object.entries(proof.sourceCode))assert.equal(createHash('sha256').update(await fs.readFile(path.join(root,name))).digest('hex'),hash,name);
}
const modelDir=path.join(task,fixtureSide+'-models/models'),files=(await fs.readdir(modelDir)).filter(n=>/^engine-\d+-982-1-(elm|vnci)\.json$/.test(n)).sort();assert.equal(files.length,40);
const load=async n=>JSON.parse(await fs.readFile(path.join(modelDir,n),'utf8'));
const seenOf=m=>{const map=new Map();for(const p of m.pdus){if(p.duringCleanup)continue;const a=map.get(p.requestHex)||[];a.push(p.pduHex);map.set(p.requestHex,a);}return map;};
const sha=b=>createHash('sha256').update(b).digest('hex');let checks=0;const representatives=new Map();
for(const file of files){
 const m=await load(file);assert.equal(m.syntheticPort,true);const doc=m.result,seen=seenOf(m),source=UDS_CODING_SOURCES[doc.codingRead.profileId];
 const id=validateCompleteCodingRead(doc,seen);assert.equal(id.generation,'982');assert.ok(doc.identityInfo.length>doc.readContract.identityRequests.length);checks+=2;
 const backup={schemaVersion:3,kind:'ecu-coding-backup',identity:{...id,ecu:'dme'},profileId:doc.codingRead.profileId,sourceHash:doc.codingRead.definitionSha256,scopeHash:doc.codingRead.completeScopeSource.sha256,blocks:doc.codingRead.blocks,capturedUtc:doc.codingRead.blocks.at(-1).receivedUtc,provenance:'source-vehicle-read',freshReadProven:true,completeness:'source-qualified-complete-record-set',liveVerified:false};
 assert.equal(normalizeSourceCodingBackup(backup).blocks.length,doc.codingRead.blocks.length);assert.throws(()=>normalizeSourceCodingBackup({...backup,identity:{...backup.identity,generation:'981'}}));checks+=2;
 const group=doc.readContract.identityRequests.findIndex(g=>g.records?.length>1),hw=source.hardwareIdentity?.requestHex||'22F1A3',sw=source.softwareIdentity?.requestHex||'22F189';
 for(const mutate of [d=>d.sourceRouteDiscovery.uniqueMatch=false,d=>d.sourceRouteDiscovery.applicationProtocol='kwp',d=>d.sourceRouteDiscovery.probes.reverse(),d=>d.sourceRouteDiscovery.probes[0].ok=true,d=>d.identityInfo.pop(),d=>d.identityInfo.find(r=>r.recordIndex===1).recordIndex=0,
  d=>d.readContract.identityRequests[group].records[1].byteOffset++,d=>d.readContract.identityRequests[group].records[1].at++,
  d=>d.readContract.identityRequests[group].records.reverse(),d=>d.readContract.identityRequests[group].records[1].formula.text='IDENTICAL:other',
  d=>d.identityInfo.find(r=>r.requestHex===hw).value='other',d=>d.identityInfo.find(r=>r.requestHex===sw).value='other',
  d=>d.codingRead.blocks.pop(),d=>d.codingRead.blocks[0].dataHex+='00',d=>d.codingRead.definitionSha256='0'.repeat(64),
  d=>d.codingRead.completeOriginalRead=false,d=>d.codingRead.codingWriteQualified=true,d=>d.codingRead.fields[0].editable=true,
  d=>d.readContract.versionSelectors[0].selector.formulaId=0,d=>d.readContract.sourceCommands=[],d=>d.readContract.identityConstraints={},
  d=>d.readContract.txId=0x710,d=>d.readContract.rxId=0x77A,d=>d.codingRead.ecuId=9,
  d=>{d.codingRead.generation=d.readContract.generation=d.vehicleGeneration=d.transport.qualification.generation='981';}]){
   const bad=structuredClone(doc);mutate(bad);assert.throws(()=>validateCompleteCodingRead(bad,seen),undefined,file);checks++;
 }
 const absent=structuredClone(seen);absent.delete(hw);assert.throws(()=>validateCompleteCodingRead(doc,absent));checks++;
 const noGroup=structuredClone(doc);noGroup.identityInfo=noGroup.identityInfo.filter(r=>r.recordIndex===0);assert.throws(()=>validateCompleteCodingRead(noGroup,seen));checks++;
 if(source.factoryOnlyReadRequests){const bad=structuredClone(backup);bad.blocks=bad.blocks.filter(b=>b.requestHex!=='2204FC');assert.throws(()=>normalizeSourceCodingBackup(bad));checks++;}
 if(source.softwareIdentity){assert.equal(id.software,doc.identityInfo.find(r=>r.requestHex==='22F197').value);assert.ok(id.software.length>=13&&id.software.length<=15);checks+=2;}
 if(!representatives.has(source.module))representatives.set(source.module,{m,identity:id,backup});
}
for(const {m,identity} of representatives.values()){
 const temp=await fs.mkdtemp(path.join(task,'engine-store-')),desktop=path.join(temp,'Desktop');await fs.mkdir(desktop);let fail=true;
 const workspace=createCodingWorkspace({directory:path.join(temp,'store'),desktopDirectory:()=>fail?desktop+'/missing':desktop,readClient:async()=>({ok:true,result:structuredClone(m.result),identity,releaseVerified:true})});
 assert.equal((await workspace.handle({action:'read',ecu:'dme'},1)).error,'coding-backup-failed');fail=false;
 const saved=await workspace.handle({action:'backup',ecu:'dme'},1);assert.equal(saved.saved,true);assert.equal(saved.snapshot,undefined);const bytes=await fs.readFile(saved.filePath),names=await fs.readdir(desktop);
 const read=await workspace.handle({action:'read',ecu:'dme'},1);assert.equal(read.ok,true);assert.equal(read.snapshot.fields.length,m.result.codingRead.definitionFieldCount);assert.equal(read.snapshot.canWrite,false);assert.equal(read.snapshot.canRestore,false);assert.deepEqual(await fs.readFile(saved.filePath),bytes);assert.deepEqual(await fs.readdir(desktop),names);checks+=9;
 for(const native of [false,true]){
  const filename=files.find(n=>n.includes(m.result.codingRead.profileId.includes(':BV_ECM_UDS:')?'engine-00-':'engine-09-')&&n.endsWith(native?'-vnci.json':'-elm.json'));
  const fixture=await load(filename);const vehicle=JSON.parse(await fs.readFile(path.join(host,'.local/obd-ecm-uds-reader-20261016/vehicle-source-982.json')));
  const gate=createTransportGate();let calls=0,resumes=0;
  const manager=createManufacturerStream({repoRoot:root,env:{},allowInjectedLive:true,gate,
   connection:{snapshot:()=>({purpose:'diagnostic',model:native?'VNCI':'ELM327',serialBaud:null}),selectedId:()=>native?'vnci:10001':'bt:001122334455',reserveDispatch:()=>true,releaseDispatch(){},suspendForSession:async()=>({ok:true}),resumeAfterSession:async proof=>{assert.equal(proof.nativeReleaseVerified,true);resumes++;return{ok:true};}},
   spawnFn(){const child=new EventEmitter();child.stdin=new PassThrough();child.stdout=new PassThrough();child.stderr=new PassThrough();child.kill=()=>{};child.stdin.once('data',raw=>{const req=JSON.parse(raw),coding=++calls%2===0;assert.equal(req.action,coding?'manufacturer-read-coding':'manufacturer-identify-vehicle');
    if(coding){assert.equal(req.ecuId,1);assert.equal(req.sourceBound,true);assert.equal(req.generation,'982');assert.equal(req.completeOriginalRequired,true);assert.equal(req.expectedVehicleIdentitySha256,fixture.result.vehicleIdentitySha256);}
    setImmediate(()=>{child.emit('spawn');const frames=coding?fixture.pdus.map(pdu=>({event:'diagnostic-pdu',result:{executionEnabled:true,liveVerified:false,writePayload:null,simulation:false,sourceKind:'independent-manufacturer-read',pdu}})).concat({event:'finished',result:structuredClone(fixture.result)}):vehicle.frames;for(const f of frames)child.stdout.write(JSON.stringify(f)+'\n');setImmediate(()=>child.emit('close',0));});});return child;}});
  const read=await manager.readCoding(1,1);assert.equal(read.ok,true,JSON.stringify(read));assert.equal(read.identity.generation,'982');assert.equal(gate.owner(),null);assert.equal(manager.isIdle(),true);assert.equal(resumes,2);await manager.shutdown();checks+=5;
 }
}
const code=await Promise.all(['uds-coding-sources.mjs','coding-read-evidence.mjs','source-coding-backup.mjs','coding-workspace.mjs','manufacturer-stream.mjs'].map(async name=>({name,sha256:sha(await fs.readFile(path.join(root,'apps/desktop/electron',name)))})));
const report={passed:true,models:files.length,checks,code,fixtureSide,sourceFixtureReuse:side==='installed'&&fixtureSide==='source',deviceIO:false,vehicleIO:false};await fs.writeFile(path.join(task,(side==='installed'?'installed-':'')+'desktop-result.json'),JSON.stringify(report,null,2)+'\n');console.log('PASS',files.length,'grouped engine models /',checks,'interface checks; modeled IO only');
