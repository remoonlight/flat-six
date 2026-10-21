import assert from 'node:assert/strict';
import {createCodingDraft,acceptCodingRead,editCodingChoice,codingDraftDirty,discardCodingEdits,codingReadFailed,beginCodingRestore,acceptCodingReview,acceptCodingResult,codingWriteUnknown,returnFromCodingResult} from '../apps/desktop/src/obd/coding-workspace-state.mjs';
const snapshot=()=>({ecu:'dme',readId:'read-one',identity:{generation:'981',vin:'WP0ZZZ98ZES123456',ecu:'dme',hardware:'MODEL-HW',software:'MODEL-SW'},sourceHash:'a'.repeat(64),completeOriginal:true,sourceQualified:true,freshRead:true,desktopBackupSaved:true,desktopPath:'MODEL/DESKTOP',canWrite:true,canRestore:true,fields:[{id:'a',name:'模型选项一',group:'模型分组',editable:true,currentOptionId:'off',currentDisplay:'关闭',options:[{id:'off',label:'关闭'},{id:'on',label:'开启'}]},{id:'b',name:'模型选项二',group:'模型分组',editable:true,currentOptionId:'off',currentDisplay:'关闭',options:[{id:'off',label:'关闭'},{id:'on',label:'开启'}]},{id:'unknown',name:'用途已知但定义未完成',group:'模型分组',editable:false,currentOptionId:null,currentDisplay:'未核实',options:[]}]});
const read=()=>acceptCodingRead(createCodingDraft(),snapshot(),'dme');
const review=(state,ids=['a'],mode='edit')=>({id:'review-one',readId:state.snapshot.readId,mode,canExecute:true,changes:ids.map(fieldId=>({fieldId,beforeOptionId:'off',targetOptionId:'on'})),conditions:['模型操作条件'],rawDiffs:[{name:'模型原码差异',beforeHex:'00',targetHex:'01'}]});
const checks=[];
{
 const input=snapshot();input.canRestore=false;input.canWrite=false;input.canPreviewRestore=true;
 const draft=beginCodingRestore(acceptCodingRead(createCodingDraft(),input,'dme'));
 assert.equal(acceptCodingReview(draft,review(draft,['a'],'restore')).review.canExecute,false);
 const stale=codingReadFailed(draft);assert.throws(()=>beginCodingRestore(stale),/coding-restore-unavailable/);
 input.canPreviewRestore=false;assert.throws(()=>beginCodingRestore(acceptCodingRead(createCodingDraft(),input,'dme')),/coding-restore-unavailable/);
 input.canPreviewRestore='yes';assert.throws(()=>acceptCodingRead(createCodingDraft(),input,'dme'),/coding-snapshot-unqualified/);
 checks.push('restoration comparison is independent of execution permission; a preview-only snapshot can never enable restore');
}
{
 const scoped=snapshot();scoped.fields[0].name=scoped.fields[1].name='最后激活BC菜单';
 scoped.fields[0].sourceGroup='车载电脑显示配置钥匙1';scoped.fields[1].sourceGroup='车载电脑显示配置钥匙2';
 let draft=acceptCodingRead(createCodingDraft(),scoped,'dme');draft=editCodingChoice(draft,'a','on');draft=editCodingChoice(draft,'b','on');
 assert.deepEqual(draft.edits.map(e=>e.name),['车载电脑显示配置钥匙1 · 最后激活BC菜单','车载电脑显示配置钥匙2 · 最后激活BC菜单']);
 draft=acceptCodingReview(draft,review(draft,['a','b']));assert.deepEqual(draft.review.changes.map(e=>e.name),draft.edits.map(e=>e.name));
 for(const context of [null,42,'',' ', 'X'.repeat(4097)]){const invalid=snapshot();invalid.fields[0].sourceGroup=context;assert.throws(()=>acceptCodingRead(createCodingDraft(),invalid,'dme'),/unqualified/);}
 const oversized=snapshot();oversized.fields[0].name='X'.repeat(4090);oversized.fields[0].sourceGroup='完整来源父分组名称';assert.throws(()=>acceptCodingRead(createCodingDraft(),oversized,'dme'),/unqualified/);
 const unknown=snapshot();unknown.fields[2].sourceGroup='不能据此提升资格';assert.throws(()=>acceptCodingRead(createCodingDraft(),unknown,'dme'),/unqualified/);
 checks.push('source parent labels distinguish same-named key settings in edits and reviews; malformed context never changes field IDs or edit qualification');
}
for(const mutate of [s=>{s.completeOriginal=false;},s=>{s.desktopBackupSaved=false;},s=>{s.freshRead=false;},s=>{s.sourceQualified=false;},s=>{s.ecu='pdk';},s=>{s.fields[0].options.push({...s.fields[0].options[0]});}]){const s=snapshot();mutate(s);assert.throws(()=>acceptCodingRead(createCodingDraft(),s,'dme'),/unqualified/);}
checks.push('only a complete source-qualified fresh current-unit read with successful desktop baseline opens the editor');
let state=read();state=editCodingChoice(state,'b','on');state=editCodingChoice(state,'a','on');assert.deepEqual(state.edits.map(edit=>edit.fieldId),['a','b']);assert.equal(codingDraftDirty(state),true);state=editCodingChoice(state,'a','off');assert.deepEqual(state.edits.map(edit=>edit.fieldId),['b']);assert.throws(()=>editCodingChoice(state,'unknown','on'),/unqualified/);assert.throws(()=>editCodingChoice(state,'a','raw-hex'),/unqualified/);assert.equal(codingDraftDirty(discardCodingEdits(state)),false);
checks.push('only supplied legal options are editable; catalogue order is fixed; returning to current value removes the change and discard clears all');
state=codingReadFailed(state);assert.equal(state.snapshot.fields[1].currentDisplay,'关闭');assert.equal(state.stale,true);assert.throws(()=>editCodingChoice(state,'a','on'),/unqualified/);assert.throws(()=>acceptCodingReview(state,review(state,['b'])),/unqualified/);
const changed=snapshot();changed.readId='read-two';changed.fields[1].options=changed.fields[1].options.filter(option=>option.id!=='on');state=acceptCodingRead(state,changed,'dme',true);assert.equal(state.edits[0].unresolved,true);assert.throws(()=>acceptCodingReview(state,review(state,['b'])),/unqualified/);
checks.push('failed reread retains old values and disables editing/writing; conflict rebase preserves unsupported targets visibly unresolved');
{
 const original=editCodingChoice(read(),'a','on');
 for(const mutate of [s=>{s.identity.vin='WP0ZZZ98ZES654321';},s=>{s.identity.generation='982';},s=>{s.identity.hardware='OTHER-HW';},s=>{s.identity.software='OTHER-SW';},s=>{s.sourceHash='b'.repeat(64);}]){
  const fresh=snapshot();fresh.readId='different-scope';mutate(fresh);
  assert.throws(()=>acceptCodingRead(original,fresh,'dme',true),/coding-read-scope-changed/);
  assert.equal(original.edits[0].optionId,'on');assert.equal(original.snapshot.identity.vin,'WP0ZZZ98ZES123456');
  const accepted=acceptCodingRead(discardCodingEdits(original),fresh,'dme',true);assert.equal(accepted.edits.length,0);
 }
 const reordered=snapshot();reordered.readId='same-scope';reordered.identity=Object.fromEntries(Object.entries(reordered.identity).reverse());
 assert.equal(acceptCodingRead(original,reordered,'dme',true).edits[0].optionId,'on');
 const restore=acceptCodingReview(beginCodingRestore(read()),review(read(),['a'],'restore'));
 const changed=snapshot();changed.identity.vin='WP0ZZZ98ZES654321';
 assert.throws(()=>acceptCodingRead(restore,changed,'dme',true),/coding-read-scope-changed/);
 checks.push('retained edit and restore targets cannot cross vehicle, unit identity or definition changes; unchanged scopes preserve intent and explicit discard permits a new read');
}
state=editCodingChoice(read(),'a','on');assert.throws(()=>acceptCodingReview(state,{...review(state),changes:[]}),/incomplete/);assert.throws(()=>acceptCodingReview(state,{...review(state),changes:[{fieldId:'unknown',beforeOptionId:null,targetOptionId:'on'}]}),/unqualified/);state=acceptCodingReview(state,review(state));assert.equal(state.review.changes[0].name,'模型选项一');assert.equal(state.review.changes[0].target,'开启');
checks.push('review is tied to the current read and every requested target; labels derive from source fields/options and unknown changes are rejected');
state=editCodingChoice(read(),'a','on');state=editCodingChoice(state,'b','on');state=acceptCodingReview(state,review(state,['a','b']));const fresh=snapshot();fresh.readId='read-after-write';fresh.fields[0].currentOptionId='on';fresh.fields[0].currentDisplay='开启';fresh.identity={software:'MODEL-SW',hardware:'MODEL-HW',ecu:'dme',vin:'WP0ZZZ98ZES123456',generation:'981'};state=acceptCodingResult(state,{reviewId:'review-one',items:[{fieldId:'a',writeState:'succeeded',readbackOptionId:'on'},{fieldId:'b',writeState:'unexecuted',readbackOptionId:null}],snapshot:fresh});assert.deepEqual(state.edits.map(edit=>edit.fieldId),['b']);assert.equal(state.result.items[0].matched,true);assert.equal(state.result.items[1].readback,'关闭');assert.equal(state.result.items[1].matched,false);assert.equal(returnFromCodingResult(state).snapshot.fields[0].currentDisplay,'开启');assert.equal(returnFromCodingResult(state).edits[0].optionId,'on');
checks.push('result only contains reviewed changes; complete reread updates current values, unexecuted targets remain, and missing readback is never copied from target');
state=editCodingChoice(read(),'b','on');state=beginCodingRestore(state);assert.equal(state.edits.length,0);state=acceptCodingReview(state,review(state,['a'],'restore'));assert.equal(codingDraftDirty(state),true);state=acceptCodingResult(state,{reviewId:'review-one',items:[{fieldId:'a',writeState:'unconfirmed',readbackOptionId:null}]});assert.equal(state.stale,true);assert.equal(state.result.items[0].matched,false);assert.equal(state.edits[0].fieldId,'a');assert.equal(codingDraftDirty(discardCodingEdits(state)),false);
checks.push('restore immediately discards manual edits; only changed fields are reviewed; unknown results preserve targets and require a new complete read');
const restoreReview=acceptCodingReview(beginCodingRestore(read()),review(read(),['a'],'restore'));const changedRestore=snapshot();changedRestore.readId='restore-conflict';assert.equal(acceptCodingRead(restoreReview,changedRestore,'dme',true).edits[0].optionId,'on');
const readOnly=read();readOnly.snapshot.canWrite=false;assert.equal(acceptCodingReview(editCodingChoice(readOnly,'a','on'),review(readOnly)).review.canExecute,false);
state=acceptCodingReview(editCodingChoice(read(),'a','on'),review(read()));const invalidResult={reviewId:'review-one',items:[{fieldId:'a',writeState:'succeeded',readbackOptionId:'on'}],snapshot:{...snapshot(),readId:'different'}};assert.throws(()=>acceptCodingResult(state,invalidResult),/inconsistent/);state=codingWriteUnknown(state);assert.equal(state.stale,true);assert.equal(state.result.items[0].readback,null);assert.equal(state.edits[0].optionId,'on');assert.throws(()=>acceptCodingReview(state,review(state)),/unqualified/);
console.log(JSON.stringify({passed:true,checks,sourceSnapshotsSynthetic:true,vehiclePermissionGranted:false,deviceIO:false,vehicleIO:false}));
