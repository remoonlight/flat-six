import { randomUUID } from 'node:crypto';
import path from 'node:path';
import { createCodingOperationJournal, codingJournalHash } from './coding-operation-journal.mjs';
import { createCodingBackupStore } from './coding-backup-store.mjs';
import { normalizeSourceCodingBackup } from './source-coding-backup.mjs';
import { codingRestorePlan, normalizeStoredCodingBackup, reconcileCodingReadback } from './diagnostic-preparation.mjs';
import { UDS_CODING_CONTEXTS } from './uds-coding-sources.mjs';

const targets = { 'headlamp-left':47, 'headlamp-right':48, gateway:9, dme:1, pdk:2,
  ...Object.fromEntries(UDS_CODING_CONTEXTS.map(row => [row.workspaceEcu, row.ecuId])) };
const fields = { capability:['action','ecu'], read:['action','ecu'], backup:['action','ecu'],
  preview:['action','ecu','readId','edits'], 'preview-restore':['action','ecu','readId'],
  write:['action','ecu','reviewId'], restore:['action','ecu','reviewId'] };
const text = value => typeof value === 'string' && value.length > 0 && value.length <= 240;
const fail = error => ({ok:false,error});

/** Only the private owned transport client supplies originals. IPC accepts no
 * backup bytes, imported IDs, identity, request lists or source formulas. */
export function createCodingWorkspace({directory,desktopDirectory,readClient,draftClient,isVehicleBusy=()=>false,filesystem}) {
  const store = createCodingBackupStore({directory,normalizeBackup:normalizeSourceCodingBackup,
    normalizeStoredBackup:normalizeStoredCodingBackup,...(filesystem ? {filesystem} : {})});
  const journal = createCodingOperationJournal({directory:path.join(directory,'operations'),...(filesystem ? {filesystem} : {})});
  let busy = false;
  const cancelled = new Set();
  const snapshots = new Map();
  let pendingDraft = null;
  return {
    isIdle:()=>!busy,
    cancelOwned(owner) { cancelled.add(owner);snapshots.delete(owner);if(pendingDraft?.owner===owner)pendingDraft.abort.abort(); },
    async handle(request,owner) {
      const keys = request && !Array.isArray(request) && Object.hasOwn(fields,request.action) && fields[request.action];
      if (!keys || Object.keys(request).some(k=>!keys.includes(k)) || !text(request.ecu)
        || ['preview','preview-restore'].includes(request.action) && !text(request.readId)
        || ['write','restore'].includes(request.action) && !text(request.reviewId)
        || request.action === 'preview' && (!Array.isArray(request.edits) || !request.edits.length || request.edits.length > 512
          || request.edits.some(e=>!e || Array.isArray(e) || Object.keys(e).length!==2 || !text(e.fieldId)
            || ['optionId','physicalValue','textValue'].filter(k=>Object.hasOwn(e,k)).length!==1
            || Object.hasOwn(e,'optionId')&&!text(e.optionId)
            || Object.hasOwn(e,'physicalValue')&&(!text(e.physicalValue)||e.physicalValue.length>128)
            || Object.hasOwn(e,'textValue')&&(typeof e.textValue!=='string'||e.textValue.length>4095)
            || Object.keys(e).some(k=>!['fieldId','optionId','physicalValue','textValue'].includes(k))))) return fail('coding-workspace-invalid');
      const supported = Object.hasOwn(targets,request.ecu) && typeof readClient === 'function';
      if (request.action === 'capability') return {ok:true,readReady:supported,backupReady:supported};
      if (!supported || !['read','backup','preview','preview-restore'].includes(request.action)
        || ['preview','preview-restore'].includes(request.action) && typeof draftClient!=='function') return fail('coding-client-unavailable');
      if (busy || isVehicleBusy()) return fail('vehicle-busy');
      if (cancelled.has(owner)) return fail('coding-read-failed');
      if(request.action==='preview-restore'){
        const held=snapshots.get(owner);
        if(!held||held.snapshot.ecu!==request.ecu||held.snapshot.readId!==request.readId)return fail('coding-read-scope-changed');
        if(!held.snapshot.canPreviewRestore)return fail('coding-restore-failed');
        busy=true;const abort=new AbortController();pendingDraft={owner,abort};
        try{
          const first=await store.firstOriginal(held.baselineIdentityKey);
          if(!first||first.sha256!==held.baselineSha256)throw new Error('coding-restore-failed');
          const plan=codingRestorePlan(first.backup,held.backup);
          const context={...held.context,blocks:first.backup.blocks.map(b=>({
            requestHex:(b.identifierKind==='LID'?'21':'22')+b.identifierHex,dataHex:b.dataHex}))};
          const result=await draftClient({context:structuredClone(context),edits:[]},abort.signal);
          if(cancelled.has(owner)||snapshots.get(owner)!==held||!validDraft(result,context))throw new Error('coding-restore-failed');
          const changes=restoreFieldChanges(held.snapshot.fields,result.fields);
          const rawDiffs=plan.blocks.filter(b=>b.changed).map((b,index)=>({
            name:`原码 ${index+1}`,beforeHex:b.beforeHex,targetHex:b.targetHex}));
          return {ok:true,review:{id:randomUUID(),readId:request.readId,mode:'restore',canExecute:false,
            changes,conditions:[rawDiffs.length?'与当前单元首次完整原码比较；未解析字段保留在原码差异中。':'当前单元与首次完整原码一致，无需恢复。',
              '恢复执行(未完成)。'],rawDiffs}};
        }catch{return fail('coding-restore-failed');}finally{pendingDraft=null;busy=false;}
      }
      if(request.action==='preview'){
        const held=snapshots.get(owner);
        if(!held||held.snapshot.ecu!==request.ecu||held.snapshot.readId!==request.readId)return fail('coding-read-scope-changed');
        const edits=structuredClone(request.edits);
        for(const edit of edits){
          const field=held.snapshot.fields.find(f=>f.id===edit.fieldId);
          if(!field?.editable || (Object.hasOwn(edit,'textValue') ? !field.text
            : Object.hasOwn(edit,'physicalValue') ? !field.numeric : field.numeric||field.text||!field.options.some(o=>o.id===edit.optionId)))return fail('coding-workspace-invalid');
        }
        if(new Set(edits.map(e=>e.fieldId)).size!==edits.length)return fail('coding-workspace-invalid');
        busy=true;const abort=new AbortController();pendingDraft={owner,abort};
        try{
          const result=await draftClient({context:structuredClone(held.context),edits},abort.signal);
          if(cancelled.has(owner)||snapshots.get(owner)!==held||!validDraft(result,held.context))throw new Error('coding-draft-failed');
          const draft=result.draft;
          if(!draft||draft.profileId!==held.context.profileId||draft.sourceDefinitionSha256!==held.snapshot.sourceHash
            ||draft.executionEnabled!==false||draft.writePayload!==null||!Array.isArray(draft.changes)
            ||draft.changes.length!==edits.length||!Array.isArray(draft.blocks)
            ||draft.blocks.length!==held.context.blocks.length)throw new Error('coding-draft-failed');
          const changes=edits.map(edit=>{
            const field=held.snapshot.fields.find(f=>f.id===edit.fieldId);
            if(draft.changes.filter(c=>c.fieldId===edit.fieldId
              &&['optionId','physicalValue','textValue'].filter(k=>Object.hasOwn(c,k)).length===1
              &&(field.text ? c.textValue===edit.textValue : field.numeric
                ? c.physicalValue===edit.physicalValue.trim() : c.optionId===edit.optionId)).length!==1)throw new Error('coding-draft-failed');
            return field.text ? {fieldId:edit.fieldId,beforeTextValue:field.text.currentValue,targetTextValue:edit.textValue}
              : field.numeric ? {fieldId:edit.fieldId,beforePhysicalValue:field.numeric.currentValue,targetPhysicalValue:edit.physicalValue.trim()}
              : {fieldId:edit.fieldId,beforeOptionId:field.currentOptionId,targetOptionId:edit.optionId};
          });
          const seen=new Set(),rawDiffs=[];
          for(const block of draft.blocks){
            const original=held.context.blocks.find(b=>b.requestHex===block.requestHex);
            if(!original||seen.has(block.requestHex)||block.beforeHex!==original.dataHex
              ||typeof block.afterHex!=='string'||!/^(?:[0-9A-F]{2})+$/.test(block.afterHex)
              ||block.afterHex.length!==block.beforeHex.length)throw new Error('coding-draft-failed');
            seen.add(block.requestHex);
            if(block.beforeHex!==block.afterHex)rawDiffs.push({name:`原码 ${rawDiffs.length+1}`,beforeHex:block.beforeHex,targetHex:block.afterHex});
          }
          return {ok:true,review:{id:randomUUID(),readId:request.readId,mode:'edit',canExecute:false,changes,
            conditions:['写入及恢复(未完成)。'],rawDiffs}};
        }catch{return fail('coding-draft-failed');}finally{pendingDraft=null;busy=false;}
      }
      if(request.action==='read')snapshots.delete(owner);
      busy = true;
      let phase = 'read';
      try {
        const checkpoints=new Map((await journal.list()).filter(o=>o.metadata?.mode==='vehicle' && o.metadata.ecu===request.ecu)
          .map(o=>[o.operationId,o.lastSha256]));
        const readStartedUtc=new Date().toISOString();
        const read = await readClient(targets[request.ecu],owner);
        if (!read?.ok || read.releaseVerified !== true || !read.identity || cancelled.has(owner)) {
          if (['coding-complete-scope-unqualified','manufacturer-coding-definition-incomplete',
            'manufacturer-coding-source-unqualified'].includes(read?.error)) return fail('coding-client-unavailable');
          throw new Error('coding-read-failed');
        }
        const value = read.result?.codingRead;
        if (!value?.completeOriginalRead || !value.completeVehicleCodingScopeQualified || value.ecuId!==targets[request.ecu]
          || read.result.vehicleGeneration!==read.identity.generation || value.generation!==read.identity.generation)
          throw new Error('coding-read-failed');
        const identity = {...read.identity,ecu:request.ecu};
        const backup = normalizeSourceCodingBackup({ schemaVersion:3,kind:'ecu-coding-backup',identity,
          profileId:value.profileId,sourceHash:value.definitionSha256,scopeHash:value.completeScopeSource?.sha256,
          targetEcuId:value.ecuId,
          blocks:value.blocks,capturedUtc:value.blocks.at(-1)?.receivedUtc,provenance:'source-vehicle-read',
          freshReadProven:true,completeness:'source-qualified-complete-record-set',liveVerified:false });
        const scopeKey=codingJournalHash(JSON.stringify({identity:backup.identity,profileId:backup.profileId}));
        for(const operation of await journal.list()){
          if(operation.metadata?.mode==='vehicle' && operation.metadata.scopeKey===scopeKey
            && (operation.requiresReconciliation || operation.requiresLockCleanup)) {
            phase='reconcile';
            await reconcileCodingReadback(journal,operation.operationId,backup,id=>store.load(id),
              {lastSha256:checkpoints.get(operation.operationId),readStartedUtc});
          }
        }
        phase = 'backup';
        let first = await store.findOriginal(backup);
        if (!first) {
          const stored = await store.persist(backup);
          first = await store.firstOriginal(stored.identityKey);
        }
        // A declared legacy import remains a legacy baseline, never a fresh,
        // complete first original. Preserve it rather than silently replace it.
        if (first?.backup.provenance !== 'source-vehicle-read' || first.backup.freshReadProven !== true) throw new Error('coding-backup-failed');
        const published = await store.publishOriginal(first.identityKey,await desktopDirectory());
        if (!published.saved || cancelled.has(owner)) throw new Error('coding-backup-failed');
        if (request.action === 'backup') return {ok:true,saved:true,filePath:published.filePath};
        phase='read';
        const context={profileId:value.profileId,generation:value.generation,ecuId:value.ecuId,
          sourceHash:value.definitionSha256,scopeHash:value.completeScopeSource.sha256,
          contract:structuredClone(read.result.readContract),
          blocks:backup.blocks.map(b=>({requestHex:(b.identifierKind==='LID'?'21':'22')+b.identifierHex,dataHex:b.dataHex}))};
        let shown=value.fields;
        if(typeof draftClient==='function'){
          const abort=new AbortController();pendingDraft={owner,abort};
          const result=await draftClient({context:structuredClone(context),edits:[]},abort.signal);
          if(cancelled.has(owner)||!validDraft(result,context)||!Array.isArray(result.fields)
            ||result.fields.length!==value.fields.length||new Set(result.fields.map(f=>f.id)).size!==result.fields.length
            ||result.fields.some(f=>!value.fields.some(v=>v.id===f.id&&v.name===f.name&&v.group===f.group&&v.currentDisplay===f.currentDisplay)))
            throw new Error('coding-read-failed');
          shown=result.fields;
        }
        let canPreviewRestore=false;
        if(typeof draftClient==='function'){
          try{codingRestorePlan(first.backup,backup);canPreviewRestore=true;}catch{/* First baseline may belong to another software/source version. */}
        }
        const snapshot={ecu:request.ecu,readId:randomUUID(),identity,sourceHash:value.definitionSha256,
          completeOriginal:true,sourceQualified:true,freshRead:true,desktopBackupSaved:true,desktopPath:published.filePath,
          canWrite:false,canRestore:false,canPreviewRestore,fields:structuredClone(shown)};
        snapshots.set(owner,{context,backup,baselineIdentityKey:first.identityKey,baselineSha256:first.sha256,snapshot:structuredClone(snapshot)});
        return {ok:true,snapshot};
      } catch { return fail(phase === 'backup' ? 'coding-backup-failed' : phase === 'reconcile' ? 'coding-reconciliation-failed' : 'coding-read-failed'); }
      finally { pendingDraft=null;busy = false; }
    },
  };
}

function validDraft(result,context){
  return result?.ok===true&&result.executionEnabled===false&&result.writePayload===null
    &&result.profileId===context.profileId&&result.sourceHash===context.sourceHash&&result.scopeHash===context.scopeHash;
}

function restoreFieldChanges(current,baseline){
  if(!Array.isArray(baseline)||baseline.length!==current.length
    ||new Set(baseline.map(f=>f.id)).size!==baseline.length)throw new Error('coding-restore-failed');
  const originals=new Map(baseline.map(f=>[f.id,f])),changes=[];
  for(const field of current){
    const first=originals.get(field.id);
    if(!first||first.name!==field.name||first.group!==field.group)throw new Error('coding-restore-failed');
    if(!field.editable)continue;
    if(field.text){
      if(typeof first.text?.currentValue==='string'&&first.text.currentValue!==field.text.currentValue)
        changes.push({fieldId:field.id,beforeTextValue:field.text.currentValue,targetTextValue:first.text.currentValue});
    }else if(field.numeric){
      if(typeof first.numeric?.currentValue==='string'&&first.numeric.currentValue!==field.numeric.currentValue)
        changes.push({fieldId:field.id,beforePhysicalValue:field.numeric.currentValue,targetPhysicalValue:first.numeric.currentValue});
    }else if(typeof first.currentOptionId==='string'&&first.currentOptionId!==field.currentOptionId
      &&field.options.some(o=>o.id===first.currentOptionId)){
      changes.push({fieldId:field.id,beforeOptionId:field.currentOptionId,targetOptionId:first.currentOptionId});
    }
  }
  return changes;
}
