/** Actual renderer/preload/controller/worker; only vehicle read is modeled. */
import {_electron as electron} from 'playwright';
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import path from 'node:path';
const host=path.resolve(import.meta.dirname,'..'),root=path.resolve(process.env.PORSCHE981_REVIEW_ROOT||host);
const definitionRoot=path.resolve(process.env.PORSCHE981_DEFINITION_ROOT||path.join(host,'.local/obd-lane-coding-20261019/library'));
const out=path.resolve(process.env.PORSCHE981_ACCEPT_OUTPUT||'.local/obd-coding-desktop-draft-20261019/ui');
await fs.mkdir(out,{recursive:true});const temp=await fs.mkdtemp(path.join(out,'isolated-'));
const desktop=path.join(temp,'Desktop');await fs.mkdir(desktop);
const devices=path.join(temp,'devices.json');await fs.writeFile(devices,JSON.stringify({devices:[],errors:[]}));
const variable=process.env.PORSCHE981_CODING_VARIABLE==='1',scale=process.env.PORSCHE981_CODING_SCALE==='1',rangeChoices=process.env.PORSCHE981_CODING_RANGE_CHOICES==='1',restorePreview=process.env.PORSCHE981_CODING_RESTORE_PREVIEW==='1',doorChoices=process.env.PORSCHE981_CODING_DOOR_CHOICES==='1';
const ecu=doorChoices?(process.env.PORSCHE981_CODING_DOOR_ECU||'door-driver'):rangeChoices?'cluster':variable||scale?'pcm':'headlamp-left';
assert.ok(Number(variable)+Number(scale)+Number(rangeChoices)+Number(restorePreview)+Number(doorChoices)<=1);
if(doorChoices)assert(['door-driver','door-passenger'].includes(ecu));
const modelPath=process.env.PORSCHE981_CODING_MODEL||(doorChoices?'.local/obd-door-choice-source-20261020/current-door-model.json':rangeChoices?'.local/obd-cabin-coding-source-20261018/source-models/models/cabin-06-981-8-elm.json':scale?'.local/obd-headunit-coding-source-20261018/source-models/models/headunit-02-981-70-elm.json':variable?'.local/obd-media-coding-source-20261018/source-models/models/media-00-981-70-elm.json':'.local/obd-coding-desktop-20261018/source-models/models/coding-00-981-47-elm.json');
const model=JSON.parse(await fs.readFile(path.resolve(host,modelPath),'utf8'));
assert.equal(model.syntheticPort,true);
const reference=scale||rangeChoices||doorChoices?null:JSON.parse(await fs.readFile(path.join(host,'.local/obd-coding-prewrite-20261019/source-drafts-result.json'),'utf8'))
 .cases.find(c=>c.profileId===model.result.codingRead.profileId).example.edit;
const numericReference=variable||scale||rangeChoices||doorChoices?null:JSON.parse(await fs.readFile(path.join(host,'.local/obd-coding-prewrite-20261019/numeric-drafts-result.json'),'utf8'))
 .cases.find(c=>c.profileId===model.result.codingRead.profileId).example.edit;
const env={...process.env,PORSCHE981_HEADLESS:'1',PORSCHE981_OBD_SMOKE:'1',PORSCHE981_SESSION_DENY_LIVE:'1',
 PORSCHE981_USER_DATA:path.join(temp,'user'),PORSCHE981_DB:path.join(temp,'garage.db'),PORSCHE981_LOCAL_ROOT:path.join(temp,'assets'),
 PORSCHE981_CONNECTION_FIXTURE:devices,PORSCHE981_CONNECTION_STATE:path.join(temp,'connection.json')};
delete env.ELECTRON_RUN_AS_NODE;delete env.VITE_DEV_SERVER_URL;
const app=await electron.launch({args:[root===host?path.join(root,'apps/desktop'):root],cwd:root,env});
const timer=setTimeout(()=>{void app.evaluate(({app})=>app.exit(1)).catch(()=>{});},120000);
const checks=[];let unknownCurrentChoiceChecked=false;
try{
 const page=await app.firstWindow();page.setDefaultTimeout(60000);await page.waitForLoadState('domcontentloaded');
 await app.evaluate(async({ipcMain},{root,temp,desktop,model,python,definitionRoot})=>{
  const vm=process.getBuiltinModule('vm'),{pathToFileURL}=process.getBuiltinModule('url'),path=process.getBuiltinModule('path');
  const load=file=>vm.runInThisContext(`import(${JSON.stringify(pathToFileURL(path.join(root,file)).href)})`,{importModuleDynamically:vm.constants.USE_MAIN_CONTEXT_DEFAULT_LOADER});
  const {createCodingWorkspace}=await load('apps/desktop/electron/coding-workspace.mjs');
  const {runCodingDraft}=await load('apps/desktop/electron/offline-diagnostics.mjs');
  const {validateCompleteCodingRead,validateCodingVehicleAnchor}=await load('apps/desktop/electron/coding-read-evidence.mjs');
  let seen=new Map(),anchor;
  if(model.frames?.some(f=>f.event==='coding-vehicle-anchor')){
   for(const frame of model.frames){
    if(frame.event==='diagnostic-pdu'&&!frame.result.pdu.duringCleanup){const p=frame.result.pdu;seen.set(p.requestHex,[...(seen.get(p.requestHex)||[]),p.pduHex]);}
    else if(frame.event==='coding-vehicle-anchor'){anchor=validateCodingVehicleAnchor(frame.result,seen,model.result.vehicleGeneration,model.result.vehicleIdentitySha256);seen=new Map();}
   }
  }else for(const p of model.pdus){const row=seen.get(p.requestHex)||[];row.push(p.pduHex);seen.set(p.requestHex,row);}
  const identity=validateCompleteCodingRead(model.result,seen,anchor);
  const test=globalThis.__ownedDraftUi={reads:0,snapshot:null,lastRead:null,lastWorker:null,readResult:structuredClone(model.result)};
  const workspace=createCodingWorkspace({directory:path.join(temp,'store'),desktopDirectory:()=>desktop,
   readClient:async()=>{test.reads++;return {ok:true,result:structuredClone(test.readResult),identity,releaseVerified:true};},
   draftClient:async(input,signal)=>{const result=await runCodingDraft(input,{repoRoot:root,signal,
    env:{...process.env,PORSCHE981_PYTHON:python,
      PORSCHE981_VARIANTS:path.join(definitionRoot,'.local/x431-re/2026-09-27-981982/expansion/variants.jsonl'),
      PORSCHE981_DEFINITION_ROOT:definitionRoot}});
    test.lastWorker={ok:result.ok,error:result.error,stderr:result.stderr};return result;}});
  ipcMain.removeHandler('diagnostics:codingWorkspace');
  ipcMain.handle('diagnostics:codingWorkspace',async(e,request)=>{const result=await workspace.handle(request,e.sender.id);if(result.snapshot)test.snapshot=result.snapshot;if(request.action==='read')test.lastRead={ok:result.ok,error:result.error};return result;});
 },{root,temp,desktop,model,python:process.env.PORSCHE981_PYTHON,definitionRoot});
 await page.locator('nav.side [data-tab="obd"]').click();await page.locator('[data-obd-tab="coding"]').click();
 await page.locator(`[data-coding-system="${ecu}"]`).click();await page.getByTestId('coding-read-original').click();
 let status;for(let i=0;i<150;i++){status=await app.evaluate(()=>globalThis.__ownedDraftUi);if(status.lastRead)break;await new Promise(resolve=>setTimeout(resolve,100));}
 assert.equal(status.lastRead?.ok,true,JSON.stringify({read:status.lastRead,worker:status.lastWorker}));
 const snapshot=await app.evaluate(()=>globalThis.__ownedDraftUi.snapshot);
 if(restorePreview){
  assert.equal(snapshot.canPreviewRestore,true);assert.equal(snapshot.canRestore,false);
  const files=await fs.readdir(desktop);assert.equal(files.length,1);const original=await fs.readFile(path.join(desktop,files[0]));
  await page.getByTestId('coding-restore-original').click();
  await page.getByTestId('coding-write-review').filter({hasText:'无需恢复'}).waitFor();
  assert.equal(await page.getByTestId('coding-write-review').locator('li').count(),0);
  assert.equal(await page.getByTestId('coding-confirm-write').isDisabled(),true);
  checks.push('the real restoration entry opens an empty first-baseline comparison without enabling execution');
  const next=JSON.parse(await fs.readFile(path.join(path.dirname(path.resolve(host,modelPath)),'current-model.json'),'utf8'));
  assert.equal(next.syntheticPort,true);assert.equal(next.result.codingRead.profileId,model.result.codingRead.profileId);
  await app.evaluate((_electron,result)=>{globalThis.__ownedDraftUi.readResult=result;},next.result);
  await page.getByTestId('coding-read-original').click();
  for(let n=0;n<150;n++){if(await app.evaluate((_electron,prior)=>globalThis.__ownedDraftUi.snapshot?.readId!==prior,snapshot.readId))break;await new Promise(resolve=>setTimeout(resolve,100));}
  const changed=await app.evaluate(()=>globalThis.__ownedDraftUi.snapshot);assert.notEqual(changed.readId,snapshot.readId);
  const field=snapshot.fields.find(f=>f.id===reference.fieldId);assert.equal(typeof field.currentOptionId,'string');
  await page.getByTestId('coding-live-search').fill(field.name);
  await page.waitForFunction(({id,value})=>{const input=document.querySelector(`[data-coding-choice="${id}"]`);return input&&!input.disabled&&input.value===value;},{id:field.id,value:changed.fields.find(f=>f.id===field.id).currentOptionId});
  await page.locator(`[data-coding-choice="${field.id}"]`).selectOption(field.currentOptionId);
  assert.equal(await page.getByTestId('coding-review-changes').isDisabled(),false);
  await page.getByTestId('coding-restore-original').click();
  await page.waitForFunction(()=>document.querySelectorAll('[data-testid="coding-write-review"] li').length===3);
  const review=await page.getByTestId('coding-write-review').innerText();assert(review.includes('本次恢复'));assert(review.includes(field.name));assert(review.includes('恢复执行(未完成)'));
  assert.equal(await page.getByTestId('coding-confirm-write').isDisabled(),true);
  assert.equal(await page.getByTestId('coding-discard-all').isDisabled(),false);
  assert.equal(await app.evaluate(()=>globalThis.__ownedDraftUi.reads),2);
  checks.push('a second modeled vehicle read uses the real worker; clicking restore discards pending edits and lists only the three changed choice/numeric/text fields with raw-code differences');
  for(const action of ['write','restore'])assert.equal((await page.evaluate(({action,ecu})=>window.porsche981.codingWorkspace({action,ecu,reviewId:'not-executable'}),{action,ecu})).error,'coding-client-unavailable');
  assert.equal((await fs.readdir(desktop)).length,1);assert.deepEqual(await fs.readFile(path.join(desktop,files[0])),original);
  checks.push('restoration comparison preserves the first Desktop file and performs no additional read or vehicle execution');
 }else if(doorChoices){
  const ids=['8312236','8312436','8313375','8313575'],fields=ids.map(id=>snapshot.fields.find(f=>f.id===id));
  const files=await fs.readdir(desktop);assert.equal(files.length,1);const original=await fs.readFile(path.join(desktop,files[0]));
  for(const field of fields){
   assert(field?.editable);assert.deepEqual(field.options.map(o=>o.label),field.id==='8313575'?['Audi','未启用']:['启用','未启用']);
   assert.equal(field.currentDisplay,field.options.find(o=>o.id===field.currentOptionId).label);
   await page.getByTestId('coding-live-search').fill(field.name);
   const selector=page.locator(`[data-coding-choice="${field.id}"]`);await selector.waitFor();
   assert.equal(await selector.inputValue(),field.currentOptionId);
   assert.deepEqual(await selector.locator('option').allTextContents(),field.options.map(o=>o.label));
   await selector.selectOption(field.options.find(o=>o.id!==field.currentOptionId).id);
  }
  checks.push('four exact A2_1 door fields display factory-matched current values and legal choices; Audi is retained literally and zero is not assumed to mean disabled');
  await page.getByTestId('coding-review-changes').click();await page.getByTestId('coding-write-review').waitFor();
  const review=await page.getByTestId('coding-write-review').innerText();for(const field of fields)assert(review.includes(field.name));
  assert(review.includes('Audi'));assert(review.includes('原码 1'));assert.equal(await page.getByTestId('coding-confirm-write').isDisabled(),true);
  checks.push('the real worker combines four source choices into one complete-block comparison while vehicle write and restore remain unavailable');
  for(const edit of [{fieldId:ids[0],optionId:'raw-1'},{fieldId:ids[0],physicalValue:'1'}]){
   const result=await page.evaluate(({ecu,readId,edit})=>window.porsche981.codingWorkspace({action:'preview',ecu,readId,edits:[edit]}),{ecu,readId:snapshot.readId,edit});
   assert.equal(result.error,'coding-workspace-invalid');
  }
  assert.equal(await app.evaluate(()=>globalThis.__ownedDraftUi.reads),1);
  assert.equal((await fs.readdir(desktop)).length,1);assert.deepEqual(await fs.readFile(path.join(desktop,files[0])),original);
  checks.push('raw-number or unknown-option requests fail; the first complete desktop backup and modeled vehicle read count remain unchanged');
  await page.getByTestId('coding-discard-all').click();
  await page.getByTestId('coding-live-search').fill(fields[0].name);
  const selector=page.locator(`[data-coding-choice="${ids[0]}"]`);assert.equal(await selector.inputValue(),fields[0].currentOptionId);
  await selector.selectOption(fields[0].currentOptionId);assert.equal(await page.getByTestId('coding-pending-edits').count(),0);
  await selector.selectOption(fields[0].options.find(o=>o.id!==fields[0].currentOptionId).id);
  await page.getByTestId('coding-review-changes').click();await page.getByTestId('coding-write-review').waitFor();
  checks.push('discard all restores the factory-matched current choice and selecting it again creates no pending change');
 }else if(rangeChoices){
  const evidence=JSON.parse(await fs.readFile(path.join(host,'.local/obd-coding-choice-gaps-20261019/source-result.json'),'utf8'));
  const example=evidence.cases.find(c=>c.profileId===model.result.codingRead.profileId);assert(example);
  const files=await fs.readdir(desktop);assert.equal(files.length,1);const original=await fs.readFile(path.join(desktop,files[0]));
  const targets=example.fields.slice(0,2);assert.equal(targets.length,2);assert.notEqual(targets[0].sourceGroup,targets[1].sourceGroup);
  for(const field of targets){
   const shown=snapshot.fields.find(f=>f.id===field.fieldId);assert(shown?.editable&&shown.sourceGroup===field.sourceGroup);
   assert.equal(shown.options.length,field.options);assert.equal(shown.numeric,undefined);assert.equal(shown.text,undefined);
   await page.getByTestId('coding-live-search').fill(field.sourceGroup);
   const card=page.locator(`[data-coding-live-field="${field.fieldId}"]`);await card.waitFor();
   assert((await card.locator('strong').innerText()).includes(field.sourceGroup+' · '+field.name));
   assert.equal(await page.locator(`[data-coding-live-field="${targets.find(f=>f!==field).fieldId}"]`).count(),0);
   const selector=page.locator(`[data-coding-choice="${field.fieldId}"]`);assert.equal(await selector.getAttribute('aria-label'),field.sourceGroup+' · '+field.name);
   await selector.selectOption(field.edit.optionId);
  }
  checks.push('fresh instrument source fields expose only named source options, with original key groups in headings, accessible labels and search');
  await page.getByTestId('coding-review-changes').click();await page.getByTestId('coding-write-review').waitFor();
  const review=await page.getByTestId('coding-write-review').innerText();for(const field of targets)assert(review.includes(field.sourceGroup+' · '+field.name));
  assert.equal(await page.getByTestId('coding-confirm-write').isDisabled(),true);
  checks.push('different keys remain distinct in the same actual worker review; only their declared source targets are used and vehicle writing stays disabled');
  for(const edit of [{fieldId:targets[0].fieldId,optionId:'raw-FFFF'},{fieldId:targets[0].fieldId,physicalValue:'65535'}]){
   const response=await page.evaluate(({readId,edit})=>window.porsche981.codingWorkspace({action:'preview',ecu:'cluster',readId,edits:[edit]}),{readId:snapshot.readId,edit});
   assert.equal(response.error,'coding-workspace-invalid');
  }
  assert.equal(await app.evaluate(()=>globalThis.__ownedDraftUi.reads),1);
  assert.deepEqual(await fs.readFile(path.join(desktop,files[0])),original);assert.equal((await fs.readdir(desktop)).length,1);
  checks.push('raw-number/unknown-option payloads cannot turn named ranges into a free editor; original desktop backup and read count stay unchanged');
  await page.getByTestId('coding-discard-all').click();
  await page.getByTestId('coding-live-search').fill(targets[0].sourceGroup);
  await page.locator(`[data-coding-choice="${targets[0].fieldId}"]`).selectOption(snapshot.fields.find(f=>f.id===targets[0].fieldId).currentOptionId);
  assert.equal(await page.getByTestId('coding-pending-edits').count(),0);
  await page.locator(`[data-coding-choice="${targets[0].fieldId}"]`).selectOption(targets[0].edit.optionId);
  await page.getByTestId('coding-review-changes').click();await page.getByTestId('coding-write-review').waitFor();
  checks.push('choosing the current named interval creates no pending edit or replacement with its canonical lower endpoint');
 }else if(scale){
  const evidence=JSON.parse(await fs.readFile(path.join(host,'.local/obd-coding-input-gaps-20261019/source-result.json'),'utf8'));
  const example=evidence.cases.find(c=>c.profileId===model.result.codingRead.profileId);assert(example);assert.equal(example.fields.length,3);
  const files=await fs.readdir(desktop);assert.equal(files.length,1);const original=await fs.readFile(path.join(desktop,files[0]));
  for(const field of example.fields){
   const shown=snapshot.fields.find(f=>f.id===field.fieldId);assert(shown?.editable&&shown.numeric);
   assert.equal(shown.numeric.minimum,String(field.domain[0]));assert.equal(shown.numeric.maximum,String(field.domain[1]));
   await page.getByTestId('coding-live-search').fill(field.name);
   const input=page.locator(`[data-coding-number="${field.fieldId}"]`);await input.waitFor();
   await input.fill(String(field.domain[0]-1));assert.equal(await page.getByTestId('coding-review-changes').isDisabled(),true);
   await input.fill(String(field.domain[1]+1));assert.equal(await page.getByTestId('coding-review-changes').isDisabled(),true);
   await input.fill(field.edit.physicalValue);
  }
  checks.push('actual PCM source fields expose their declared 0–100 / 50–100 ranges after the real first complete backup');
  await page.getByTestId('coding-review-changes').click();await page.getByTestId('coding-write-review').waitFor();
  const review=await page.getByTestId('coding-write-review').innerText();
  for(const field of example.fields)assert(review.includes(field.name));
  assert.equal(await page.getByTestId('coding-confirm-write').isDisabled(),true);
  checks.push('three single-segment source numeric edits combine in an actual private-worker review; vehicle writes remain disabled');
  const field=example.fields[0];
  for(const physicalValue of ['50.5','101','NaN']){
   const response=await page.evaluate(({readId,fieldId,physicalValue})=>window.porsche981.codingWorkspace({action:'preview',ecu:'pcm',readId,
    edits:[{fieldId,physicalValue}]}),{readId:snapshot.readId,fieldId:field.fieldId,physicalValue});
   assert.equal(response.error,'coding-draft-failed');
  }
  assert.equal(await app.evaluate(()=>globalThis.__ownedDraftUi.reads),1);
  assert.deepEqual(await fs.readFile(path.join(desktop,files[0])),original);assert.equal((await fs.readdir(desktop)).length,1);
  checks.push('a forged out-of-range or nonrepresentable value fails; preview neither reads the vehicle again nor changes or adds backup files');
 }else if(variable){
  const field=snapshot.fields.find(f=>f.text?.minimumLength!==undefined);assert(field);
  assert.equal(field.text.minimumLength,0);assert.equal(field.text.length,255);assert.equal(field.text.currentValue,null);
  await page.getByTestId('coding-live-search').fill(field.name);
  const input=page.locator(`[data-coding-text="${field.id}"]`);await input.waitFor();
  assert((await page.locator(`[data-coding-live-field="${field.id}"]`).innerText()).includes('0 ～ 255'));
  checks.push('actual source PCM snapshot exposes the bounded variable text only after the real first backup');
  const files=await fs.readdir(desktop);assert.equal(files.length,1);const original=await fs.readFile(path.join(desktop,files[0]));
  for(const value of ['A'.repeat(256),'汉']){await input.fill(value);assert.equal(await page.getByTestId('coding-review-changes').isDisabled(),true);}
  await input.fill('A');await page.getByTestId('coding-review-changes').click();await page.getByTestId('coding-write-review').waitFor();
  assert((await page.getByTestId('coding-write-review').innerText()).includes('4100A5'));
  assert.equal(await page.getByTestId('coding-confirm-write').isDisabled(),true);
  checks.push('variable text rejects overlong/unencodable input and an actual worker review includes a literal zero terminator');
  await page.getByTestId('coding-discard-all').click();await input.fill('A');await input.fill('');
  assert.equal(await page.getByTestId('coding-review-changes').isDisabled(),false);
  await page.getByTestId('coding-review-changes').click();await page.getByTestId('coding-write-review').waitFor();
  assert((await page.getByTestId('coding-write-review').innerText()).includes('00A5'));
  assert((await page.getByTestId('coding-write-review').innerText()).includes('空文本'));
  assert.equal(await page.getByTestId('coding-confirm-write').isDisabled(),true);
  checks.push('clearing unknown source text is an explicit empty target and preserves the complete original behind the terminator');
  for(const textValue of ['A'.repeat(256),'汉'])assert.equal((await page.evaluate(({readId,fieldId,textValue})=>window.porsche981.codingWorkspace({action:'preview',ecu:'pcm',readId,edits:[{fieldId,textValue}]}),{readId:snapshot.readId,fieldId:field.id,textValue})).error,'coding-draft-failed');
  assert.equal(await app.evaluate(()=>globalThis.__ownedDraftUi.reads),1);
  assert.deepEqual(await fs.readFile(path.join(desktop,files[0])),original);assert.equal((await fs.readdir(desktop)).length,1);
  checks.push('renderer checks cannot bypass the private worker; preview creates no extra vehicle reads, saved files or write permission');
 }else{
 const selector=page.locator(`[data-coding-choice="${reference.fieldId}"]`);await selector.waitFor();
 assert.equal(snapshot.canWrite,false);assert.equal(snapshot.canRestore,false);
 const field=snapshot.fields.find(f=>f.id===reference.fieldId);assert.equal(field.editable,true);
 const files=await fs.readdir(desktop);assert.equal(files.length,1);const original=await fs.readFile(path.join(desktop,files[0]));
 checks.push('fresh complete modeled read and real mandatory first backup open the actual source-bound choice editor');
 const reserved=snapshot.fields.find(f=>/^预留\s*[0-9]*$/.test(f.name));assert(reserved);assert.equal(reserved.editable,false);
 assert.equal(await page.locator(`[data-coding-live-field="${reserved.id}"] input,[data-coding-live-field="${reserved.id}"] select`).count(),0);
 assert.equal((await page.evaluate(({readId,fieldId})=>window.porsche981.codingWorkspace({action:'preview',ecu:'headlamp-left',readId,
  edits:[{fieldId,optionId:'source-option-0'}]}),{readId:snapshot.readId,fieldId:reserved.id})).error,'coding-workspace-invalid');
 checks.push('explicit source reserved fields remain read-only and cannot be modified by a forged IPC selection');
 const unknown=snapshot.fields.find(f=>f.editable&&!f.numeric&&!f.text&&f.currentOptionId===null);
 unknownCurrentChoiceChecked=!!unknown;
 if(unknown){assert.equal(await page.locator(`[data-coding-choice="${unknown.id}"]`).inputValue(),'');
  await page.locator(`[data-coding-choice="${unknown.id}"]`).selectOption(unknown.options[0].id);
  await page.getByTestId('coding-discard-all').click();assert.equal(await page.locator(`[data-coding-choice="${unknown.id}"]`).inputValue(),'');}
 checks.push('unknown current choices have no selected target; explicit discard restores that state');
 await page.getByTestId('coding-live-search').fill(field.name);await selector.selectOption(reference.optionId);
 await page.getByTestId('coding-pending-edits').waitFor();
 await page.locator('[data-obd-tab="topology"]').click();await page.getByTestId('coding-leave-review').waitFor();
 await page.getByTestId('coding-leave-review').getByRole('button',{name:'继续修改',exact:true}).click();
 assert.equal(await selector.inputValue(),reference.optionId);
 checks.push('real source adjustments participate in the confirmed leave-or-discard navigation guard');
 await page.getByTestId('coding-review-changes').click();await page.getByTestId('coding-write-review').waitFor();
 const review=await page.getByTestId('coding-write-review').innerText();assert(review.includes(field.name));
 assert(review.includes(field.options.find(o=>o.id===reference.optionId).label));assert(review.includes('原码 1'));
 assert.equal(await page.getByTestId('coding-confirm-write').isDisabled(),true);
 assert.equal(await app.evaluate(()=>globalThis.__ownedDraftUi.reads),1);
 assert.deepEqual(await fs.readFile(path.join(desktop,files[0])),original);assert.equal((await fs.readdir(desktop)).length,1);
 checks.push('actual private Python worker returns named before/target and complete code differences; vehicle write stays disabled and original backup stays unchanged');
 await page.getByTestId('coding-discard-all').click();await page.getByTestId('coding-live-search').fill('');
 const integerField=snapshot.fields.find(f=>f.numeric&&/^-?[0-9]+$/.test(f.numeric.currentValue||''))||snapshot.fields.find(f=>f.numeric);assert(integerField);
 const integerInput=page.locator(`[data-coding-number="${integerField.id}"]`);
 const originalNumber=integerField.numeric.currentValue||'1';
 await integerInput.fill(originalNumber+'.0');assert.equal(await integerInput.inputValue(),originalNumber+'.0');
 if(integerField.numeric.currentValue!==null)assert.equal(await page.getByTestId('coding-pending-edits').count(),0);
 await integerInput.fill(originalNumber+'.05');assert.equal(await integerInput.inputValue(),originalNumber+'.05');
 await page.getByTestId('coding-discard-all').click();
 const numberField=snapshot.fields.find(f=>f.id===numericReference.fieldId);assert(numberField.numeric);
 assert((await page.locator(`[data-coding-live-field="${numberField.id}"]`).innerText()).includes(`支持范围：${numberField.numeric.minimum} ～ ${numberField.numeric.maximum}`));
 const numberInput=page.locator(`[data-coding-number="${numberField.id}"]`);await numberInput.fill('NaN');
 assert.equal(await page.getByTestId('coding-review-changes').isDisabled(),true);
 await numberInput.fill(numericReference.physicalValue);await selector.selectOption(reference.optionId);
 await page.getByTestId('coding-review-changes').click();await page.getByTestId('coding-write-review').waitFor();
 const mixed=await page.getByTestId('coding-write-review').innerText();assert(mixed.includes(numberField.name));assert(mixed.includes(field.name));
 assert.equal(await page.getByTestId('coding-confirm-write').isDisabled(),true);
 assert.equal(await app.evaluate(()=>globalThis.__ownedDraftUi.reads),1);
 assert.deepEqual(await fs.readFile(path.join(desktop,files[0])),original);
 checks.push('source numeric input shows factory bounds, blocks malformed values and combines with a named option in an actual worker review without vehicle writes');
 assert.equal((await page.evaluate(({readId,edits})=>window.porsche981.codingWorkspace({action:'preview',ecu:'headlamp-left',readId,edits,blocks:[]}),{readId:snapshot.readId,edits:[reference]})).error,'coding-workspace-invalid');
 await page.getByTestId('coding-discard-all').click();await page.getByTestId('coding-live-search').fill('');
 const textField=snapshot.fields.find(f=>f.text);assert(textField);
 const textInput=page.locator(`[data-coding-text="${textField.id}"]`);await textInput.waitFor();
 const target='A'.repeat(textField.text.length);assert.notEqual(target,textField.text.currentValue);
 await textInput.fill('A'.repeat(textField.text.length-1));
 assert.equal(await page.getByTestId('coding-review-changes').isDisabled(),true);
 await textInput.fill('汉'.repeat(textField.text.length));
 assert.equal(await page.getByTestId('coding-review-changes').isDisabled(),true);
 await textInput.fill(target);await numberInput.fill(numericReference.physicalValue);await selector.selectOption(reference.optionId);
 await page.getByTestId('coding-review-changes').click();await page.getByTestId('coding-write-review').waitFor();
 const mixedText=await page.getByTestId('coding-write-review').innerText();assert(mixedText.includes(target));assert(mixedText.includes(textField.name));
 assert.equal(await page.getByTestId('coding-confirm-write').isDisabled(),true);
 assert.equal((await page.evaluate(({readId,fieldId})=>window.porsche981.codingWorkspace({action:'preview',ecu:'headlamp-left',readId,
  edits:[{fieldId,textValue:'汉'}]}),{readId:snapshot.readId,fieldId:textField.id})).error,'coding-draft-failed');
 assert.deepEqual(await fs.readFile(path.join(desktop,files[0])),original);assert.equal((await fs.readdir(desktop)).length,1);
 assert.equal(await app.evaluate(()=>globalThis.__ownedDraftUi.reads),1);
 checks.push('actual source text, numeric and named edits share one worker review; invalid length/encoding is blocked, originals stay intact and no vehicle write is enabled');
 }
 await page.getByTestId('coding-write-review').scrollIntoViewIfNeeded();await page.screenshot({path:path.join(out,'review.png')});
 await fs.writeFile(path.join(out,'result.json'),JSON.stringify({passed:true,checks,unknownCurrentChoiceChecked,variableTextChecked:variable,singleSegmentNumericChecked:scale,namedRangeChoicesChecked:rangeChoices,restorePreviewChecked:restorePreview,factoryDoorChoicesChecked:doorChoices,ecu,sourceProfileId:model.result.codingRead.profileId,applicationRoot:root,definitionRoot,
  syntheticVehicleRead:true,actualPrivateWorker:true,deviceIO:false,vehicleIO:false,writeEnabled:false},null,2)+'\n');
 console.log(JSON.stringify({passed:true,checks}));
}catch(error){const page=await app.firstWindow().catch(()=>null);await page?.screenshot({path:path.join(out,'failure.png')}).catch(()=>{});throw error;}
finally{clearTimeout(timer);await app.evaluate(({app})=>app.exit(0)).catch(()=>{});await app.close().catch(()=>{});await fs.rm(temp,{recursive:true,force:true});}
