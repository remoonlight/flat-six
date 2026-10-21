/** Real controller, immutable store and Python field decoder; modeled vehicle reads only. */
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import path from 'node:path';
import {createHash} from 'node:crypto';
import {createCodingWorkspace} from '../apps/desktop/electron/coding-workspace.mjs';
import {runCodingDraft} from '../apps/desktop/electron/offline-diagnostics.mjs';
import {validateCompleteCodingRead} from '../apps/desktop/electron/coding-read-evidence.mjs';
import {acceptCodingRead,acceptCodingReview,beginCodingRestore,createCodingDraft,editCodingChoice} from '../apps/desktop/src/obd/coding-workspace-state.mjs';

const root=path.resolve(import.meta.dirname,'..');
const output=path.resolve(process.env.PORSCHE981_ACCEPT_OUTPUT||'.local/obd-coding-restore-preview-20261019');
await fs.mkdir(output,{recursive:true});
const temp=await fs.mkdtemp(path.join(output,'isolated-')),desktop=path.join(temp,'Desktop'),directory=path.join(temp,'store');
await fs.mkdir(desktop);
const file=path.join(root,'.local/obd-coding-desktop-20261018/source-models/models/coding-00-981-47-elm.json');
const model=JSON.parse(await fs.readFile(file,'utf8'));assert.equal(model.syntheticPort,true);
const observed=new Map();for(const pdu of model.pdus)observed.set(pdu.requestHex,[...(observed.get(pdu.requestHex)||[]),pdu.pduHex]);
const identity=validateCompleteCodingRead(model.result,observed);
const env={...process.env,PORSCHE981_DEFINITION_ROOT:path.resolve(process.env.PORSCHE981_DRAFT_DEFINITION_ROOT||'.local/obd-coding-choice-gaps-20261019/library')};
let current=structuredClone(model.result),reads=0,context,drift=false,hold=false,release,workerCalls=0;
const worker=async input=>{workerCalls++;return runCodingDraft(input,{repoRoot:root,env});};
const workspace=createCodingWorkspace({directory,desktopDirectory:()=>desktop,
 readClient:async()=>{reads++;return {ok:true,result:structuredClone(current),identity,releaseVerified:true};},
 draftClient:async(input,signal)=>{context=structuredClone(input.context);
  if(hold)await new Promise(resolve=>{release=resolve;signal.addEventListener('abort',resolve,{once:true});});
  const result=await worker(input);return drift?{...result,scopeHash:'0'.repeat(64)}:result;}});
const checks=[],request=(readId,extra={})=>({action:'preview-restore',ecu:'headlamp-left',readId,...extra});
let passed=false;
const sha=data=>createHash('sha256').update(data).digest('hex');
const snapshotFiles=async folder=>Object.fromEntries(await Promise.all((await fs.readdir(folder)).map(async name=>[name,sha(await fs.readFile(path.join(folder,name)))])));
async function installDraft(draft,sourceContext){
 assert.equal(draft.ok,true,JSON.stringify(draft));
 for(const block of current.codingRead.blocks){
  const requestHex=(block.identifierKind==='LID'?'21':'22')+block.identifierHex;
  const changed=draft.draft.blocks.find(b=>b.requestHex===requestHex);assert(changed);
  block.dataHex=changed.afterHex;block.sha256=sha(Buffer.from(block.dataHex,'hex'));
  block.pduHex=(block.identifierKind==='LID'?'61':'62')+block.identifierHex+block.dataHex;
  block.receivedUtc=new Date(Date.parse(block.receivedUtc)+1000).toISOString();
 }
 const changedContext={...sourceContext,blocks:draft.draft.blocks.map(b=>({requestHex:b.requestHex,dataHex:b.afterHex}))};
 const decoded=await worker({context:changedContext,edits:[]});assert.equal(decoded.ok,true,JSON.stringify(decoded));
 current.codingRead.fields=model.result.codingRead.fields.map(f=>({...f,currentDisplay:decoded.fields.find(value=>value.id===f.id).currentDisplay}));
}
async function saveModel(name){
 const synthetic={...structuredClone(model),result:structuredClone(current)};
 synthetic.pdus=synthetic.pdus.map(pdu=>{const block=current.codingRead.blocks.find(b=>(b.identifierKind==='LID'?'21':'22')+b.identifierHex===pdu.requestHex);
  return block?{...pdu,pduHex:block.pduHex}:pdu;});
 const seen=new Map();for(const pdu of synthetic.pdus)seen.set(pdu.requestHex,[...(seen.get(pdu.requestHex)||[]),pdu.pduHex]);
 assert.deepEqual(validateCompleteCodingRead(synthetic.result,seen),identity);
 await fs.writeFile(path.join(output,name),JSON.stringify(synthetic)+'\n');
}
try{
 const numeric=JSON.parse(await fs.readFile(path.join(root,'.local/obd-coding-prewrite-20261019/numeric-drafts-result.json'),'utf8')).cases.find(c=>c.profileId===model.result.codingRead.profileId).example.edit;
 // This old synthetic capture deliberately contains an out-of-range numeric
 // original. Seed a legal modeled baseline through the real source encoder,
 // before its first backup, so the numeric restoration target is meaningful.
 const c=current.codingRead,seedContext={profileId:c.profileId,generation:c.generation,ecuId:c.ecuId,sourceHash:c.definitionSha256,
  scopeHash:c.completeScopeSource.sha256,contract:current.readContract,blocks:c.blocks.map(b=>({requestHex:(b.identifierKind==='LID'?'21':'22')+b.identifierHex,dataHex:b.dataHex}))};
 await installDraft(await worker({context:seedContext,edits:[numeric]}),seedContext);
 await saveModel('baseline-model.json');
 const initial=await workspace.handle({action:'read',ecu:'headlamp-left'},1);assert.equal(initial.ok,true,JSON.stringify(initial));
 assert.equal(initial.snapshot.canPreviewRestore,true);assert.equal(initial.snapshot.canRestore,false);
 const originalContext=structuredClone(context),desktopBefore=await snapshotFiles(desktop),storeBefore=await snapshotFiles(directory);
 let review=await workspace.handle(request(initial.snapshot.readId),1);assert.equal(review.ok,true,JSON.stringify(review));
 let state=acceptCodingReview(beginCodingRestore(acceptCodingRead(createCodingDraft(),initial.snapshot,'headlamp-left')),review.review);
 assert.equal(state.review.canExecute,false);assert.equal(state.review.changes.length,0);assert.equal(state.review.rawDiffs.length,0);assert.equal(reads,1);
 checks.push('a fresh complete read compares to the immutable first original; identical data needs no restore, no extra read or saved file');

 const named=JSON.parse(await fs.readFile(path.join(root,'.local/obd-coding-prewrite-20261019/source-drafts-result.json'),'utf8')).cases.find(c=>c.profileId===model.result.codingRead.profileId).example.edit;
 const numericField=initial.snapshot.fields.find(f=>f.id===numeric.fieldId).numeric;
 numeric.physicalValue=numericField.currentValue===numericField.minimum?numericField.maximum:numericField.minimum;
 const text=initial.snapshot.fields.find(f=>f.text),edits=[named,numeric,{fieldId:text.id,textValue:'A'.repeat(text.text.length)}];
 const draft=await worker({context:originalContext,edits});await installDraft(draft,originalContext);
 await saveModel('current-model.json');
 const read=await workspace.handle({action:'read',ecu:'headlamp-left'},1);assert.equal(read.ok,true,JSON.stringify(read));
 state=acceptCodingRead(createCodingDraft(),read.snapshot,'headlamp-left');
 state=editCodingChoice(state,named.fieldId,initial.snapshot.fields.find(f=>f.id===named.fieldId).currentOptionId);
 state=beginCodingRestore(state);assert.equal(state.edits.length,0);
 const before=structuredClone(current);review=await workspace.handle(request(read.snapshot.readId),1);assert.equal(review.ok,true,JSON.stringify(review));
 state=acceptCodingReview(state,review.review);assert.equal(state.review.canExecute,false);
 assert.deepEqual(new Set(state.review.changes.map(c=>c.fieldId)),new Set(edits.map(e=>e.fieldId)));
 for(const change of state.review.changes){
  const first=initial.snapshot.fields.find(f=>f.id===change.fieldId);
  if(first.text)assert.equal(change.targetTextValue,first.text.currentValue);
  else if(first.numeric)assert.equal(change.targetPhysicalValue,first.numeric.currentValue);
  else assert.equal(change.targetOptionId,first.currentOptionId);
 }
 assert(state.review.rawDiffs.length>0);
 for(const diff of state.review.rawDiffs)assert(originalContext.blocks.some(b=>b.dataHex===diff.targetHex));
 assert.deepEqual(current,before);assert.equal(reads,2);
 assert.deepEqual(await snapshotFiles(desktop),desktopBefore);assert.deepEqual(await snapshotFiles(directory),storeBefore);
 checks.push('choice, numeric and text restore targets come from the first baseline; pending edits are discarded, only changed fields appear and complete opaque bytes remain in raw differences');

 for(const [bad,owner,error] of [[request(initial.snapshot.readId),1,'coding-read-scope-changed'],[request(read.snapshot.readId),2,'coding-read-scope-changed'],[request(read.snapshot.readId,{ecu:'headlamp-right'}),1,'coding-read-scope-changed'],[request(read.snapshot.readId,{blocks:[]}),1,'coding-workspace-invalid']])
  assert.equal((await workspace.handle(bad,owner)).error,error);
 for(const action of ['write','restore'])assert.equal((await workspace.handle({action,ecu:'headlamp-left',reviewId:review.review.id},1)).error,'coding-client-unavailable');
 drift=true;assert.equal((await workspace.handle(request(read.snapshot.readId),1)).error,'coding-restore-failed');drift=false;
 const originalName=Object.keys(storeBefore).find(n=>/^[a-f0-9]{64}\.json$/.test(n)),originalFile=path.join(directory,originalName),bytes=await fs.readFile(originalFile);
 try{await fs.writeFile(originalFile,Buffer.concat([bytes,Buffer.from('broken')]));assert.equal((await workspace.handle(request(read.snapshot.readId),1)).error,'coding-restore-failed');}
 finally{await fs.writeFile(originalFile,bytes);}
 checks.push('stale reads, another owner/unit, supplied bytes, source drift, damaged baseline and all vehicle execution attempts are rejected');

 const software=identity.software;identity.software+='-OTHER';
 try{const changedVersion=await workspace.handle({action:'read',ecu:'headlamp-left'},3);assert.equal(changedVersion.ok,true);
  assert.equal(changedVersion.snapshot.canPreviewRestore,false);
  assert.equal((await workspace.handle(request(changedVersion.snapshot.readId),3)).error,'coding-restore-failed');}
 finally{identity.software=software;}
 checks.push('the first vehicle/unit baseline is retained across software changes, but another version cannot open restoration comparison');

 hold=true;const pending=workspace.handle(request(read.snapshot.readId),1);
 for(let n=0;n<100&&!release;n++)await new Promise(resolve=>setTimeout(resolve,5));assert(release);
 assert.equal((await workspace.handle({action:'read',ecu:'headlamp-left'},1)).error,'vehicle-busy');
 workspace.cancelOwned(1);release();assert.equal((await pending).error,'coding-restore-failed');hold=false;
 assert.equal(workspace.isIdle(),true);assert.equal((await workspace.handle(request(read.snapshot.readId),1)).error,'coding-read-failed');
 assert.deepEqual(await snapshotFiles(desktop),desktopBefore);assert.deepEqual(await snapshotFiles(directory),storeBefore);
 checks.push('comparison owns the workspace until completion; closing its owner cancels it and cannot publish a late review');
 passed=true;
}finally{
 await fs.writeFile(path.join(output,'controller-result.json'),JSON.stringify({passed,checks,reads,workerCalls,sourceModel:file,vehicleReadModeled:true,actualPrivateWorker:true,writeEnabled:false,deviceIO:false,vehicleIO:false},null,2)+'\n');
 if(passed)await fs.rm(temp,{recursive:true,force:true});
}
console.log('PASS restore preview controller:',checks.length,'groups; no device/vehicle I/O');
