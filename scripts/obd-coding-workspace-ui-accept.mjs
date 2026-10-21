/** Real renderer/preload/state helpers. The coding IPC is synthetic after the
 * initial production-denial checks; no vehicle or real backup is enabled. */
import {_electron as electron} from 'playwright';
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import path from 'node:path';
const source=path.resolve(import.meta.dirname,'..'),appRoot=process.env.PORSCHE981_ACCEPT_APP_ROOT&&path.resolve(process.env.PORSCHE981_ACCEPT_APP_ROOT);
const output=path.resolve(process.env.PORSCHE981_ACCEPT_OUTPUT||path.join(source,'.local/obd-coding-workspace-20261015/source-ui'));await fs.mkdir(output,{recursive:true});const temp=await fs.mkdtemp(path.join(output,'isolated-')),fixture=path.join(temp,'devices.json');await fs.writeFile(fixture,JSON.stringify({devices:[],errors:[]}));
const env={...process.env,PORSCHE981_HEADLESS:'1',PORSCHE981_OBD_SMOKE:'1',PORSCHE981_SESSION_DENY_LIVE:'1',PORSCHE981_USER_DATA:path.join(temp,'user'),PORSCHE981_DB:path.join(temp,'ui.db'),PORSCHE981_LOCAL_ROOT:path.join(temp,'assets'),PORSCHE981_CONNECTION_FIXTURE:fixture,PORSCHE981_CONNECTION_STATE:path.join(temp,'connection.json')};delete env.ELECTRON_RUN_AS_NODE;delete env.VITE_DEV_SERVER_URL;
let app,passed=false;const checks=[],errors=[],scopeChecks=[];const timer=setTimeout(()=>{console.error('coding workspace UI total timeout');void app?.evaluate(({app})=>app.exit(1)).catch(()=>{});},120000);
try{
 app=await electron.launch({args:[appRoot||path.join(source,'apps/desktop')],env,timeout:30000});const page=await app.firstWindow();page.setDefaultTimeout(12000);page.on('pageerror',e=>errors.push(e.message));
 await page.locator('nav.side [data-tab="obd"]').click();await page.locator('[data-obd-tab="coding"]').click();await page.locator('[data-coding-system="dme"]').click();
 await page.getByTestId('coding-read-original').click();await page.getByTestId('coding-original-notice').filter({hasText:'本次读取未完成'}).waitFor();assert.equal(await page.locator('[data-coding-choice]').count(),0);
 const denied=await page.evaluate(async()=>[await window.porsche981.codingWorkspace({action:'read',ecu:'dme'}),await window.porsche981.codingWorkspace({action:'read',ecu:'dme',dataHex:'FF'}),await window.porsche981.codingWorkspace({action:'write',ecu:'dme',reviewId:'invented'})]);assert.equal(denied[0].error,'coding-read-failed');assert.equal(denied[1].error,'coding-workspace-invalid');assert.equal(denied[2].error,'coding-client-unavailable');
 checks.push('the production original reader cannot run without a diagnostic connection; source values cannot be injected and vehicle writes remain blocked');
 await app.evaluate(({ipcMain})=>{
  const s=globalThis.__codingUi={mode:'normal',reads:0,writes:0,calls:[],values:{a:'off',b:'off'},desktopSaved:true};
  const field=id=>({id,name:id==='a'?'灯光选项':'舒适选项',group:id==='a'?'灯光':'舒适',editable:true,currentOptionId:s.values[id],currentDisplay:s.values[id]==='on'?'开启':'关闭',options:[{id:'off',label:'关闭'},{id:'on',label:'开启'}]});
  const snapshot=ecu=>({ecu,readId:'read-'+ ++s.reads,identity:{generation:'981',vin:'WP0ZZZ98ZES123456',ecu,hardware:'MODEL-HW',software:'MODEL-SW',...s.identity},sourceHash:s.sourceHash||'a'.repeat(64),completeOriginal:true,sourceQualified:true,freshRead:true,desktopBackupSaved:s.desktopSaved,desktopPath:'MODEL/DESKTOP/first.json',canWrite:true,canRestore:true,fields:[field('a'),field('b'),{id:'unknown',name:'已知用途项目',group:'舒适',description:'模型：匹配定义尚未完成。',editable:false,currentOptionId:null,currentDisplay:'未核实',options:[]}]});
  s.snapshot=null;s.review=null;
  ipcMain.removeHandler('diagnostics:codingWorkspace');ipcMain.handle('diagnostics:codingWorkspace',async(_e,r)=>{
   s.calls.push(r.action);if(r.action==='capability')return{ok:true,readReady:true,backupReady:true};
   if(r.action==='backup')return{ok:true,saved:true,filePath:'MODEL/DESKTOP/first.json'};
   if(r.action==='read'){if(s.mode==='read-fail')return{ok:false,error:'PRIVATE/read-fail'};if(s.mode==='reconciliation-fail')return{ok:false,error:'coding-reconciliation-failed'};s.snapshot=snapshot(r.ecu);return{ok:true,snapshot:s.snapshot};}
   if(r.action==='preview'||r.action==='preview-restore'){
    const edits=r.action==='preview-restore'?Object.keys(s.values).filter(id=>s.values[id]!=='off').map(fieldId=>({fieldId,optionId:'off'})):r.edits;
    s.review={id:'review-'+s.reads,readId:s.snapshot.readId,mode:r.action==='preview-restore'?'restore':'edit',canExecute:true,changes:edits.map(edit=>({fieldId:edit.fieldId,beforeOptionId:s.values[edit.fieldId],targetOptionId:edit.optionId})),conditions:['模型操作条件已核对'],rawDiffs:edits.length?[{name:'模型原码差异',beforeHex:'00',targetHex:'01'}]:[]};return{ok:true,review:s.review};
   }
   if(r.action==='write'||r.action==='restore'){
    if(['conflict','scope-conflict'].includes(s.mode)){s.values.b='on';if(s.mode==='scope-conflict'){s.identity={...s.identity,...s.nextIdentity};s.sourceHash=s.nextSourceHash||s.sourceHash;}s.snapshot=snapshot(r.ecu);return{ok:false,error:'coding-current-changed',snapshot:s.snapshot};}
    s.writes++;if(s.mode==='held')await new Promise(done=>{s.finishWrite=done;});
    for(const change of s.review.changes)s.values[change.fieldId]=change.targetOptionId;
    if(s.mode==='unknown')throw new Error('PRIVATE/native/unknown');
    s.snapshot=snapshot(r.ecu);return{ok:true,result:{reviewId:r.reviewId,items:s.review.changes.map(change=>({fieldId:change.fieldId,writeState:'succeeded',readbackOptionId:s.values[change.fieldId]})),snapshot:s.snapshot}};
   }
   return{ok:false,error:'not-in-model'};
  });
  ipcMain.removeHandler('diagnostics:connection');ipcMain.handle('diagnostics:connection',()=>({ok:true,connected:true,purpose:'diagnostic',selectedDeviceId:'vnci:1',model:'VNCI',linkState:'diagnostic'}));
 });
 // Reload the selected unit so its real capability query reaches the model.
 await page.locator('[data-coding-system="pdk"]').click();await page.locator('[data-coding-system="dme"]').click();
 await page.waitForFunction(()=>document.querySelector('[data-testid="coding-read-original"]')?.textContent==='读取原车设码');
 await page.getByTestId('coding-backup-original').click();await page.getByTestId('coding-original-notice').filter({hasText:'已备份'}).waitFor();assert.equal(await page.locator('[data-coding-choice]').count(),0);
 await app.evaluate(()=>{__codingUi.desktopSaved=false;});await page.getByTestId('coding-read-original').click();await page.getByTestId('coding-original-notice').filter({hasText:'未完成'}).waitFor();assert.equal(await page.locator('[data-coding-choice]').count(),0);
 await app.evaluate(()=>{__codingUi.desktopSaved=true;});await page.getByTestId('coding-read-original').click();await page.locator('[data-coding-choice="a"]').waitFor();
 assert.match(await page.locator('[data-coding-live-field="unknown"]').innerText(),/已知用途项目\(未完成\)/);assert.equal(await page.locator('[data-coding-choice="unknown"]').count(),0);
 checks.push('backup only reports its saved path; failed desktop-baseline proof never opens editing; successful read opens grouped source options and marks unavailable fields unfinished');
 const choose=async(id,value)=>page.locator(`[data-coding-choice="${id}"]`).selectOption(value);
 await choose('a','on');await choose('a','off');assert.equal(await page.getByTestId('coding-pending-edits').count(),0);await choose('a','on');
 await page.locator('[data-coding-system="pdk"]').click();await page.getByTestId('coding-switch-review').waitFor();await page.getByTestId('coding-switch-review').getByRole('button',{name:'继续修改',exact:true}).click();assert.equal(await page.locator('[data-coding-choice="a"]').inputValue(),'on');
 await page.locator('[data-obd-tab="topology"]').click();await page.getByTestId('coding-leave-review').waitFor();await page.getByTestId('coding-leave-review').getByRole('button',{name:'丢弃后离开',exact:true}).click();await page.locator('[data-obd-tab="coding"]').click();assert.equal(await page.locator('[data-coding-choice="a"]').inputValue(),'off');
 await choose('a','on');await page.locator('nav.side [data-tab="settings"]').click();await page.getByTestId('coding-app-leave-review').waitFor();await page.getByTestId('coding-app-leave-review').getByRole('button',{name:'继续修改',exact:true}).click();assert.equal(await page.locator('[data-coding-choice="a"]').inputValue(),'on');
 await page.getByTestId('coding-read-original').click();await page.getByTestId('coding-reread-review').waitFor();await page.getByTestId('coding-reread-review').getByRole('button',{name:'继续修改',exact:true}).click();assert.equal(await page.locator('[data-coding-choice="a"]').inputValue(),'on');
 checks.push('returning a choice to current removes it; unit, internal-page, app-page and reread guards preserve edits or discard them only on the chosen action');
 await page.getByTestId('coding-live-group').selectOption('灯光');await page.getByTestId('coding-live-search').fill('灯光');await page.getByTestId('coding-review-changes').click();await page.getByTestId('coding-write-review').waitFor();assert.match(await page.getByTestId('coding-write-review').innerText(),/灯光选项：关闭 → 开启/);
 await app.evaluate(()=>{__codingUi.mode='held';});await page.getByTestId('coding-confirm-write').click();await page.getByTestId('coding-live-busy').filter({hasText:'正在写入'}).waitFor();assert.equal(await page.locator('[data-testid="coding-live-workspace"]:visible').getByRole('button',{name:/取消/}).count(),0);
 await page.locator('nav.side [data-tab="settings"]').click();await page.locator('nav.side [data-tab="obd"]').click();await app.evaluate(()=>__codingUi.finishWrite());await page.getByTestId('coding-write-result').waitFor();assert.match(await page.getByTestId('coding-write-result').innerText(),/灯光选项：关闭 → 开启/);assert.doesNotMatch(await page.getByTestId('coding-write-result').innerText(),/舒适选项|点火/);await page.getByTestId('coding-return-fields').click();assert.equal(await page.getByTestId('coding-live-search').inputValue(),'灯光');assert.equal(await page.getByTestId('coding-live-group').inputValue(),'灯光');assert.match(await page.locator('[data-coding-live-field="a"]').innerText(),/当前值：开启/);
 checks.push('in-page review confirms once; write has no cancellation control, navigation continues, and result only lists changed fields before returning to the same group/search with reread values');
 await app.evaluate(()=>{__codingUi.mode='read-fail';});await page.getByTestId('coding-read-original').click();await page.getByTestId('coding-previous-read').waitFor();assert.equal(await page.locator('[data-coding-choice="a"]').isDisabled(),true);assert.match(await page.locator('[data-coding-live-field="a"]').innerText(),/开启/);
 await app.evaluate(()=>{__codingUi.mode='reconciliation-fail';});await page.getByTestId('coding-read-original').click();await page.getByTestId('coding-original-notice').filter({hasText:'原操作结果仍未确认'}).waitFor();assert.equal(await page.locator('[data-coding-choice="a"]').isDisabled(),true);assert.match(await page.locator('[data-coding-live-field="a"]').innerText(),/开启/);
 await page.screenshot({path:path.join(output,'reconciliation-failed.png'),fullPage:true});
 checks.push('a failed pending-operation reconciliation retains the old values as stale, disables editing and explains that a fresh read and original-backup check are required');
 await app.evaluate(()=>{__codingUi.mode='normal';});await page.getByTestId('coding-read-original').click();await page.waitForFunction(()=>!document.querySelector('[data-coding-choice="a"]')?.disabled);await choose('a','off');await page.getByTestId('coding-review-changes').click();await page.getByTestId('coding-write-review').waitFor();const before=await app.evaluate(()=>__codingUi.writes);await app.evaluate(()=>{__codingUi.mode='conflict';});await page.getByTestId('coding-confirm-write').click();await page.getByTestId('coding-original-notice').filter({hasText:'保留调整供对照'}).waitFor();assert.equal(await app.evaluate(()=>__codingUi.writes),before);assert.equal(await page.locator('[data-coding-choice="a"]').inputValue(),'off');assert.equal(await page.getByTestId('coding-write-review').count(),0);
 checks.push('failed reread keeps old values and disables writing; external changes stop before write and preserve chosen targets for a new explicit review');
 await app.evaluate(()=>{__codingUi.mode='unknown';});await page.getByTestId('coding-review-changes').click();await page.getByTestId('coding-write-review').waitFor();await page.getByTestId('coding-confirm-write').click();await page.getByTestId('coding-write-result').waitFor();assert.match(await page.getByTestId('coding-write-result').innerText(),/写入结果未确认.*回读：未确认/);assert.doesNotMatch(await page.locator('[data-testid="coding-live-workspace"]:visible').innerText(),/PRIVATE|native/);await page.getByTestId('coding-return-fields').click();assert.equal(await page.locator('[data-coding-choice="a"]').isDisabled(),true);
 const writesAfterUnknown=await app.evaluate(()=>__codingUi.writes);await app.evaluate(()=>{__codingUi.mode='normal';});await page.getByTestId('coding-read-original').click();await page.waitForFunction(()=>!document.querySelector('[data-coding-choice="a"]')?.disabled);assert.equal(await page.getByTestId('coding-reread-review').count(),0);assert.equal(await app.evaluate(()=>__codingUi.writes),writesAfterUnknown);
 checks.push('unknown outcome has no fabricated readback or auto retry; a chosen reread is required before editing can continue');
 // The conflict changed b to on; the unknown write changed a to off. Restore only b.
 await page.getByTestId('coding-live-group').selectOption('');await page.getByTestId('coding-live-search').fill('');await choose('a','on');await page.getByTestId('coding-restore-original').click();await page.getByTestId('coding-write-review').waitFor();assert.doesNotMatch(await page.getByTestId('coding-write-review').innerText(),/灯光选项/);assert.match(await page.getByTestId('coding-write-review').innerText(),/舒适选项：开启 → 关闭/);assert.equal(await page.getByTestId('coding-pending-edits').count(),0);
 await page.getByTestId('coding-confirm-write').click();await page.getByTestId('coding-write-result').waitFor();assert.match(await page.getByTestId('coding-write-result').innerText(),/舒适选项/);assert.doesNotMatch(await page.getByTestId('coding-write-result').innerText(),/灯光选项/);await page.screenshot({path:path.join(output,'coding-result.png'),fullPage:true});assert.deepEqual(errors,[]);
 checks.push('restore directly discards manual edits and only reviews actual baseline differences; restore result remains limited to those changes');
  await page.getByTestId('coding-return-fields').click();
  for(const scope of ['vehicle','definition','hardware','software','generation']){
   console.log('Scope case:',scope);
   await choose('a','on');await page.getByTestId('coding-review-changes').click();await page.getByTestId('coding-write-review').waitFor();
   const beforeWrites=await app.evaluate(()=>__codingUi.writes);
   await app.evaluate((_,scope)=>{__codingUi.mode='scope-conflict';__codingUi.nextIdentity=({vehicle:{vin:'WP0ZZZ98ZES654321'},hardware:{hardware:'OTHER-HW'},software:{software:'OTHER-SW'},generation:{generation:'982',vin:'WP0ZZZ98ZJS223456'}})[scope]||{};__codingUi.nextSourceHash=scope==='definition'?'b'.repeat(64):null;},scope);
   await page.getByTestId('coding-confirm-write').click();await page.getByTestId('coding-original-notice').filter({hasText:'请先撤销原来的修改'}).waitFor();
   assert.equal(await app.evaluate(()=>__codingUi.writes),beforeWrites);assert.equal(await page.locator('[data-coding-choice="a"]').isDisabled(),true);assert.equal(await page.getByTestId('coding-write-review').count(),0);await page.screenshot({path:path.join(output,'coding-scope-'+scope+'.png'),fullPage:true});
   await page.getByTestId('coding-read-original').click();await page.getByTestId('coding-original-notice').filter({hasText:'请先撤销原来的修改'}).waitFor();assert.equal(await page.locator('[data-coding-choice="a"]').isDisabled(),true);
   await page.getByTestId('coding-discard-all').click();await app.evaluate(()=>{__codingUi.mode='normal';});await page.getByTestId('coding-read-original').click();await page.waitForFunction(()=>!document.querySelector('[data-coding-choice="a"]')?.disabled);
   assert.equal(await page.locator('[data-coding-choice="a"]').inputValue(),'off');assert.equal(await page.getByTestId('coding-pending-edits').count(),0);assert.equal(await app.evaluate(()=>__codingUi.writes),beforeWrites);scopeChecks.push(scope);
  }
  checks.push('vehicle or source definition changes keep old drafts stale and nonwritable; repeated reads cannot rebase them, and explicit discard permits a fresh scope without dispatching a write');passed=true;
}catch(error){errors.push(error.stack||String(error));console.error(error);try{await (await app?.firstWindow())?.screenshot({path:path.join(output,'coding-failure.png'),fullPage:true});}catch{}throw error;}finally{clearTimeout(timer);if(app){await app.evaluate(({app})=>app.exit(0)).catch(()=>{});await app.close().catch(()=>{});}await fs.writeFile(path.join(output,'result.json'),JSON.stringify({passed,checks,scopeChecks,errors,sourceSnapshotsAndIpcSynthetic:true,productionVehicleClientEnabled:false,deviceIO:false,vehicleIO:false},null,2)+'\n');}
