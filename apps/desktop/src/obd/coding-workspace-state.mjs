/** Presentation state only. Source snapshots/reviews must arrive through the
 * application-owned IPC client; these guards never authorize a vehicle write. */
const text = value => typeof value === 'string' && value.length > 0 && value.length <= 4096;
const closed = (value, keys) => value && typeof value === 'object' && !Array.isArray(value) && Object.keys(value).every(key => keys.includes(key));
const same = (a,b) => JSON.stringify(a) === JSON.stringify(b);
const bad = code => { throw new Error(code); };
export const codingFieldName=field=>field.sourceGroup?`${field.sourceGroup} · ${field.name}`:field.name;
export function codingPhysicalValueKey(value) {
  if(typeof value!=='string'||!value.length||value.length>128)return null;
  const m=/^([+-]?)([0-9]{1,64})(?:\.([0-9]{1,64})|\/([1-9][0-9]{0,63}))?$/.exec(value.trim());
  if(!m)return null;
  let n=BigInt(m[2]+(m[3]||''))*(m[1]==='-'?-1n:1n),d=m[3]?10n**BigInt(m[3].length):BigInt(m[4]||'1');
  let a=n<0n?-n:n,b=d;while(b){const r=a%b;a=b;b=r;}
  const gcd=a||1n;n/=gcd;d/=gcd;return `${n}/${d}`;
}
const physicalParts=value=>{const key=codingPhysicalValueKey(value);return key?key.split('/').map(BigInt):null;};
const samePhysical=(a,b)=>a===null||b===null?a===null&&b===null:!!codingPhysicalValueKey(a)&&codingPhysicalValueKey(a)===codingPhysicalValueKey(b);
const numeric=field=>field?.numeric!==undefined;
const textual=field=>field?.text!==undefined;
const textLabel=value=>value===''?'空文本':value;
const latin2Printable='Ą˘Ł¤ĽŚ§¨ŠŞŤŹŽŻ°ą˛ł´ľśˇ¸šşťź˝žżŔÁÂĂÄĹĆÇČÉĘËĚÍÎĎĐŃŇÓÔŐÖ×ŘŮÚŰÜÝŢßŕáâăäĺćçčéęëěíîďđńňóôőö÷řůúűüýţ˙';
export function codingTextValueValid(field,value){
  if(!textual(field)||typeof value!=='string'||value.length<(field.text.minimumLength??field.text.length)||value.length>field.text.length)return false;
  const enc=field.text.encoding;
  if(!['ASCII','ISO-8859-1','ISO-8859-2'].includes(enc))return false;
  return [...value].every(char=>{
    const n=char.codePointAt(0);
    return n>=32&&n<=126||enc==='ISO-8859-1'&&n>=161&&n<=255&&n!==173
      ||enc==='ISO-8859-2'&&latin2Printable.includes(char);
  });
}
const inRange=(field,value)=>{
  const v=physicalParts(value),lo=physicalParts(field?.numeric?.minimum),hi=physicalParts(field?.numeric?.maximum);
  return !!v&&!!lo&&!!hi&&v[0]*lo[1]>=lo[0]*v[1]&&v[0]*hi[1]<=hi[0]*v[1];
};
const numberLabel=(field,value)=>value===null?field.currentDisplay:`${value}${field.numeric.unit?' '+field.numeric.unit:''}`;
const matches=(field,edit)=>!!field&&(textual(field)?Object.hasOwn(edit,'textValue')&&field.text.currentValue===edit.textValue:numeric(field)?Object.hasOwn(edit,'physicalValue')&&samePhysical(field.numeric.currentValue,edit.physicalValue):Object.hasOwn(edit,'optionId')&&field.currentOptionId===edit.optionId);
const supported=(field,edit)=>!!field?.editable&&(textual(field)?Object.hasOwn(edit,'textValue')&&codingTextValueValid(field,edit.textValue):numeric(field)?Object.hasOwn(edit,'physicalValue')&&inRange(field,edit.physicalValue):Object.hasOwn(edit,'optionId')&&field.options.some(o=>o.id===edit.optionId));
const reviewEdit=change=>({fieldId:change.fieldId,...(Object.hasOwn(change,'targetTextValue')?{textValue:change.targetTextValue}:Object.hasOwn(change,'targetPhysicalValue')?{physicalValue:change.targetPhysicalValue}:{optionId:change.targetOptionId}),name:change.name,label:change.target,unresolved:false});
const inputBuffers=edits=>Object.fromEntries(edits.filter(e=>Object.hasOwn(e,'physicalValue')).map(e=>[e.fieldId,e.physicalValue]));
const textBuffers=edits=>Object.fromEntries(edits.filter(e=>Object.hasOwn(e,'textValue')).map(e=>[e.fieldId,e.textValue]));
export function acceptCodingSnapshot(input, ecu) {
  if (!closed(input,['ecu','readId','identity','sourceHash','completeOriginal','sourceQualified','freshRead','desktopBackupSaved','desktopPath','canWrite','canRestore','canPreviewRestore','fields'])
    || input.ecu !== ecu || !text(input.readId) || !/^[a-f0-9]{64}$/.test(input.sourceHash || '')
    || input.completeOriginal !== true || input.sourceQualified !== true || input.freshRead !== true || input.desktopBackupSaved !== true
    || typeof input.canWrite !== 'boolean' || typeof input.canRestore !== 'boolean'
    || input.canPreviewRestore !== undefined && typeof input.canPreviewRestore !== 'boolean'
    || input.desktopPath !== undefined && (typeof input.desktopPath !== 'string' || input.desktopPath.length > 8192)
    || !closed(input.identity,['generation','vin','ecu','hardware','software']) || input.identity.ecu !== ecu
    || !['981','982'].includes(input.identity.generation) || !/^[A-HJ-NPR-Z0-9]{17}$/.test(input.identity.vin || '')
    || !text(input.identity.hardware) || !text(input.identity.software)
    || !Array.isArray(input.fields) || input.fields.length > 100000) bad('coding-snapshot-unqualified');
  const ids=new Set();
  for (const field of input.fields) {
    if (!closed(field,['id','name','group','description','editable','currentOptionId','currentDisplay','options','numeric','text','sourceGroup'])
      || !text(field.id) || ids.has(field.id) || !text(field.name) || !text(field.group)
      || typeof field.editable !== 'boolean' || typeof field.currentDisplay !== 'string' || field.currentDisplay.length > 4096
      || field.description !== undefined && (typeof field.description !== 'string' || field.description.length > 4096)
      || field.sourceGroup !== undefined && (!text(field.sourceGroup)||!field.sourceGroup.trim()||!field.editable||numeric(field)||textual(field)||codingFieldName(field).length>4096)
      || !Array.isArray(field.options) || field.options.length > 65536
      || field.options.some(option => !closed(option,['id','label']) || !text(option.id) || typeof option.label !== 'string' || option.label.length > 4096)
      || new Set(field.options.map(option=>option.id)).size !== field.options.length
      || textual(field) && (numeric(field)||!field.editable||field.currentOptionId!==null||field.options.length!==0
        ||!closed(field.text,['length','encoding','currentValue','minimumLength','termination'])||!Number.isInteger(field.text.length)||field.text.length<1||field.text.length>4095
        ||field.text.minimumLength!==undefined&&(!Number.isInteger(field.text.minimumLength)||field.text.minimumLength<0||field.text.minimumLength>field.text.length||field.text.termination!=='ZERO')
        ||field.text.minimumLength===undefined&&field.text.termination!==undefined
        ||!['ASCII','ISO-8859-1','ISO-8859-2'].includes(field.text.encoding)
        ||field.text.currentValue!==null&&!codingTextValueValid(field,field.text.currentValue))
      || numeric(field) && (!field.editable||field.currentOptionId!==null||field.options.length!==0
        ||!closed(field.numeric,['minimum','maximum','currentValue','unit'])
        ||!codingPhysicalValueKey(field.numeric.minimum)||!inRange(field,field.numeric.minimum)
        ||typeof field.numeric.unit!=='string'||field.numeric.unit.length>4096
        ||field.numeric.currentValue!==null&&!inRange(field,field.numeric.currentValue))
      || !numeric(field) && !textual(field) && field.editable && (!field.options.length || field.currentOptionId!==null && !field.options.some(option=>option.id===field.currentOptionId))
      || !field.editable && field.currentOptionId !== null && !field.options.some(option=>option.id===field.currentOptionId)) bad('coding-field-unqualified');
    ids.add(field.id);
  }
  return {...structuredClone(input),identity:Object.fromEntries(['generation','vin','ecu','hardware','software'].map(key=>[key,input.identity[key]]))};
}
export function createCodingDraft() { return { snapshot:null, edits:[], numericInputs:{}, textInputs:{}, stale:false, mode:'edit', review:null, result:null }; }
export function codingDraftDirty(state) { return state.edits.length>0 || state.mode==='restore' && !!state.review?.changes.length; }
export function acceptCodingRead(state, input, ecu, preserveEdits=false) {
  const snapshot=acceptCodingSnapshot(input,ecu);
  const targets=state.mode==='restore' && state.review ? state.review.changes.map(reviewEdit) : state.edits;
  if (preserveEdits && targets.length && state.snapshot
    && (!same(snapshot.identity,state.snapshot.identity) || snapshot.sourceHash!==state.snapshot.sourceHash)) bad('coding-read-scope-changed');
  const edits=preserveEdits ? targets.filter(edit=>!matches(snapshot.fields.find(field=>field.id===edit.fieldId),edit))
    .map(edit=>({...edit,unresolved:!supported(snapshot.fields.find(field=>field.id===edit.fieldId),edit)})) : [];
  return {snapshot,edits,numericInputs:inputBuffers(edits),textInputs:textBuffers(edits),stale:false,mode:'edit',review:null,result:null};
}
export function codingReadFailed(state) { return {...state,stale:!!state.snapshot,review:null,result:null}; }
export function discardCodingEdits(state) { return {...state,edits:[],numericInputs:{},textInputs:{},mode:'edit',review:null,result:null}; }
export function editCodingChoice(state,fieldId,optionId) {
  const field=state.snapshot?.fields.find(field=>field.id===fieldId);
  if (state.stale || !field?.editable || numeric(field) || textual(field) || !field.options.some(option=>option.id===optionId)) bad('coding-choice-unqualified');
  const edits=state.edits.filter(edit=>edit.fieldId!==fieldId);
  if (field.currentOptionId!==optionId) edits.push({fieldId,optionId,name:codingFieldName(field),label:field.options.find(option=>option.id===optionId).label,unresolved:false});
  // Keep the source catalogue's fixed order; selection/search order never sorts changes.
  edits.sort((a,b)=>state.snapshot.fields.findIndex(field=>field.id===a.fieldId)-state.snapshot.fields.findIndex(field=>field.id===b.fieldId));
  return {...state,edits,mode:'edit',review:null,result:null};
}
export function editCodingPhysicalValue(state,fieldId,physicalValue){
  const field=state.snapshot?.fields.find(f=>f.id===fieldId);
  if(state.stale||!field?.editable||!numeric(field)||typeof physicalValue!=='string'||physicalValue.length>128)bad('coding-numeric-unqualified');
  const edits=state.edits.filter(e=>e.fieldId!==fieldId);
  if(!(field.numeric.currentValue===null&&physicalValue.trim()==='')&&!samePhysical(field.numeric.currentValue,physicalValue))
    edits.push({fieldId,physicalValue,name:codingFieldName(field),label:numberLabel(field,physicalValue),unresolved:!inRange(field,physicalValue)});
  edits.sort((a,b)=>state.snapshot.fields.findIndex(f=>f.id===a.fieldId)-state.snapshot.fields.findIndex(f=>f.id===b.fieldId));
  return {...state,edits,numericInputs:{...state.numericInputs,[fieldId]:physicalValue},mode:'edit',review:null,result:null};
}
export function editCodingTextValue(state,fieldId,textValue){
  const field=state.snapshot?.fields.find(f=>f.id===fieldId);
  if(state.stale||!field?.editable||!textual(field)||typeof textValue!=='string'||textValue.length>4095)bad('coding-text-unqualified');
  const edits=state.edits.filter(e=>e.fieldId!==fieldId);
  if(!(field.text.currentValue===null&&textValue===''&&!codingTextValueValid(field,textValue))&&field.text.currentValue!==textValue)
    edits.push({fieldId,textValue,name:codingFieldName(field),label:textLabel(textValue),unresolved:!codingTextValueValid(field,textValue)});
  edits.sort((a,b)=>state.snapshot.fields.findIndex(f=>f.id===a.fieldId)-state.snapshot.fields.findIndex(f=>f.id===b.fieldId));
  return {...state,edits,textInputs:{...state.textInputs,[fieldId]:textValue},mode:'edit',review:null,result:null};
}
export function beginCodingRestore(state) {
  if (!(state.snapshot?.canRestore || state.snapshot?.canPreviewRestore) || state.stale) bad('coding-restore-unavailable');
  return {...discardCodingEdits(state),mode:'restore'};
}
export function acceptCodingReview(state,input) {
  if (!state.snapshot || state.stale || state.edits.some(edit=>edit.unresolved)
    || !closed(input,['id','readId','mode','canExecute','changes','conditions','rawDiffs']) || !text(input.id)
    || input.readId!==state.snapshot.readId || input.mode!==state.mode || typeof input.canExecute!=='boolean'
    || !Array.isArray(input.changes) || !Array.isArray(input.conditions) || input.conditions.some(value=>!text(value))
    || !Array.isArray(input.rawDiffs) || input.rawDiffs.some(row=>!closed(row,['name','beforeHex','targetHex']) || !text(row.name)
      || !/^(?:[0-9A-F]{2})+$/.test(row.beforeHex || '') || !/^(?:[0-9A-F]{2})+$/.test(row.targetHex || '') || row.beforeHex.length!==row.targetHex.length)) bad('coding-review-unqualified');
  const seen=new Set();
  const changes=input.changes.map(change=>{
    const field=state.snapshot.fields.find(field=>field.id===change.fieldId);
    if(!field?.editable||seen.has(field.id))bad('coding-review-unqualified');
    if(textual(field)){
      if(!closed(change,['fieldId','beforeTextValue','targetTextValue'])
        ||field.text.currentValue!==change.beforeTextValue||!codingTextValueValid(field,change.targetTextValue)
        ||change.beforeTextValue===change.targetTextValue)bad('coding-review-unqualified');
      seen.add(field.id);return {...change,name:codingFieldName(field),before:textLabel(field.text.currentValue)??field.currentDisplay,target:textLabel(change.targetTextValue)};
    }
    if(numeric(field)){
      if(!closed(change,['fieldId','beforePhysicalValue','targetPhysicalValue'])
        ||!samePhysical(field.numeric.currentValue,change.beforePhysicalValue)||!inRange(field,change.targetPhysicalValue)
        ||samePhysical(change.beforePhysicalValue,change.targetPhysicalValue))bad('coding-review-unqualified');
      seen.add(field.id);return {...change,name:codingFieldName(field),before:numberLabel(field,field.numeric.currentValue),target:numberLabel(field,change.targetPhysicalValue)};
    }
    if (!closed(change,['fieldId','beforeOptionId','targetOptionId'])
      || field.currentOptionId!==change.beforeOptionId || change.beforeOptionId===change.targetOptionId
      || !field.options.some(option=>option.id===change.targetOptionId)) bad('coding-review-unqualified');
    seen.add(field.id);
    return {...change,name:codingFieldName(field),before:field.currentDisplay,target:field.options.find(option=>option.id===change.targetOptionId).label};
  });
  if (state.mode==='edit' && (!state.edits.length || changes.length!==state.edits.length || state.edits.some(edit=>!changes.some(change=>change.fieldId===edit.fieldId
    &&(Object.hasOwn(edit,'textValue')?change.targetTextValue===edit.textValue:Object.hasOwn(edit,'physicalValue')?samePhysical(change.targetPhysicalValue,edit.physicalValue):change.targetOptionId===edit.optionId))))) bad('coding-review-incomplete');
  return {...state,review:{...structuredClone(input),changes,canExecute:input.canExecute && changes.length>0
    && (state.mode==='restore'?state.snapshot.canRestore:state.snapshot.canWrite)},result:null};
}
export function acceptCodingResult(state,input) {
  if (!state.review || !closed(input,['reviewId','items','snapshot']) || input.reviewId!==state.review.id || !Array.isArray(input.items)
    || input.items.length!==state.review.changes.length) bad('coding-result-unqualified');
  const seen=new Set();
  const items=input.items.map(item=>{
    const change=state.review.changes.find(change=>change.fieldId===item.fieldId),field=state.snapshot.fields.find(field=>field.id===item.fieldId);
    if(!change||!field||seen.has(item.fieldId)||!['succeeded','failed','unexecuted','unconfirmed'].includes(item.writeState))bad('coding-result-unqualified');
    if(textual(field)){
      if(!closed(item,['fieldId','writeState','readbackTextValue'])||item.readbackTextValue!==null&&!codingTextValueValid(field,item.readbackTextValue))bad('coding-result-unqualified');
      seen.add(item.fieldId);return {...item,name:change.name,before:change.before,target:change.target,
        readback:textLabel(item.readbackTextValue),matched:item.readbackTextValue!==null&&item.readbackTextValue===change.targetTextValue};
    }
    if(numeric(field)){
      if(!closed(item,['fieldId','writeState','readbackPhysicalValue'])||item.readbackPhysicalValue!==null&&!codingPhysicalValueKey(item.readbackPhysicalValue))bad('coding-result-unqualified');
      seen.add(item.fieldId);return {...item,name:change.name,before:change.before,target:change.target,
        readback:item.readbackPhysicalValue===null?null:numberLabel(field,item.readbackPhysicalValue),
        matched:item.readbackPhysicalValue!==null&&samePhysical(item.readbackPhysicalValue,change.targetPhysicalValue)};
    }
    if (!closed(item,['fieldId','writeState','readbackOptionId'])
      || item.readbackOptionId!==null && !field.options.some(option=>option.id===item.readbackOptionId)) bad('coding-result-unqualified');
    seen.add(item.fieldId);
    return {...item,name:change.name,before:change.before,target:change.target,
      readback:item.readbackOptionId===null?null:field.options.find(option=>option.id===item.readbackOptionId).label,
      matched:item.readbackOptionId!==null && item.readbackOptionId===change.targetOptionId};
  });
  let snapshot=state.snapshot,stale=true;
  if (input.snapshot) {
    const fresh=acceptCodingSnapshot(input.snapshot,state.snapshot.ecu);
    if (fresh.readId===state.snapshot.readId || !same(fresh.identity,state.snapshot.identity) || fresh.sourceHash!==state.snapshot.sourceHash) bad('coding-result-identity-changed');
    snapshot=fresh;stale=false;
    for (const item of items) {
      const field=fresh.fields.find(field=>field.id===item.fieldId);
      if(textual(field)){
        if(!field.editable||!Object.hasOwn(item,'readbackTextValue')||item.readbackTextValue!==null&&item.readbackTextValue!==field.text.currentValue)bad('coding-result-inconsistent');
        item.readbackTextValue=field.text.currentValue;item.readback=textLabel(field.text.currentValue);
        item.matched=field.text.currentValue!==null&&field.text.currentValue===state.review.changes.find(c=>c.fieldId===item.fieldId).targetTextValue;continue;
      }
      if(numeric(field)){
        if(!field.editable||Object.hasOwn(item,'readbackOptionId')||item.readbackPhysicalValue!==null&&!samePhysical(item.readbackPhysicalValue,field.numeric.currentValue))bad('coding-result-inconsistent');
        item.readbackPhysicalValue=field.numeric.currentValue;item.readback=field.numeric.currentValue===null?null:numberLabel(field,field.numeric.currentValue);
        item.matched=field.numeric.currentValue!==null&&samePhysical(field.numeric.currentValue,state.review.changes.find(c=>c.fieldId===item.fieldId).targetPhysicalValue);continue;
      }
      if (!field?.editable || item.readbackOptionId!==null && item.readbackOptionId!==field.currentOptionId) bad('coding-result-inconsistent');
      item.readbackOptionId=field.currentOptionId;
      item.readback=field.currentDisplay;
      item.matched=field.currentOptionId===state.review.changes.find(change=>change.fieldId===item.fieldId).targetOptionId;
    }
  }
  // Successful target choices are removed only after an authoritative complete
  // reread. Failed/unexecuted targets remain for an explicit new review.
  const targetEdits=state.mode==='restore' ? state.review.changes.map(reviewEdit) : state.edits;
  const edits=targetEdits.filter(edit=>stale||!matches(snapshot.fields.find(f=>f.id===edit.fieldId),edit))
    .map(edit=>({...edit,unresolved:!supported(snapshot.fields.find(f=>f.id===edit.fieldId),edit)}));
  return {...state,snapshot,edits,numericInputs:inputBuffers(edits),textInputs:textBuffers(edits),stale,result:{items},review:null,mode:'edit'};
}
export function codingWriteUnknown(state) {
  const items=(state.review?.changes || []).map(change=>({fieldId:change.fieldId,name:change.name,before:change.before,target:change.target,
    writeState:'unconfirmed',...(Object.hasOwn(change,'targetTextValue')?{readbackTextValue:null}:Object.hasOwn(change,'targetPhysicalValue')?{readbackPhysicalValue:null}:{readbackOptionId:null}),readback:null,matched:false}));
  const edits=state.mode==='restore' ? (state.review?.changes || []).map(reviewEdit) : state.edits;
  return {...state,stale:true,edits,numericInputs:inputBuffers(edits),textInputs:textBuffers(edits),review:null,mode:'edit',result:{items}};
}
export function returnFromCodingResult(state) { return {...state,result:null}; }
