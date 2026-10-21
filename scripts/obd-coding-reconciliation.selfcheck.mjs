/** Synthetic full source-scoped originals; journal/reader DTOs are real code.
 * No protocol payload is generated and no adapter or vehicle is opened. */
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import {createHash} from 'node:crypto';
import {pathToFileURL} from 'node:url';
const repo=path.resolve(import.meta.dirname,'..'),root=path.resolve(process.env.PORSCHE981_ACCEPT_APP_ROOT||repo);
const moduleAt=name=>import(pathToFileURL(path.join(root,'apps/desktop/electron',name)));
const {createCodingOperationJournal,codingJournalHash:hash}=await moduleAt('coding-operation-journal.mjs');
const {normalizeSourceCodingBackup}=await moduleAt('source-coding-backup.mjs');
const {createCodingBackupStore}=await moduleAt('coding-backup-store.mjs');
const {codingBlockKey,normalizeStoredCodingBackup,reconcileCodingReadback}=await moduleAt('diagnostic-preparation.mjs');
const {createCodingWorkspace}=await moduleAt('coding-workspace.mjs');
const {SWSG_CODING_SOURCES}=await moduleAt('swsg-coding-sources.mjs');
const pid='9x1:SWSG:SWSG_Master_mit_AFS_ab_1310',source=SWSG_CODING_SOURCES[pid];
const sizes={ '04A1':137,'04A2':27,'04A3':57,'04A4':29,'04A5':17,'04A6':42,'04A7':11,'04A8':54,'04A9':33,'04AA':26,'04AB':61,'04B0':30,'04FE':3,'0600':3 };
const dataHash=hx=>hash(Buffer.from(hx,'hex'));
function original(change={},capturedUtc=new Date().toISOString()){
 return normalizeSourceCodingBackup({schemaVersion:3,kind:'ecu-coding-backup',identity:{generation:'981',vin:'WP0ZZZ98ZES123456',ecu:'headlamp-left',hardware:'MODEL-HW',software:'MODEL-SW'},
  profileId:pid,sourceHash:source.definitionSha256,scopeHash:source.scopeSha256,
  blocks:Object.entries(sizes).map(([identifierHex,n])=>{const dataHex=change[identifierHex]||'00'.repeat(n);return{identifierKind:'DID',identifierHex,dataHex,pduHex:'62'+identifierHex+dataHex,sha256:dataHash(dataHex)};}),
  capturedUtc,provenance:'source-vehicle-read',freshReadProven:true,completeness:'source-qualified-complete-record-set',liveVerified:false});
}
const first=original({},'2000-01-01T00:00:00Z');
const targets={'04A1':'01'+'00'.repeat(136),'04A2':'02'+'00'.repeat(26)};
const temp=await fs.mkdtemp(path.join(os.tmpdir(),'flat-six-coding-reconcile-')),checks=[];let contexts=0;
async function fixture(name,{metadataChange,phase='outcome-unknown',filesystem,beforeBackup=first}={}){
 const directory=path.join(temp,name),desktop=path.join(directory,'desktop');await fs.mkdir(desktop,{recursive:true});
 const store=createCodingBackupStore({directory,normalizeBackup:normalizeSourceCodingBackup,normalizeStoredBackup:normalizeStoredCodingBackup});
 const saved=await store.persist(beforeBackup),metadata={mode:'vehicle',ecu:first.identity.ecu,scopeKey:hash(JSON.stringify({identity:first.identity,profileId:pid})),backupId:saved.id,
  targets:first.blocks.map(b=>({key:codingBlockKey(b),sha256:dataHash(targets[b.identifierHex]||b.dataHex),changed:Object.hasOwn(targets,b.identifierHex)}))};
 metadataChange?.(metadata);
 const journal=createCodingOperationJournal({directory:path.join(directory,'operations'),...(filesystem?{filesystem}:{})});
 const state=await journal.begin(metadata),item=metadata.targets.find(t=>t.changed);
 await journal.append(state.operationId,'write-intent',{key:item.key,targetSha256:item.sha256});
 if(phase==='outcome-unknown')await journal.append(state.operationId,phase,{error:'synthetic lost reply; no transport'});
 return {directory,desktop,store,journal,id:state.operationId,metadata,lock:path.join(directory,'operations','scope-'+metadata.scopeKey+'.lock')};
}
async function observed(f,change={}){
 const s=await f.journal.inspect(f.id);return {read:original(change),checkpoint:{lastSha256:s.lastSha256,readStartedUtc:s.updatedUtc}};
}
async function reconcile(f,change={}){const x=await observed(f,change);return reconcileCodingReadback(f.journal,f.id,x.read,id=>f.store.load(id),x.checkpoint);}
async function lockOwner(f){const stat=await fs.lstat(f.lock);if(!stat.isDirectory())return fs.readFile(f.lock,'utf8');const names=await fs.readdir(f.lock);assert.equal(names.length,1);return fs.readFile(path.join(f.lock,names[0]),'utf8');}
async function unchanged(f,sequence){const s=await f.journal.inspect(f.id);assert.equal(s.sequence,sequence);assert.equal(s.requiresReconciliation,true);assert.equal(await lockOwner(f),f.id);}
try{
 for(const [name,change,matches] of [['targets',targets,14],['mixed',{'04A1':targets['04A1']},13],['different',{'04A1':'03'+'00'.repeat(136)},12]]){
  const f=await fixture(name),result=await reconcile(f,change);assert.equal(result.phase,'readback-reconciled');assert.equal(result.requiresReconciliation,false);
  assert.equal(result.reconciliation.blocks.filter(b=>b.matchesTarget).length,matches);assert.equal(result.verified.length,0);assert.equal(result.automaticWriteRetry,false);assert.equal(result.executionEnabled,false);
  assert.deepEqual(result.metadata.targets,f.metadata.targets);assert.deepEqual(result.intended,[f.metadata.targets[0].key]);
  await assert.rejects(fs.access(f.lock));await assert.rejects(f.journal.append(f.id,'write-intent',{key:f.metadata.targets[1].key,targetSha256:f.metadata.targets[1].sha256}),/replay-or-order/);
  const next=await f.journal.begin(f.metadata);assert.notEqual(next.operationId,f.id);assert.deepEqual(next.intended,[]);
  // A repeat of an old readback must never delete the next operation's lock.
  const checkpoint={lastSha256:result.lastSha256,readStartedUtc:new Date().toISOString()};
  await reconcileCodingReadback(f.journal,f.id,original(change),id=>f.store.load(id),checkpoint);
  assert.equal(await lockOwner(f),next.operationId);contexts++;
 }
 checks.push('target, mixed and other full observations close only the old outcome; preserve targets/intents, never claim write verification, and cannot replay or delete a later operation lock');
 const edits=[
  ['missing-checkpoint',x=>{x.checkpoint=null;}],
  ['stale-capture',x=>{x.read.capturedUtc='2000-01-01T00:00:00Z';}],
  ['read-start-before-operation',x=>{x.checkpoint.readStartedUtc='2000-01-01T00:00:00Z';}],
  ['wrong-car',x=>{x.read.identity.vin='WP0ZZZ98ZES654321';}],
  ['wrong-version',x=>{x.read.identity.software='OTHER';}],
  ['wrong-unit',x=>{x.read.identity.ecu='headlamp-right';}],
  ['source-changed',x=>{x.read.sourceHash='a'.repeat(64);}],
  ['scope-changed',x=>{x.read.scopeHash='b'.repeat(64);}],
  ['incomplete',x=>{x.read.blocks.pop();}],
  ['invalid-block',x=>{x.read.blocks[0].dataHex='FF'+x.read.blocks[0].dataHex.slice(2);}],
  ['imported',x=>{x.read.provenance='imported';}],
  ['unproven-read',x=>{x.read.freshReadProven=false;}],
 ];
 for(const [name,edit] of edits){const f=await fixture(name),before=await f.journal.inspect(f.id),x=await observed(f);edit(x);
  await assert.rejects(reconcileCodingReadback(f.journal,f.id,x.read,id=>f.store.load(id),x.checkpoint));await unchanged(f,before.sequence);contexts++;}
 checks.push('stale, foreign, changed-source, incomplete, corrupt, imported and unproven observations leave the unknown record and its lock unchanged');
 for(const [name,metadataChange] of [['missing-target',m=>m.targets.pop()],['wrong-order',m=>m.targets.reverse()],['wrong-change-flag',m=>{m.targets[1].changed=false;}]]){
  const f=await fixture(name,{metadataChange}),before=await f.journal.inspect(f.id);await assert.rejects(reconcile(f),/scope-invalid/);await unchanged(f,before.sequence);contexts++;
 }
 checks.push('complete observation must match the exact original target scope/order and original changed flags');
 {
  const f=await fixture('journal-reconcile-admission'),before=await f.journal.inspect(f.id);
  await assert.rejects(f.journal.append(f.id,'readback-reconciled',{}),/reconciliation-required/);
  await assert.rejects(f.journal.reconcile(f.id,{capturedUtc:new Date().toISOString(),blocks:[]},before.lastSha256),/reconciliation-invalid/);
  await assert.rejects(f.journal.reconcile(f.id,{},'f'.repeat(64)),/reconciliation-raced/);
  await unchanged(f,before.sequence);contexts++;
 }
 {
  const f=await fixture('simulation-cannot-reconcile-as-vehicle',{metadataChange:m=>{m.mode='simulation';}}),before=await f.journal.inspect(f.id);
  await assert.rejects(reconcile(f),/reconciliation-unavailable/);assert.equal((await f.journal.inspect(f.id)).sequence,before.sequence);contexts++;
 }
 {
  const f=await fixture('broken-backup'),before=await f.journal.inspect(f.id),file=path.join(f.directory,f.metadata.backupId+'.json');await fs.writeFile(file,'{}');
  await assert.rejects(reconcile(f));await unchanged(f,before.sequence);contexts++;
 }
 {
  const f=await fixture('backup-after-operation',{beforeBackup:original({},new Date(Date.now()+86400000).toISOString())}),before=await f.journal.inspect(f.id);
  await assert.rejects(reconcile(f),/backup-invalid/);await unchanged(f,before.sequence);contexts++;
 }
 {
  const f=await fixture('state-changed-during-read',{phase:'write-intent'}),x=await observed(f);
  await f.journal.append(f.id,'outcome-unknown',{error:'synthetic changed checkpoint'});const before=await f.journal.inspect(f.id);
  await assert.rejects(reconcileCodingReadback(f.journal,f.id,x.read,id=>f.store.load(id),x.checkpoint),/read-stale/);await unchanged(f,before.sequence);contexts++;
 }
 {
  const f=await fixture('state-changed-during-backup-check',{phase:'write-intent'}),x=await observed(f);
  await assert.rejects(reconcileCodingReadback(f.journal,f.id,x.read,async id=>{await f.journal.append(f.id,'outcome-unknown',{error:'synthetic append race'});return f.store.load(id);},x.checkpoint),/raced/);
  const s=await f.journal.inspect(f.id);assert.equal(s.phase,'outcome-unknown');assert.equal(await lockOwner(f),f.id);contexts++;
 }
 checks.push('broken persisted originals and journal changes across the fresh read or backup check cannot close a pending operation');
 {
  let failed=false;const filesystem=new Proxy(fs,{get(target,key){if(key==='link')return async(a,b)=>{if(!failed&&String(b).endsWith('000003.json')){failed=true;throw new Error('synthetic publish failure');}return target.link(a,b);};return target[key];}});
  const f=await fixture('publish-failure',{filesystem}),before=await f.journal.inspect(f.id);await assert.rejects(reconcile(f),/publish failure/);await unchanged(f,before.sequence);await reconcile(f);contexts++;
 }
 {
  let failed=false;const filesystem=new Proxy(fs,{get(target,key){if(key==='rmdir')return async p=>{if(!failed&&String(p).endsWith('.lock')){failed=true;throw new Error('synthetic lock removal failure');}return target.rmdir(p);};return target[key];}});
  const f=await fixture('committed-lock-cleanup',{filesystem});await assert.rejects(reconcile(f),/lock removal failure/);
  const committed=await f.journal.inspect(f.id);assert.equal(committed.phase,'readback-reconciled');assert.equal(committed.requiresLockCleanup,true);assert.deepEqual(await fs.readdir(f.lock),[]);
  await reconcile(f);assert.equal((await f.journal.inspect(f.id)).sequence,committed.sequence);await assert.rejects(fs.access(f.lock));contexts++;
 }
 checks.push('failed event publication retains the lock; committed readback can finish interrupted lock cleanup without a duplicate event');
 {
  const f=await fixture('legacy-file-lock');await fs.unlink(path.join(f.lock,f.id+'.owner'));await fs.rmdir(f.lock);await fs.writeFile(f.lock,f.id);
  const done=await reconcile(f);assert.equal(done.phase,'readback-reconciled');await assert.rejects(fs.access(f.lock));contexts++;
 }
 for(const legacy of [false,true]){
  const f=await fixture('cleanup-handoff-'+legacy),done=await reconcile(f);if(legacy)await fs.writeFile(f.lock,f.id);else{await fs.mkdir(f.lock);await fs.writeFile(path.join(f.lock,f.id+'.owner'),f.id);}
  const delayedPath=legacy?f.lock:path.join(f.lock,f.id+'.owner');let entered,resume,paused=false;const ready=new Promise(r=>{entered=r;}),wait=new Promise(r=>{resume=r;});
  const slowFs=new Proxy(fs,{get(target,key){if(key==='unlink')return async p=>{if(!paused&&String(p)===delayedPath){paused=true;entered();await wait;}return target.unlink(p);};return target[key];}});
  const slow=createCodingOperationJournal({directory:path.join(f.directory,'operations'),filesystem:slowFs});
  const delayed=slow.reconcile(f.id,{capturedUtc:done.reconciliation.capturedUtc,blocks:done.reconciliation.blocks.map(({key,sha256})=>({key,sha256}))},done.lastSha256);
  let watchdog;try{await Promise.race([ready,new Promise((_,reject)=>{watchdog=setTimeout(()=>reject(new Error('cleanup test did not reach its owned pause')),3000);})]);
   await f.journal.reconcile(f.id,{},done.lastSha256);const next=await f.journal.begin(f.metadata);resume();await delayed;
   assert.equal(await lockOwner(f),next.operationId);await f.journal.append(next.operationId,'write-intent',{key:f.metadata.targets[0].key,targetSha256:f.metadata.targets[0].sha256});assert.equal(await lockOwner(f),next.operationId);
  }finally{clearTimeout(watchdog);resume();await delayed.catch(()=>{});}contexts++;
 }
 checks.push('legacy file locks remain readable; delayed cleanup of either legacy or current ownership cannot remove a newer operation owner');
 {
  const f=await fixture('workspace-integration');let reads=0;
  const client=async()=>{reads++;const current=original({'04A1':targets['04A1']});return{ok:true,releaseVerified:true,identity:current.identity,result:{vehicleGeneration:'981',readContract:{},codingRead:{
   completeOriginalRead:true,completeVehicleCodingScopeQualified:true,ecuId:47,generation:'981',profileId:pid,definitionSha256:source.definitionSha256,completeScopeSource:{sha256:source.scopeSha256},
   blocks:current.blocks.map(b=>({...b,receivedUtc:current.capturedUtc})),fields:[]}}};};
  const workspace=createCodingWorkspace({directory:f.directory,desktopDirectory:async()=>f.desktop,readClient:client});
  const beforeFiles=(await fs.readdir(f.directory)).filter(n=>/^[a-f0-9]{64}\.json$/.test(n));
  const result=await workspace.handle({action:'read',ecu:'headlamp-left'},1);assert.equal(result.ok,true,JSON.stringify(result));assert.equal(result.snapshot.canWrite,false);assert.equal(result.snapshot.canRestore,false);
  assert.equal((await f.journal.inspect(f.id)).phase,'readback-reconciled');assert.deepEqual((await fs.readdir(f.directory)).filter(n=>/^[a-f0-9]{64}\.json$/.test(n)),beforeFiles);
  const files=await fs.readdir(f.desktop);assert.equal(files.length,1);const desktopBefore=await fs.readFile(path.join(f.desktop,files[0]));
  assert.equal((await workspace.handle({action:'backup',ecu:'headlamp-left'},1)).ok,true);assert.deepEqual(await fs.readFile(path.join(f.desktop,files[0])),desktopBefore);
  assert.equal((await workspace.handle({action:'write',ecu:'headlamp-left',reviewId:'not-executable'},1)).error,'coding-client-unavailable');
  assert.equal(reads,2);contexts++;
 }
 checks.push('actual source coding workspace uses an owned fresh full read to reconcile, preserves the first desktop baseline, saves no new full read, and keeps write/restore disabled');
 {
  const f=await fixture('workspace-reconciliation-race',{phase:'write-intent'});
  const workspace=createCodingWorkspace({directory:f.directory,desktopDirectory:async()=>f.desktop,readClient:async()=>{
   await f.journal.append(f.id,'outcome-unknown',{error:'synthetic concurrent change during read'});const current=original();return{ok:true,releaseVerified:true,identity:current.identity,result:{vehicleGeneration:'981',readContract:{},codingRead:{completeOriginalRead:true,completeVehicleCodingScopeQualified:true,ecuId:47,generation:'981',profileId:pid,definitionSha256:source.definitionSha256,completeScopeSource:{sha256:source.scopeSha256},blocks:current.blocks.map(b=>({...b,receivedUtc:current.capturedUtc})),fields:[]}}};}});
  const result=await workspace.handle({action:'read',ecu:'headlamp-left'},1);assert.equal(result.error,'coding-reconciliation-failed');assert.equal((await f.journal.inspect(f.id)).requiresReconciliation,true);assert.deepEqual(await fs.readdir(f.desktop),[]);assert.equal(await lockOwner(f),f.id);contexts++;
 }
 checks.push('a workspace read whose operation checkpoint changed reports unresolved reconciliation, preserves ownership and publishes no desktop backup');
 const output=path.resolve(process.env.PORSCHE981_ACCEPT_OUTPUT||path.join(repo,'.local/obd-coding-reconciliation-20261020/source'));
 await fs.mkdir(output,{recursive:true});const evidence={passed:true,contexts,checks,sourceOnlySyntheticBytes:true,deviceIO:false,vehicleIO:false,writeEnabled:false,moduleRoot:root};
 await fs.writeFile(path.join(output,'result.json'),JSON.stringify(evidence,null,2)+'\n');console.log('PASS coding readback reconciliation: '+contexts+' contexts, '+checks.length+' checks; no device I/O');
}finally{await fs.rm(temp,{recursive:true,force:true});}
