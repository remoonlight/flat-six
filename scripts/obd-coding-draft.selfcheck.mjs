/** Real owned-source worker and controller; retained modeled read, no head I/O. */
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import path from 'node:path';
import {createCodingWorkspace} from '../apps/desktop/electron/coding-workspace.mjs';
import {runCodingDraft,validateRequest} from '../apps/desktop/electron/offline-diagnostics.mjs';
import {validateCompleteCodingRead} from '../apps/desktop/electron/coding-read-evidence.mjs';
import {acceptCodingRead,createCodingDraft,editCodingChoice,editCodingPhysicalValue,editCodingTextValue,acceptCodingReview} from '../apps/desktop/src/obd/coding-workspace-state.mjs';
const root=path.resolve(import.meta.dirname,'..'),out=path.resolve(process.env.PORSCHE981_ACCEPT_OUTPUT||'.local/obd-coding-desktop-draft-20261019');
await fs.mkdir(out,{recursive:true});
const temp=await fs.mkdtemp(path.join(out,'controller-')),desktop=path.join(temp,'Desktop');await fs.mkdir(desktop);
const modelPath=path.join(root,'.local/obd-coding-desktop-20261018/source-models/models/coding-00-981-47-elm.json');
const model=JSON.parse(await fs.readFile(modelPath,'utf8'));assert.equal(model.syntheticPort,true);
const observed=new Map();for(const p of model.pdus){const row=observed.get(p.requestHex)||[];row.push(p.pduHex);observed.set(p.requestHex,row);}
const identity=validateCompleteCodingRead(model.result,observed);
let reads=0,calls=0,failRead=false,failDraft=false;
const env={...process.env,PORSCHE981_DEFINITION_ROOT:path.join(root,'.local/obd-lane-coding-20261019/library')};
const workspace=createCodingWorkspace({directory:path.join(temp,'store'),desktopDirectory:()=>desktop,
 readClient:async()=>{reads++;return failRead?{ok:false}:{ok:true,result:structuredClone(model.result),identity,releaseVerified:true};},
 draftClient:async(input,signal)=>{calls++;const result=await runCodingDraft(input,{repoRoot:root,env,signal});return failDraft?{...result,sourceHash:'0'.repeat(64)}:result;}});
const checks=[];
assert.equal(validateRequest({action:'coding-owned-draft'}).error,'invalid_action');
const read=await workspace.handle({action:'read',ecu:'headlamp-left'},1);assert.equal(read.ok,true,JSON.stringify(read));
let state=acceptCodingRead(createCodingDraft(),read.snapshot,'headlamp-left');
const reference=JSON.parse(await fs.readFile(path.join(root,'.local/obd-coding-prewrite-20261019/source-drafts-result.json'),'utf8'))
 .cases.find(c=>c.profileId===model.result.codingRead.profileId).example.edit;
const field=state.snapshot.fields.find(f=>f.id===reference.fieldId);assert.equal(field.editable,true);
assert(field.options.every(o=>Object.keys(o).sort().join(',')==='id,label'));
checks.push('complete source read and first desktop backup supply source-bound options, without raw targets or vehicle permission');
const files=await fs.readdir(desktop);assert.equal(files.length,1);const original=await fs.readFile(path.join(desktop,files[0]));
state=editCodingChoice(state,reference.fieldId,reference.optionId);
const request={action:'preview',ecu:'headlamp-left',readId:read.snapshot.readId,edits:[reference]};
const result=await workspace.handle(request,1);assert.equal(result.ok,true,JSON.stringify(result));state=acceptCodingReview(state,result.review);
assert.equal(state.review.canExecute,false);assert(state.review.rawDiffs.length);assert.equal(reads,1);
assert.deepEqual(await fs.readFile(path.join(desktop,files[0])),original);
assert.equal((await fs.readdir(desktop)).length,1);
checks.push('actual Python draft reviews selected source options and full original-code differences without another vehicle read or additional desktop files');
const numericReference=JSON.parse(await fs.readFile(path.join(root,'.local/obd-coding-prewrite-20261019/numeric-drafts-result.json'),'utf8'))
 .cases.find(c=>c.profileId===model.result.codingRead.profileId).example.edit;
let mixed=acceptCodingRead(createCodingDraft(),read.snapshot,'headlamp-left');
mixed=editCodingChoice(mixed,reference.fieldId,reference.optionId);
mixed=editCodingPhysicalValue(mixed,numericReference.fieldId,numericReference.physicalValue);
assert.equal(mixed.edits.some(e=>e.unresolved),false);
const mixedResult=await workspace.handle({...request,edits:[reference,numericReference]},1);
assert.equal(mixedResult.ok,true,JSON.stringify(mixedResult));mixed=acceptCodingReview(mixed,mixedResult.review);
assert.equal(mixed.review.changes.length,2);assert.equal(mixed.review.canExecute,false);
assert.equal((await workspace.handle({...request,edits:[{fieldId:reference.fieldId,physicalValue:'1'}]},1)).error,'coding-workspace-invalid');
assert.equal((await workspace.handle({...request,edits:[{fieldId:numericReference.fieldId,physicalValue:'NaN'}]},1)).error,'coding-draft-failed');
assert.deepEqual(await fs.readFile(path.join(desktop,files[0])),original);assert.equal(reads,1);
checks.push('source numeric and named targets combine in the actual worker; invalid values and wrong input kinds cannot bypass source qualification');
const textField=read.snapshot.fields.find(f=>f.text);assert(textField);
const textReference={fieldId:textField.id,textValue:'A'.repeat(textField.text.length)};
let all=editCodingTextValue(acceptCodingRead(createCodingDraft(),read.snapshot,'headlamp-left'),textReference.fieldId,textReference.textValue);
all=editCodingChoice(all,reference.fieldId,reference.optionId);all=editCodingPhysicalValue(all,numericReference.fieldId,numericReference.physicalValue);
const allResult=await workspace.handle({...request,edits:[reference,numericReference,textReference]},1);
assert.equal(allResult.ok,true,JSON.stringify(allResult));all=acceptCodingReview(all,allResult.review);
assert.equal(all.review.changes.length,3);assert.equal(all.review.canExecute,false);
for(const textValue of ['A','汉'.repeat(textField.text.length),textReference.textValue+'A'])
 assert.equal((await workspace.handle({...request,edits:[{...textReference,textValue}]},1)).error,'coding-draft-failed');
for(const wrong of [{fieldId:reference.fieldId,textValue:'A'},{...textReference,physicalValue:'1'}])
 assert.equal((await workspace.handle({...request,edits:[wrong]},1)).error,'coding-workspace-invalid');
assert.deepEqual(await fs.readFile(path.join(desktop,files[0])),original);assert.equal(reads,1);
checks.push('source fixed text joins numeric and choice edits with strict encoding/length checks, owned originals and no extra read or saved file');

assert.equal((await workspace.handle({...request,blocks:[]},1)).error,'coding-workspace-invalid');
assert.equal((await workspace.handle({...request,edits:[{...reference,optionId:'external'}]},1)).error,'coding-workspace-invalid');
assert.equal((await workspace.handle({...request,edits:[reference,reference]},1)).error,'coding-workspace-invalid');
assert.equal((await workspace.handle(request,2)).error,'coding-read-scope-changed');
assert.equal((await workspace.handle({...request,ecu:'headlamp-right'},1)).error,'coding-read-scope-changed');
for(const action of ['write','restore'])assert.equal((await workspace.handle({action,ecu:'headlamp-left',reviewId:result.review.id},1)).error,'coding-client-unavailable');
checks.push('forged blocks/options, duplicate fields, another owner/unit and execution requests are rejected');
failDraft=true;assert.equal((await workspace.handle(request,1)).error,'coding-draft-failed');failDraft=false;
failRead=true;assert.equal((await workspace.handle({action:'read',ecu:'headlamp-left'},1)).error,'coding-read-failed');
assert.equal((await workspace.handle(request,1)).error,'coding-read-scope-changed');
checks.push('source drift invalidates a preview and failed reread invalidates the held read ID');
const evidence={passed:true,checks,reads,workerCalls:calls,modelPath,sourceModelSynthetic:true,deviceIO:false,vehicleIO:false,writeEnabled:false};
await fs.writeFile(path.join(out,'controller-result.json'),JSON.stringify(evidence,null,2)+'\n');
await fs.rm(temp,{recursive:true,force:true});console.log(JSON.stringify(evidence));
