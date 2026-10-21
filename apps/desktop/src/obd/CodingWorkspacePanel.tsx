import {forwardRef,useEffect,useImperativeHandle,useRef,useState} from 'react';
import {acceptCodingRead,acceptCodingReview,acceptCodingResult,beginCodingRestore,codingDraftDirty,codingFieldName,codingReadFailed,codingWriteUnknown,createCodingDraft,discardCodingEdits,editCodingChoice,editCodingPhysicalValue,editCodingTextValue,returnFromCodingResult} from './coding-workspace-state.mjs';
import type {CodingDraft} from './coding-workspace-state.mjs';
import type {CodingWorkspaceRequest} from '../api';
import {obdFunctionLabel} from './function-label';
export type CodingWorkspaceHandle={read:()=>void;backup:()=>void;discard:()=>void};
const messageFor=(error?:string)=>({
 'coding-client-unavailable':'当前单元的完整读取及设码客户端尚未完成，暂不能执行。',
 'coding-current-changed':'车辆当前设码已变化。已保留调整供对照，请重新核对后决定。',
 'coding-read-scope-changed':'车辆、控制单元身份或设码定义已变化。请先撤销原来的修改，再重新读取。',
 'coding-backup-failed':'原车设码备份未完成。请重试保存成功后再修改。',
 'coding-read-failed':'本次读取未完成。请检查连接后重试，旧值仅供对照，当前不能写入。',
 'coding-reconciliation-failed':'原操作结果仍未确认。请重新读取并核对原车备份，核对前不能再次写入。',
 'vehicle-busy':'另一项车辆操作尚未结束，请等待后重试。',
} as Record<string,string>)[error||'']||'本次操作未完成，请检查连接和当前单元资料后重试。';
export const CodingWorkspacePanel=forwardRef<CodingWorkspaceHandle,{ecu:string;active:boolean;selected:boolean;onDirty:(dirty:boolean)=>void;onReady:(ready:{read:boolean;backup:boolean})=>void;onLoaded:()=>void;onVisible:(visible:boolean)=>void;discardVersion:number}>(function CodingWorkspacePanel({ecu,active,selected,onDirty,onReady,onLoaded,onVisible,discardVersion},ref){
 const [state,setState]=useState<CodingDraft>(createCodingDraft),[ready,setReady]=useState({read:false,backup:false}),[busy,setBusy]=useState(false),[writing,setWriting]=useState(false),[notice,setNotice]=useState(''),[pendingRead,setPendingRead]=useState(false),[query,setQuery]=useState(''),[group,setGroup]=useState('');
 const alive=useRef(true),serial=useRef(0),scroll=useRef({main:0,window:0});
 const rebaseRead=useRef(false);
 const invoke=(request:CodingWorkspaceRequest)=>window.porsche981?.codingWorkspace?.(request);
 useEffect(()=>{alive.current=true;rebaseRead.current=false;++serial.current;setState(createCodingDraft());setNotice('');setReady({read:false,backup:false});
  return()=>{alive.current=false;serial.current++;onDirty(false);onVisible(false);};
 },[ecu,onDirty,onReady,onVisible]);
 useEffect(()=>{let current=true;
  void Promise.resolve(invoke({action:'capability',ecu})).then(result=>{if(current&&alive.current){const value={read:result?.ok===true&&result.readReady===true,backup:result?.ok===true&&result.backupReady===true};setReady(value);onReady(value);}}).catch(()=>{});
  return()=>{current=false;};
 },[selected,ecu,onReady]);
 useEffect(()=>{onDirty(!writing&&codingDraftDirty(state));onVisible(!!state.snapshot);},[state,writing,onDirty,onVisible]);
 useEffect(()=>{rebaseRead.current=false;setState(previous=>discardCodingEdits(previous));setPendingRead(false);},[discardVersion]);
 async function read(){
  if(busy)return;if(!ready.read){setNotice(messageFor('coding-client-unavailable'));return;}
  if(rebaseRead.current){await doRead(true);return;}
  if(codingDraftDirty(state)){setPendingRead(true);return;}await doRead(false);
 }
 async function doRead(preserve:boolean){
  const seq=++serial.current;setBusy(true);setPendingRead(false);setNotice('');
  try{const result=await invoke({action:'read',ecu});if(!alive.current||seq!==serial.current)return;
   if(!result?.ok||!result.snapshot)throw new Error(result?.error||'coding-read-failed');
   setState(acceptCodingRead(state,result.snapshot,ecu,preserve));rebaseRead.current=false;onLoaded();
  }catch(error){if(alive.current&&seq===serial.current){setState(previous=>codingReadFailed(previous));setNotice(messageFor(error instanceof Error&&['coding-read-scope-changed','coding-backup-failed','coding-client-unavailable','coding-reconciliation-failed','vehicle-busy'].includes(error.message)?error.message:'coding-read-failed'));}}
  finally{if(alive.current&&seq===serial.current)setBusy(false);}
 }
 async function backup(){
  if(busy)return;if(!ready.backup){setNotice(messageFor('coding-client-unavailable'));return;}
  const seq=++serial.current;setBusy(true);setNotice('');
  try{const result=await invoke({action:'backup',ecu});if(!alive.current||seq!==serial.current)return;
   setNotice(result?.ok&&result.saved&&result.filePath?'原车设码已备份：'+result.filePath:messageFor(result?.error||'coding-backup-failed'));
  }catch{if(alive.current&&seq===serial.current)setNotice(messageFor('coding-backup-failed'));}finally{if(alive.current&&seq===serial.current)setBusy(false);}
 }
 useImperativeHandle(ref,()=>({read:()=>void read(),backup:()=>void backup(),discard:()=>{rebaseRead.current=false;setState(previous=>discardCodingEdits(previous));setPendingRead(false);}}));
 async function preview(restore=false){
  if(busy||!state.snapshot||state.stale)return;
  let draft=state;if(restore){try{draft=beginCodingRestore(state);setState(draft);}catch{setNotice('当前单元首次原码的匹配恢复定义尚未完成，暂不能执行。');return;}}
  const seq=++serial.current;setBusy(true);setNotice('');
  try{const result=await invoke(restore?{action:'preview-restore',ecu,readId:state.snapshot.readId}:{action:'preview',ecu,readId:state.snapshot.readId,edits:draft.edits.map(edit=>edit.textValue!==undefined?{fieldId:edit.fieldId,textValue:edit.textValue}:edit.physicalValue!==undefined?{fieldId:edit.fieldId,physicalValue:edit.physicalValue}:{fieldId:edit.fieldId,optionId:edit.optionId})});
   if(!alive.current||seq!==serial.current)return;if(!result?.ok||!result.review)throw new Error('unqualified');setState(acceptCodingReview(draft,result.review));
  }catch{if(alive.current&&seq===serial.current)setNotice(restore?'无法核对当前单元与首次原码。请检查完整备份及身份、版本是否一致，再重新读取。':'本次修改未通过核对。请检查选项、数值范围及是否存在互相冲突的调整。');}finally{if(alive.current&&seq===serial.current)setBusy(false);}
 }
 async function write(){
  if(busy||!state.review?.canExecute||state.stale)return;
  const seq=++serial.current;const original=state;scroll.current={main:document.querySelector('main')?.scrollTop||0,window:window.scrollY};setBusy(true);setWriting(true);setNotice('');
  try{const result=await invoke({action:state.mode==='restore'?'restore':'write',ecu,reviewId:state.review.id});if(!alive.current||seq!==serial.current)return;
   if(result?.error==='coding-current-changed'){
    try{if(!result.snapshot)throw new Error('missing');setState(acceptCodingRead(original,result.snapshot,ecu,true));rebaseRead.current=false;}
    catch(error){setState(codingReadFailed(original));rebaseRead.current=true;setNotice(messageFor(error instanceof Error&&error.message==='coding-read-scope-changed'?error.message:result.error));return;}
    setNotice(messageFor(result.error));return;
   }
   if(!result?.ok||!result.result)throw new Error('unknown');const next=acceptCodingResult(original,result.result);setState(next);rebaseRead.current=next.stale;
  }catch{if(alive.current&&seq===serial.current){rebaseRead.current=true;setState(codingWriteUnknown(original));setNotice('写入结果未确认。请重新连接后读取实际设码核对，不直接重试写入。');}}
  finally{if(alive.current&&seq===serial.current){setBusy(false);setWriting(false);}}
 }
 const snapshot=state.snapshot,groups=snapshot?[...new Set(snapshot.fields.map(field=>field.group))]:[];
 const fields=snapshot?.fields.filter(field=>(!group||field.group===group)&&(!query||codingFieldName(field).toLocaleLowerCase('zh-CN').includes(query.toLocaleLowerCase('zh-CN'))))||[];
 const restoreReady=!!snapshot?.canRestore&&!state.stale;
 return <section data-testid="coding-live-workspace">
  {notice&&<p role="status" data-testid="coding-original-notice">{notice}</p>}
  {pendingRead&&<section role="alert" data-testid="coding-reread-review"><p>尚有未写入的调整，是否丢弃后重新读取？</p><button type="button" onClick={()=>setPendingRead(false)}>继续修改</button><button type="button" onClick={()=>{setState(previous=>discardCodingEdits(previous));void doRead(false);}}>丢弃后重读</button></section>}
  {busy&&<p role="status" data-testid="coding-live-busy">{writing?'正在写入并读取核对':'正在处理当前单元'}</p>}
  {snapshot&&active&&<>
   {state.stale&&<p role="status" data-testid="coding-previous-read">上次读取 · 重新读取成功前不能写入</p>}
   <div hidden={!!state.result} data-testid="coding-live-fields">
    <div className="chip-row"><button type="button" data-testid="coding-restore-original" disabled={busy} onClick={()=>void preview(true)}>{obdFunctionLabel('恢复原码',restoreReady)}</button><button type="button" data-testid="coding-discard-all" disabled={busy||!codingDraftDirty(state)} onClick={()=>{rebaseRead.current=false;setState(previous=>discardCodingEdits(previous));}}>撤销全部修改</button></div>
    <label>搜索设码项目<input data-testid="coding-live-search" value={query} disabled={busy} onChange={event=>setQuery(event.target.value)} /></label>
    <label>用途分组<select data-testid="coding-live-group" value={group} disabled={busy} onChange={event=>setGroup(event.target.value)}><option value="">全部</option>{groups.map(value=><option key={value} value={value}>{value}</option>)}</select></label>
    {fields.map(field=><div className="panel" key={field.id} data-coding-live-field={field.id}><strong>{obdFunctionLabel(codingFieldName(field),field.editable)}</strong><p>当前值：{field.text?.currentValue===''?'空文本':field.currentDisplay}</p>
     {field.editable&&field.numeric?<><input aria-label={field.name} data-coding-number={field.id} inputMode="decimal" maxLength={128} placeholder="输入目标值" value={state.numericInputs[field.id]??state.edits.find(edit=>edit.fieldId===field.id)?.physicalValue??field.numeric.currentValue??''} disabled={busy||state.stale} onChange={event=>setState(previous=>editCodingPhysicalValue(previous,field.id,event.target.value))}/><p>支持范围：{field.numeric.minimum} ～ {field.numeric.maximum}{field.numeric.unit?' '+field.numeric.unit:''}</p>{state.edits.some(edit=>edit.fieldId===field.id&&edit.unresolved)&&<p role="status">请输入范围内的有效数值。</p>}</>
      :field.editable&&field.text?<><input aria-label={field.name} data-coding-text={field.id} maxLength={4095} placeholder="输入目标文本" value={state.textInputs[field.id]??state.edits.find(edit=>edit.fieldId===field.id)?.textValue??field.text.currentValue??''} disabled={busy||state.stale} onChange={event=>setState(previous=>editCodingTextValue(previous,field.id,event.target.value))}/><p>{field.text.minimumLength!==undefined?`支持 ${field.text.minimumLength} ～ ${field.text.length} 个字符`:`需要 ${field.text.length} 个字符`}（{field.text.encoding}）。</p>{state.edits.some(edit=>edit.fieldId===field.id&&edit.unresolved)&&<p role="status">请输入指定长度且编码支持的文本。</p>}</>
      :field.editable?<select aria-label={codingFieldName(field)} data-coding-choice={field.id} value={state.edits.find(edit=>edit.fieldId===field.id)?.optionId||field.currentOptionId||''} disabled={busy||state.stale} onChange={event=>setState(previous=>editCodingChoice(previous,field.id,event.target.value))}>{field.currentOptionId===null&&<option value="" disabled>请选择目标值</option>}{field.options.map(option=><option key={option.id} value={option.id}>{option.label}</option>)}</select>:field.description&&<p>{field.description}</p>}</div>)}
    {!fields.length&&<p>没有符合条件的项目。</p>}
    {!!state.edits.length&&<section data-testid="coding-pending-edits"><h4>本次修改</h4><ul>{state.edits.map(edit=><li key={edit.fieldId}>{edit.name} → {edit.label}{edit.unresolved?' · 需重新核对':''}</li>)}</ul></section>}
    {state.mode==='edit'&&state.edits.length>0&&!state.review&&<button type="button" data-testid="coding-review-changes" disabled={busy||state.stale||state.edits.some(edit=>edit.unresolved)} onClick={()=>void preview()}>核对本次修改</button>}
    {state.review&&<section className="panel" data-testid="coding-write-review"><h4>{state.mode==='restore'?'本次恢复':'本次修改'}</h4><ul>{state.review.changes.map(change=><li key={change.fieldId}>{change.name}：{change.before} → {change.target}</li>)}</ul>{state.review.conditions.map((condition,index)=><p key={index}>{condition}</p>)}{state.review.rawDiffs.map((diff,index)=><p key={index}>{diff.name}：<code>{diff.beforeHex} → {diff.targetHex}</code></p>)}
     {!state.review.changes.length&&<p>没有需要恢复的修改。</p>}
     <button type="button" data-testid="coding-confirm-write" disabled={busy||state.stale||!state.review.canExecute} onClick={()=>void write()}>{obdFunctionLabel(state.mode==='restore'?'确认恢复':'确认写入',state.review.canExecute)}</button></section>}
   </div>
   {state.result&&<section className="panel" data-testid="coding-write-result"><h4>写入和回读结果</h4><ul>{state.result.items.map(item=><li key={item.fieldId}>{item.name}：{item.before} → {item.target} · {{succeeded:'写入已返回',failed:'写入失败',unexecuted:'尚未执行',unconfirmed:'写入结果未确认'}[item.writeState]} · 回读：{item.readback??'未确认'}{item.matched?' · 核对一致':''}</li>)}</ul><button type="button" data-testid="coding-return-fields" onClick={()=>{setState(previous=>returnFromCodingResult(previous));requestAnimationFrame(()=>{const main=document.querySelector('main');if(main)main.scrollTop=scroll.current.main;window.scrollTo(0,scroll.current.window);});}}>返回设码列表</button></section>}
  </>}
 </section>;
});
