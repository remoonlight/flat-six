import assert from 'node:assert/strict';
import {acceptCodingRead,acceptCodingReview,acceptCodingResult,acceptCodingSnapshot,beginCodingRestore,codingDraftDirty,codingReadFailed,
 createCodingDraft,discardCodingEdits,editCodingTextValue,codingTextValueValid,codingWriteUnknown} from '../apps/desktop/src/obd/coding-workspace-state.mjs';
const snapshot=()=>({ecu:'dme',readId:'read-one',identity:{generation:'981',vin:'WP0ZZZ98ZES123456',ecu:'dme',hardware:'MODEL-HW',software:'MODEL-SW'},
 sourceHash:'a'.repeat(64),completeOriginal:true,sourceQualified:true,freshRead:true,desktopBackupSaved:true,canWrite:true,canRestore:true,
 fields:[{id:'t',name:'模型文本',group:'模型',editable:true,currentOptionId:null,currentDisplay:'OLD!',options:[],
 text:{length:4,encoding:'ISO-8859-2',currentValue:'OLD!'}}]});
const read=()=>acceptCodingRead(createCodingDraft(),snapshot(),'dme');
const review=(state,value='NEW!',mode='edit')=>({id:'review-one',readId:state.snapshot.readId,mode,canExecute:true,
 changes:[{fieldId:'t',beforeTextValue:'OLD!',targetTextValue:value}],conditions:['模型条件'],rawDiffs:[]});
const checks=[];
for(const value of ['NEW!','ĄŁČŠ',' A  '])assert(codingTextValueValid(snapshot().fields[0],value));
for(const value of ['','ABC','ABCDE','A\0BC','汉字AB','😀ABC','A\u00a0BC','A\u00adBC'])assert.equal(codingTextValueValid(snapshot().fields[0],value),false);
let state=editCodingTextValue(read(),'t','NEW!');assert.equal(state.edits[0].unresolved,false);
assert.equal(codingDraftDirty(editCodingTextValue(state,'t','OLD!')),false);
assert.deepEqual(discardCodingEdits(state).textInputs,{});
checks.push('source text uses exact character length and supported encoding; spaces are preserved and reverting removes the change');
for(const value of ['','ABC','汉字AB']){
 state=editCodingTextValue(read(),'t',value);assert.equal(state.edits[0].unresolved,true);
 assert.throws(()=>acceptCodingReview(state,review(state)));
}
const unknown=snapshot();unknown.fields[0].text.currentValue=null;unknown.fields[0].currentDisplay='未解析';
state=acceptCodingRead(createCodingDraft(),unknown,'dme');assert.equal(state.textInputs.t,undefined);
assert.equal(codingDraftDirty(editCodingTextValue(state,'t','')),false);
assert.equal(editCodingTextValue(state,'t','NEW!').edits.length,1);
assert.throws(()=>editCodingTextValue(codingReadFailed(read()),'t','NEW!'));
checks.push('partial/invalid text cannot be reviewed, unknown current text has no default target, and stale reads cannot be edited');
state=editCodingTextValue(read(),'t','NEW!');state=acceptCodingReview(state,review(state));
assert.equal(state.review.changes[0].before,'OLD!');assert.equal(state.review.changes[0].target,'NEW!');
assert.throws(()=>acceptCodingReview(editCodingTextValue(read(),'t','NEW!'),review(read(),'OTHER')));
assert.throws(()=>acceptCodingReview(editCodingTextValue(read(),'t','NEW!'),{...review(read()),changes:[{fieldId:'t',beforeTextValue:'OLD!',targetTextValue:'NEW!',targetOptionId:'fake'}]}));
const changed=snapshot();changed.readId='read-two';changed.fields[0].text.currentValue='NEW!';changed.fields[0].currentDisplay='NEW!';
let done=acceptCodingResult(state,{reviewId:state.review.id,items:[{fieldId:'t',writeState:'succeeded',readbackTextValue:'NEW!'}],snapshot:changed});
assert.equal(done.result.items[0].matched,true);assert.equal(codingDraftDirty(done),false);
assert.throws(()=>acceptCodingResult(state,{reviewId:state.review.id,items:[{fieldId:'t',writeState:'succeeded',readbackTextValue:'OLD!'}],snapshot:changed}));
checks.push('review requires the precise selected text; only matching fresh readback clears the adjustment');
const unknownWrite=codingWriteUnknown(state);assert.equal(unknownWrite.stale,true);assert.equal(unknownWrite.textInputs.t,'NEW!');assert.equal(unknownWrite.result.items[0].readbackTextValue,null);
let rebased=acceptCodingRead(unknownWrite,{...snapshot(),readId:'read-three'},'dme',true);assert.equal(rebased.edits[0].textValue,'NEW!');
const unsupported=snapshot();unsupported.readId='read-four';unsupported.fields[0].editable=false;delete unsupported.fields[0].text;
rebased=acceptCodingRead(state,unsupported,'dme',true);assert.equal(rebased.edits[0].unresolved,true);
let restore=beginCodingRestore(read());restore=acceptCodingReview(restore,review(restore,'ORIG','restore'));
assert.equal(codingWriteUnknown(restore).edits[0].textValue,'ORIG');
checks.push('unknown write retains text for a fresh reread; removed definitions block review and restore state retains the exact requested baseline');
for(const patch of [{length:0},{encoding:'UTF-8'},{currentValue:'ABC'}]){
 const s=snapshot();Object.assign(s.fields[0].text,patch);assert.throws(()=>acceptCodingSnapshot(s,'dme'));
}
const conflicting=snapshot();conflicting.fields[0].numeric={minimum:'0',maximum:'9',currentValue:'1',unit:''};assert.throws(()=>acceptCodingSnapshot(conflicting,'dme'));
checks.push('unqualified source metadata and conflicting input kinds are rejected');
const variable=snapshot();Object.assign(variable.fields[0].text,{length:7,minimumLength:0,termination:'ZERO'});
for(const value of ['','N','ĄŁČŠ','ABCDEFG'])assert(codingTextValueValid(variable.fields[0],value));
for(const value of ['ABCDEFGH','汉字','A\0'])assert.equal(codingTextValueValid(variable.fields[0],value),false);
let v=acceptCodingRead(createCodingDraft(),variable,'dme');v=editCodingTextValue(v,'t','');assert.equal(v.edits[0].textValue,'');assert.equal(v.edits[0].unresolved,false);
v=acceptCodingReview(v,review(v,''));assert.equal(v.review.changes[0].targetTextValue,'');assert.equal(v.review.changes[0].target,'空文本');
const vu={...variable,readId:'variable-unknown',fields:[{...variable.fields[0],text:{...variable.fields[0].text,currentValue:null}}]};
let cleared=editCodingTextValue(acceptCodingRead(createCodingDraft(),vu,'dme'),'t','');assert.equal(codingDraftDirty(cleared),true);assert.equal(cleared.edits[0].unresolved,false);
cleared=acceptCodingReview(cleared,{...review(cleared,''),changes:[{fieldId:'t',beforeTextValue:null,targetTextValue:''}]});
assert.equal(codingWriteUnknown(cleared).edits[0].textValue,'');
const ve=structuredClone(variable);ve.readId='variable-empty';ve.fields[0].text.currentValue='';ve.fields[0].currentDisplay='';
assert.equal(codingDraftDirty(acceptCodingResult(v,{reviewId:v.review.id,items:[{fieldId:'t',writeState:'succeeded',readbackTextValue:''}],snapshot:ve})),false);
checks.push('source variable text supports empty/short/max targets, retains explicit empty edits from unknown originals and clears them only after matching fresh readback');
for(const patch of [{minimumLength:-1},{minimumLength:8},{termination:'END-OF-PDU'}]){
 const s=structuredClone(variable);Object.assign(s.fields[0].text,patch);assert.throws(()=>acceptCodingSnapshot(s,'dme'));
}
checks.push('variable metadata requires bounded minimum/maximum and explicit zero termination');
console.log('PASS coding text state: '+checks.length+' checks');
