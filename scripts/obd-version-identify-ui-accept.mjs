/** Real Electron and real private directory with explicit identity/connection models; no hardware. */
import {_electron as electron} from 'playwright';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import {pathToFileURL} from 'node:url';
import {verifyInstalledEntry} from '../apps/desktop/electron/installed-entry.mjs';
import {verifyUnidentifiedLiveUi,verifyUnreadCodingUi} from './obd-unidentified-ui-check.mjs';
const source=path.resolve(import.meta.dirname,'..'),root=path.resolve(process.env.PORSCHE981_ACCEPT_APP_ROOT||source);
const {mainlandParameter}=await import(pathToFileURL(path.join(root,'apps/desktop/src/obd/live-unit-logic.mjs')));
if(process.env.PORSCHE981_ACCEPT_APP_ROOT)verifyInstalledEntry(root);
const task=path.resolve(process.env.PORSCHE981_IDENTITY_MODELS||path.join(source,'.local/obd-version-identify-20261016'));
const ecuId=Number(process.env.PORSCHE981_IDENTITY_MODEL_ECU||5);
const generation=process.env.PORSCHE981_IDENTITY_MODEL_GENERATION||'981';assert.ok(['981','982'].includes(generation));
const output=path.resolve(process.env.PORSCHE981_ACCEPT_OUTPUT||path.join(task,'source-identity-ui'));fs.mkdirSync(output,{recursive:true});
const bundle=path.resolve(process.env.PORSCHE981_ACCEPT_DEFINITIONS_ZIP||path.join(task,'definitions-next.zip'));
const modelFile=(kind)=>[path.join(task,kind+'-model-'+(generation==='982'?'982-':'')+ecuId+'.json'),path.join(task,'models',generation,kind+'-model-'+ecuId+'.json')].find(file=>fs.existsSync(file));
const model=JSON.parse(fs.readFileSync(modelFile('identity'),'utf8'));assert.equal(model.syntheticPort,true);assert.equal(model.ecuId,ecuId);
const vehicleModelFile=g=>['vehicle-source-','vehicle-'].map(prefix=>path.resolve(process.env.PORSCHE981_VEHICLE_MODELS||'.local/obd-ecm-uds-reader-20261016',prefix+g+'.json')).find(file=>fs.existsSync(file));
const vehicleModel=JSON.parse(fs.readFileSync(vehicleModelFile(generation),'utf8'));assert.equal(vehicleModel.syntheticPort,true);
const result=model.frames.at(-1).result;assert.equal(result.identityOnly,true);
if(process.env.PORSCHE981_EXPECT_VERSION_CHANGE==='0')assert.equal(model.profileId,model.requestProfileId);
else assert.notEqual(model.profileId,model.requestProfileId);
const captureModel=generation==='982'?JSON.parse(fs.readFileSync(modelFile('worker'),'utf8')):null;
if(captureModel){assert.equal(captureModel.syntheticPort,true);assert.equal(captureModel.profileId,model.profileId);assert.equal(captureModel.generation,generation);}
const temp=fs.mkdtempSync(path.join(output,'isolated-')),library=path.join(temp,'library'),fixture=path.join(temp,'devices.json');
fs.writeFileSync(fixture,JSON.stringify({devices:[],errors:[]}));
const env={...process.env,PYTHONDONTWRITEBYTECODE:'1',PORSCHE981_DB:path.join(temp,'ui.db'),PORSCHE981_HEADLESS:'1',PORSCHE981_SESSION_DENY_LIVE:'1',
 PORSCHE981_USER_DATA:path.join(temp,'user-data'),PORSCHE981_LOCAL_ROOT:path.join(temp,'assets'),PORSCHE981_OBD_SMOKE:'1',
 PORSCHE981_CONNECTION_FIXTURE:fixture,PORSCHE981_CONNECTION_STATE:path.join(temp,'connection.json'),PORSCHE981_DEFINITION_ROOT:library,
 PORSCHE981_REALTIME_PREPARATION:path.join(library,'.local/diagnostics/realtime-preparation'),
 PORSCHE981_VARIANTS:path.join(library,'.local/x431-re/2026-09-27-981982/expansion/variants.jsonl'),
 PORSCHE981_LANGUAGE:path.join(library,'.local/x431-re/2026-09-27-protocol/package/PORSCHE_CN.GGP')};
delete env.ELECTRON_RUN_AS_NODE;delete env.VITE_DEV_SERVER_URL;
let app,passed=false;const checks=[],errors=[];const watchdog=setTimeout(()=>{console.error('identity UI total timeout');void app?.close();},180000);
try{
 app=await electron.launch({args:[process.env.PORSCHE981_ACCEPT_APP_ROOT?root:path.join(root,'apps/desktop')],env,timeout:30000});const page=await app.firstWindow();page.setDefaultTimeout(15000);page.on('pageerror',e=>errors.push(e.message));
 await app.evaluate(({dialog},file)=>{dialog.showOpenDialog=async()=>({canceled:false,filePaths:[file]});},bundle);
 const imported=await page.evaluate(()=>window.porsche981.diagnosticDefinitionBundle({action:'import'}));assert.equal(imported.ok,true,JSON.stringify(imported));
 const units=await page.evaluate(generation=>window.porsche981.offlineDiagnostics({action:'ready-units',generation}),generation);
 const targetUnit=units.units.find(unit=>unit.ecuId===ecuId);assert.ok(targetUnit);
 await app.evaluate(({ipcMain},{result,vehicleResult})=>{
  globalThis.__identity={connected:false,model:'ELM327',linkState:'idle',phase:'running',released:false,result,fail:false,calls:[],stops:0,reads:[],vehicleResult,vehicleCalls:[],vehiclePhase:'closing'};
  ipcMain.removeHandler('diagnostics:connection');ipcMain.handle('diagnostics:connection',(_event,request)=>{
   if(request.action!=='status')return{ok:false,error:'model-status-only'};
   const s=globalThis.__identity;return{ok:true,connected:s.connected,linkState:s.linkState,purpose:'diagnostic',model:s.model,selectedDeviceId:'bt:001122334455',serialBaud:null,voltageVolts:null};
  });
  ipcMain.removeHandler('diagnostics:manufacturerStream');ipcMain.handle('diagnostics:manufacturerStream',(_event,request)=>{
   const s=globalThis.__identity;
   if(request.action==='identify-vehicle'){s.vehicleCalls.push(request);return{ok:true,sessionId:'vehicle-model',state:'running',released:false,events:[],cursor:0};}
   if(request.action==='status' && request.sessionId==='vehicle-model')return{ok:true,sessionId:request.sessionId,state:s.vehiclePhase,released:s.vehiclePhase==='finished',events:[],cursor:0,connectionEvents:[],connectionCursor:0,hasMore:false,hasMoreConnectionEvents:false,result:s.vehiclePhase==='finished'?s.vehicleResult:null};
   if(request.action==='identify'){s.calls.push(request);s.connected=false;s.linkState='diagnostic';return{ok:true,sessionId:'identity-model-'+s.calls.length,state:'running',released:false,events:[],cursor:0};}
   if(request.action==='status' && request.sessionId==='collection-model'){
    const events=request.cursor===0?[s.captureModel.frames.find(frame=>frame.event==='cycle').result]:[];
    return{ok:true,sessionId:request.sessionId,state:s.captureFinished?'finished':'running',released:s.captureFinished,
     events,cursor:1,connectionEvents:[],connectionCursor:0,hasMore:false,hasMoreConnectionEvents:false,
     result:s.captureFinished?s.captureModel.frames.at(-1).result:null};
   }
   if(request.action==='status'){
    if(s.fail){s.fail=false;throw new Error('explicit modeled status failure');}
    if(s.phase==='finished'){s.connected=true;s.linkState='connected';}
    return{ok:true,sessionId:request.sessionId,state:s.phase,released:s.released,events:[],cursor:0,connectionEvents:[],connectionCursor:0,hasMore:false,hasMoreConnectionEvents:false,error:null,result:s.phase==='finished'?s.result:null};
   }
   if(request.action==='stop'){s.stops++;s.phase='closing';s.released=false;s.linkState='connecting';s.result={ok:false,error:'cancelled'};return{ok:true};}
   if(request.action==='start-read'){s.reads.push(request);return s.captureModel?{ok:true,sessionId:'collection-model'}:{ok:false,error:'live_not_enabled'};}
   if(request.action==='discard'){(s.discards??=[]).push(request.sessionId);return{ok:true};}
   return{ok:true};
  });
 },{result,vehicleResult:vehicleModel.frames.at(-1).result});
 const count=()=>app.evaluate(()=>globalThis.__identity.calls.length);
 const finish=async(success=true)=>app.evaluate((_electron,arg)=>{const s=globalThis.__identity;s.phase='finished';s.released=true;s.result=arg.result;s.connected=true;s.linkState='connected';},{result:success?result:{ok:false,error:'identity-mismatch'}});
 const startPending=()=>app.evaluate(()=>{Object.assign(globalThis.__identity,{phase:'running',released:false,fail:false});});
 const connected=async state=>{await app.evaluate((_electron,state)=>{Object.assign(globalThis.__identity,{connected:state,linkState:state?'connected':'idle'});},state);if(!state)await page.waitForFunction(()=>/已连接设备：\s*无/.test(document.body.innerText));};
 const params=page.getByTestId('offline-parameters').locator('input');
 await verifyUnidentifiedLiveUi(page);await verifyUnreadCodingUi(page);
 checks.push('portable Windows/Mac checks keep parameters hidden before recognition and expose only current coding controls before a fresh read');
 await page.locator('nav.side [data-tab="obd"]').click();await page.locator('[data-obd-tab="live"]').click();assert.equal(await page.locator('select[data-testid=eng-generation]').count(),0);
 await page.getByTestId('eng-system').selectOption(targetUnit.systemId);
 assert.equal(await page.getByTestId('eng-data-source').count(),0);
 assert.equal(await params.count(),0);
 await page.getByTestId('manufacturer-rehearsal').waitFor();assert.equal(await params.count(),0);assert.equal(await page.getByTestId('offline-variant').count(),0);assert.equal(await page.getByTestId('manufacturer-readonly').count(),0);assert.equal(await page.getByTestId('manufacturer-read').isDisabled(),true);
  const body=await page.locator('body').innerText();
  for(const removed of ['车辆读取使用当前所选诊断头','仅执行已定义的厂商参数只读采集','参数选择（已选'])assert.equal(body.includes(removed),false);
 assert.equal(await page.getByTestId('offline-selection').count(),0);
 for(const id of ['eng-generation','vehicle-x431-inactive','vehicle-readonly','manufacturer-x431-inactive','manufacturer-state'])assert.equal(await page.getByTestId(id).count(),0);
 assert.equal(await page.getByText('尚未开始',{exact:true}).count(),0);
 assert.equal(await count(),0);
 await page.locator('[data-obd-tab="connection"]').click();await connected(true);await page.waitForFunction(()=>document.body.textContent.includes('已连接设备：ELM327'));
 await page.waitForTimeout(1300);assert.equal(await count(),0);checks.push('unidentified unit shows no parameters or manual source/version selector; connecting on settings does not start discovery');
 await page.locator('[data-obd-tab="live"]').click();await page.waitForFunction(()=>document.querySelector('[data-testid="vehicle-identify-state"]')?.textContent.includes('等待'));
 assert.equal(await count(),0);assert.equal(await page.getByTestId('eng-system').isDisabled(),true);
 await app.evaluate(()=>{globalThis.__identity.vehiclePhase='finished';});
 await page.waitForFunction(()=>document.querySelector('[data-testid="manufacturer-state"]')?.textContent.includes('识别'));
 assert.equal(await page.getByTestId('eng-generation').count(),0);
 const vehicleRequests=await app.evaluate(()=>globalThis.__identity.vehicleCalls);assert.deepEqual(Object.keys(vehicleRequests[0]).sort(),['action','confirmedReadOnly','x431Inactive']);
 checks.push('automatic fresh vehicle recognition has no model selector, cannot start unit identification before release and chooses the matching catalogue');
 assert.equal(await count(),1);assert.equal(await page.getByTestId('eng-system').isDisabled(),true);assert.ok(await params.evaluateAll(xs=>xs.every(x=>x.disabled)));
 const req=await app.evaluate(()=>globalThis.__identity.calls[0]);assert.deepEqual(Object.keys(req).sort(),['action','confirmedReadOnly','ecuId',...(generation==='982'?['generation']:[]),'x431Inactive']);if(generation==='982')assert.equal(req.generation,'982');assert.equal(req.ecuId,ecuId);assert.equal(await page.locator('select[data-testid=eng-generation]').count(),0);
 await app.evaluate(()=>{globalThis.__identity.phase='closing';globalThis.__identity.linkState='connecting';});
 await page.waitForFunction(()=>document.querySelector('[data-testid="manufacturer-state"]')?.textContent.includes('等待'));
 assert.equal(await page.getByTestId('manufacturer-read').isDisabled(),true);assert.equal(await page.getByTestId('eng-system').isDisabled(),true);
 await finish();await page.waitForFunction(id=>document.querySelector('[data-testid="offline-realtime"]')?.dataset.profileId===id,model.profileId);
 const directory=await page.evaluate(({profileId,ecuId,generation})=>window.porsche981.offlineDiagnostics({action:'ready-parameters',generation,ecuId,profileId,limit:1}),{profileId:model.profileId,ecuId,generation});
 const parameterId=directory.items[0].id;await page.getByTestId('offline-select-'+parameterId).waitFor();await page.waitForFunction(id=>!document.querySelector('[data-testid="offline-select-'+id+'"]')?.disabled,parameterId);
 assert.equal(await page.getByTestId('offline-version').count(),0);
 assert.equal(await page.getByTestId('offline-selection').isVisible(),true);
 assert.equal(await page.getByTestId('offline-selection').evaluate(el=>el.closest('details')===null),true);
 for(const label of ['数据清单','参数目录版本','车辆读取使用当前所选诊断头','仅执行已定义的厂商参数只读采集','参数选择（已选'])assert.equal((await page.locator('[data-page="engine-session"]').innerText()).includes(label),false);
 assert.equal(await page.getByTestId('offline-parameters').locator('input:checked').count(),0);assert.equal(await page.getByTestId('offline-selected').count(),0);assert.equal(await page.getByTestId('offline-variant').count(),0);assert.equal(await page.getByTestId('offline-selection').evaluate(el=>el.tagName),'DIV');assert.equal(await page.getByTestId('offline-selection').locator('summary').count(),0);assert.equal(await count(),1);
 checks.push('confirmed read-only discovery blocks selection until release, then loads the matched real directory and clears old selections without collecting values');
 await page.getByTestId('offline-select-'+parameterId).check();await page.getByTestId('manufacturer-read').click();await page.getByTestId('manufacturer-error').waitFor();
 const reads=await app.evaluate(()=>globalThis.__identity.reads);assert.equal(reads.length,1);assert.equal(reads[0].ecuId,ecuId);assert.equal(reads[0].profileId,model.profileId);assert.deepEqual(reads[0].parameterIds,[parameterId]);if(generation==='982')assert.equal(reads[0].generation,'982');assert.equal(await page.getByTestId('manufacturer-save').isDisabled(),true);
 checks.push('collection uses the identified version and its own parameter ID; headless modeled start cannot fabricate values or save an identity record');
 await connected(false);await page.waitForFunction(()=>!document.querySelector('[data-testid="offline-realtime"]')?.dataset.profileId);
 await app.evaluate(()=>{Object.assign(globalThis.__identity,{phase:'finished',released:true,result:{ok:false,error:'identity-mismatch'}});});await connected(true);
 await page.getByTestId('manufacturer-identify-retry').waitFor();await page.waitForFunction(()=>document.querySelector('[data-testid="manufacturer-state"]')?.textContent.includes('未完成'));
 assert.equal(await count(),2);assert.ok(await params.evaluateAll(xs=>xs.every(x=>x.disabled)));assert.equal(await page.getByTestId('manufacturer-read').isDisabled(),true);
 assert.equal(await page.getByTestId('offline-variant').count(),0);assert.equal(await params.count(),0);
 checks.push('unknown version hides parameter selection and has no manual catalogue bypass');
 await startPending();await page.getByTestId('manufacturer-identify-retry').click();await page.waitForFunction(()=>document.querySelector('[data-testid="manufacturer-stop"]')?.textContent==='终止');
 await page.getByTestId('manufacturer-stop').click();await page.waitForFunction(()=>document.querySelector('[data-testid="manufacturer-state"]')?.textContent.includes('等待'));
 assert.equal(await page.getByTestId('eng-system').isDisabled(),true);await app.evaluate(()=>{const s=globalThis.__identity;s.phase='finished';s.released=true;s.connected=true;s.linkState='connected';});
 await page.waitForFunction(()=>document.querySelector('[data-testid="manufacturer-state"]')?.textContent.includes('已终止'));
 await page.waitForTimeout(400);assert.equal(await count(),3);assert.equal(await app.evaluate(()=>globalThis.__identity.stops),1);
 checks.push('terminate waits for confirmed release, publishes no version and does not restart automatically');
 await startPending();await app.evaluate(()=>{globalThis.__identity.fail=true;});await page.getByTestId('manufacturer-identify-retry').click();
 await page.waitForFunction(()=>document.querySelector('[data-testid="manufacturer-state"]')?.textContent.includes('等待'));
 assert.equal(await app.evaluate(()=>globalThis.__identity.stops),2);assert.equal(await page.getByTestId('eng-system').isDisabled(),true);
 await app.evaluate(()=>{const s=globalThis.__identity;s.phase='finished';s.released=true;s.connected=true;s.linkState='connected';});
 await page.waitForFunction(()=>document.querySelector('[data-testid="manufacturer-state"]')?.textContent.includes('已终止'));assert.equal(await app.evaluate(()=>globalThis.__identity.stops),2);
 checks.push('status failure requests stop once and keeps controls locked until the original job releases');
 await startPending();await page.getByTestId('manufacturer-identify-retry').click();await page.waitForFunction(()=>document.querySelector('[data-testid="manufacturer-stop"]')?.textContent==='终止');
 await page.locator('[data-obd-tab="topology"]').click();await finish();await page.waitForTimeout(400);await page.locator('[data-obd-tab="live"]').click();
 await page.waitForFunction(id=>document.querySelector('[data-testid="offline-realtime"]')?.dataset.profileId===id,model.profileId);await page.waitForFunction(()=>!document.querySelector('[data-testid="eng-system"]')?.disabled);
 assert.equal(await count(),5);assert.equal(await page.getByTestId('manufacturer-save').isDisabled(),true);
 await page.waitForFunction(()=>document.querySelectorAll('[data-testid="offline-parameters"] input').length>0);
 assert.ok(await params.evaluateAll(xs=>xs.every(x=>!x.disabled)), 'matched parameters remain selectable after returning');
 await page.screenshot({path:path.join(output,'identified-directory.png'),fullPage:true});checks.push('browsing another page does not cancel discovery; returning preserves the matched directory without a second request');
 if(generation==='982'){
  if(ecuId===1){
    assert.equal(await page.getByTestId('eng-data-source').count(),0);
  }
  for(const id of captureModel.parameterIds)await page.getByTestId('offline-select-'+id).check();
  await app.evaluate((_electron,captureModel)=>{globalThis.__identity.captureModel=captureModel;globalThis.__identity.captureFinished=false;},captureModel);
  await page.getByTestId('manufacturer-read').click();
  await page.waitForFunction(count=>document.querySelector('[data-testid="manufacturer-state"]')?.textContent.includes(count+' 个数值'),captureModel.frames[0].result.samples.length);
   assert.equal(await page.getByTestId('offline-selection').isVisible(),true);
   assert.equal(await page.getByTestId('offline-selection').locator('summary').count(),0);
   assert.ok(await params.evaluateAll(xs=>xs.every(x=>x.disabled)));
  const captureRequest=await app.evaluate(()=>globalThis.__identity.reads.at(-1));
  assert.equal(captureRequest.generation,'982');assert.equal(captureRequest.profileId,captureModel.profileId);
  assert.deepEqual(captureRequest.parameterIds,captureModel.parameterIds);
  assert.equal(await page.locator('select[data-testid=eng-generation]').count(),0);
  const sample=captureModel.frames[0].result.samples.find(sample=>sample.numeric===false)??captureModel.frames[0].result.samples[0];
  assert.ok(sample);
  if(sample.numeric){
   const expected=mainlandParameter(Number(sample.value),sample.unit);
   await page.waitForFunction(({id,value,unit})=>{
    const text=document.querySelector('[data-parameter-id="'+id+'"] p')?.textContent?.split('：').at(-1);
    const number=text?.match(/^(-?\d+(?:\.\d+)?(?:e[+-]?\d+)?)(?=\s|（)/i);
    return number && Math.abs(Number(number[1])-value)<=Math.max(1,Math.abs(value))*0.00001 && (!unit || text.includes(unit));
   },{id:sample.parameterId,value:expected.value,unit:expected.unit});
  }else await page.locator('[data-parameter-id="'+sample.parameterId+'"]').filter({hasText:sample.display}).waitFor();
  await app.evaluate(()=>{globalThis.__identity.captureFinished=true;});
  await page.waitForFunction(()=>document.querySelector('[data-testid="manufacturer-state"]')?.textContent.includes('本轮已完成'));
  assert.equal(await page.getByTestId('manufacturer-save').isDisabled(),false);
  checks.push('982 collection renders actual Python model samples, locks the platform while running, and retains the completed batch for saving');
  await connected(false);
  await page.waitForFunction(()=>!document.querySelector('[data-testid="offline-realtime"]')?.dataset.profileId);
  const before=await count();assert.equal(await page.getByTestId('eng-generation').count(),0);
  assert.equal(await page.getByTestId('manufacturer-save').isDisabled(),false);
  assert.ok(await page.locator('[data-parameter-id]').count()>0);
  const otherVehicle=JSON.parse(fs.readFileSync(vehicleModelFile('981'),'utf8')).frames.at(-1).result;
  await app.evaluate((_electron,result)=>{const s=globalThis.__identity;s.vehicleResult=result;s.result={ok:false,error:'identity-mismatch'};},otherVehicle);
  await connected(true);
  await page.getByTestId('manufacturer-identify-retry').waitFor();
  assert.equal(await params.count(),0);
  assert.equal(await page.getByTestId('offline-parameters').locator('input:checked').count(),0);
  assert.equal(await page.getByTestId('offline-identity-state').count(),1);assert.equal(await count(),before+1);
  assert.equal(await page.locator('[data-parameter-id]').count(),0);
  assert.ok(await app.evaluate(()=>globalThis.__identity.discards?.includes('collection-model')));
  checks.push('982 keeps its explicit scope through identity and collection; disconnect invalidates identity but retains the saved batch; recognizing another vehicle clears the old values and selection');
 }
 if([88,76,78,30,31].includes(ecuId)){
  const before=await count();await connected(false);
  await page.waitForFunction(()=>!document.querySelector('[data-testid="offline-realtime"]')?.dataset.profileId);
  const rawModels=path.resolve(process.env.PORSCHE981_RAW_MODELS||process.env.PORSCHE981_MODE50_MODELS||path.join(source,'.local/obd-mode50-vnci-20261017/models',generation));
  const rawModel=JSON.parse(fs.readFileSync(path.join(rawModels,'identity-model-vnci-'+ecuId+'.json'),'utf8'));
  assert.equal(rawModel.syntheticPort,true);assert.equal(rawModel.deviceIO,false);assert.equal(rawModel.vehicleIO,false);
  assert.ok(rawModel.frames.filter(f=>f.event==='diagnostic-pdu').every(f=>f.result.pdu.transportKind===(ecuId===88?'d-pdu-mode50':'d-pdu-mode30')));
  await app.evaluate((_electron,result)=>{const s=globalThis.__identity;Object.assign(s,
   {model:'VNCI',connected:true,linkState:'connected',phase:'running',released:false,result});},rawModel.frames.at(-1).result);
  await page.waitForFunction(()=>document.querySelector('[data-testid="manufacturer-stop"]')?.textContent==='终止');
  assert.equal(await count(),before+1);
  assert.ok(await params.evaluateAll(xs=>xs.every(x=>x.disabled)));
  await app.evaluate(()=>{Object.assign(globalThis.__identity,{phase:'finished',released:true,connected:true,linkState:'connected'});});
  await page.waitForFunction(id=>document.querySelector('[data-testid="offline-realtime"]')?.dataset.profileId===id,rawModel.profileId);
  await page.waitForFunction(()=>!!document.querySelector('[data-testid="offline-parameters"] input:not(:disabled)'));
  await params.first().check();assert.equal(await page.getByTestId('manufacturer-read').isDisabled(),false);
  checks.push('VNCI source transport waits for fresh source identity and confirmed release, then enables the matched catalogue and manual read');
 }
 await connected(false);
 await page.waitForFunction(()=>!document.querySelector('[data-testid="offline-realtime"]')?.dataset.profileId);
 const beforeUnknown=await count();
 await app.evaluate(()=>{const s=globalThis.__identity;s.vehiclePhase='finished';s.vehicleResult={ok:false,error:'vehicle-model-unidentified'};});
 await connected(true);await page.getByTestId('vehicle-identify-retry').waitFor();
 assert.equal(await page.getByTestId('eng-generation').count(),0);
 assert.equal(await count(),beforeUnknown);assert.equal(await page.getByTestId('manufacturer-read').isDisabled(),true);
 assert.ok(await params.evaluateAll(xs=>xs.every(x=>x.disabled)));
 checks.push('an unidentified vehicle cannot start unit discovery, select parameters or bypass recognition through the visible directory');
 assert.deepEqual(errors,[]);passed=true;
}finally{
 clearTimeout(watchdog);await fs.promises.writeFile(path.join(output,'result.json'),JSON.stringify({passed,checks,errors,moduleRoot:root,pythonIdentityOutputReal:true,connectionAndIPCExplicitlyModeled:true,deviceIO:false,vehicleIO:false},null,2)+'\n');
 await app?.close();if(passed)fs.rmSync(temp,{recursive:true,force:true});
}
console.log('PASS version identification UI: '+checks.length+' checks, no device I/O');
