import assert from 'node:assert/strict';
import {acceptCodingRead,acceptCodingReview,acceptCodingResult,beginCodingRestore,codingDraftDirty,codingReadFailed,
 createCodingDraft,discardCodingEdits,editCodingPhysicalValue,codingPhysicalValueKey,codingWriteUnknown} from '../apps/desktop/src/obd/coding-workspace-state.mjs';
const snapshot=()=>({ecu:'dme',readId:'read-one',identity:{generation:'981',vin:'WP0ZZZ98ZES123456',ecu:'dme',hardware:'MODEL-HW',software:'MODEL-SW'},
 sourceHash:'a'.repeat(64),completeOriginal:true,sourceQualified:true,freshRead:true,desktopBackupSaved:true,canWrite:true,canRestore:true,
 fields:[{id:'n',name:'模型时间',group:'模型',editable:true,currentOptionId:null,currentDisplay:'1.67 ms',options:[],
 numeric:{minimum:'-10',maximum:'100',currentValue:'5/3',unit:'ms'}}]});
const read=()=>acceptCodingRead(createCodingDraft(),snapshot(),'dme');
const review=(state,value='6',mode='edit')=>({id:'review-one',readId:state.snapshot.readId,mode,canExecute:true,
 changes:[{fieldId:'n',beforePhysicalValue:'5/3',targetPhysicalValue:value}],conditions:['模型条件'],rawDiffs:[]});
const checks=[];
assert.equal(codingPhysicalValueKey('1.0'),codingPhysicalValueKey('1/1'));
assert.equal(codingPhysicalValueKey('9007199254740993'),'9007199254740993/1');
assert.equal(codingPhysicalValueKey('-0.125'),'-1/8');
for(const value of ['1e3','NaN','1/0','1.',true,'1'.repeat(129)])assert.equal(codingPhysicalValueKey(value),null);
checks.push('decimal/fraction comparison is exact, including integers beyond floating-point precision; invalid formats never normalize to a target');
let state=editCodingPhysicalValue(read(),'n','5/3');assert.equal(codingDraftDirty(state),false);
const one=snapshot();one.fields[0].numeric.currentValue='1';state=acceptCodingRead(createCodingDraft(),one,'dme');
state=editCodingPhysicalValue(state,'n','1.0');assert.equal(codingDraftDirty(state),false);assert.equal(state.numericInputs.n,'1.0');
state=editCodingPhysicalValue(state,'n','1.05');assert.equal(state.edits[0].physicalValue,'1.05');assert.equal(state.edits[0].unresolved,false);
assert.deepEqual(discardCodingEdits(state).numericInputs,{});
checks.push('returning to the exact original value removes the change while keeping the typing buffer; decimal editing is not truncated');
for(const value of ['101','-11','1.','']){
 state=editCodingPhysicalValue(read(),'n',value);assert.equal(state.edits[0].unresolved,true);
 assert.throws(()=>acceptCodingReview(state,review(state)),/unqualified/);
}
for(const mutate of [s=>{s.fields[0].numeric.maximum='-11';},s=>{s.fields[0].numeric.currentValue='NaN';},s=>{s.fields[0].options=[{id:'raw',label:'raw'}];},s=>{s.fields[0].numeric.formula='external';}]){
 const s=snapshot();mutate(s);assert.throws(()=>acceptCodingRead(createCodingDraft(),s,'dme'),/unqualified/);
}
checks.push('source ranges and metadata shape are required; partial, out-of-range and malformed inputs cannot be reviewed');
state=editCodingPhysicalValue(read(),'n','6');state=acceptCodingReview(state,review(state));
assert.equal(state.review.changes[0].before,'5/3 ms');assert.equal(state.review.changes[0].target,'6 ms');
assert.throws(()=>acceptCodingReview(editCodingPhysicalValue(read(),'n','6'),review(read(),'7')),/incomplete/);
const fresh=snapshot();fresh.readId='read-after';fresh.fields[0].numeric.currentValue='6';fresh.fields[0].currentDisplay='6 ms';
state=acceptCodingResult(state,{reviewId:'review-one',items:[{fieldId:'n',writeState:'succeeded',readbackPhysicalValue:'6.0'}],snapshot:fresh});
assert.equal(state.result.items[0].matched,true);assert.equal(state.edits.length,0);
checks.push('numeric review is tied to the exact requested value; only authoritative fresh readback removes a successful target');
state=editCodingPhysicalValue(read(),'n','6');const limited=snapshot();limited.readId='narrowed';limited.fields[0].numeric.maximum='5';
state=acceptCodingRead(state,limited,'dme',true);assert.equal(state.edits[0].unresolved,true);
assert.throws(()=>editCodingPhysicalValue(codingReadFailed(state),'n','4'),/unqualified/);
state=acceptCodingReview(editCodingPhysicalValue(read(),'n','6'),review(read()));state=codingWriteUnknown(state);
assert.equal(state.result.items[0].readback,null);assert.equal(state.stale,true);assert.equal(state.edits[0].physicalValue,'6');
state=beginCodingRestore(editCodingPhysicalValue(read(),'n','6'));assert.deepEqual(state.numericInputs,{});assert.equal(state.edits.length,0);
checks.push('reread conflicts retain unsupported targets, failed reads disable edits, unknown writes require reread, and restore discards typed adjustments');
console.log(JSON.stringify({passed:true,checks,syntheticSnapshots:true,deviceIO:false,vehicleIO:false}));
