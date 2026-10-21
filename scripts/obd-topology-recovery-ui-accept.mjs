/** Production topology recovery + explicit IPC models; no head/vehicle IO. */
import {_electron as electron} from 'playwright';
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import {createHash} from 'node:crypto';
import path from 'node:path';
const root=path.resolve(import.meta.dirname,'..'),output=path.resolve(process.env.PORSCHE981_ACCEPT_OUTPUT || '.local/obd-dtc-source-client-20261017/recovery-ui');
await fs.mkdir(output,{recursive:true});const temp=await fs.mkdtemp(path.join(output,'isolated-'));
const devices=path.join(temp,'devices.json');await fs.writeFile(devices,JSON.stringify({devices:[],errors:[]}));
const env={...process.env,PORSCHE981_HEADLESS:'1',PORSCHE981_OBD_SMOKE:'1',PORSCHE981_SESSION_DENY_LIVE:'1',
 PORSCHE981_USER_DATA:path.join(temp,'user'),PORSCHE981_DB:path.join(temp,'garage.db'),PORSCHE981_LOCAL_ROOT:path.join(temp,'assets'),
 PORSCHE981_CONNECTION_STATE:path.join(temp,'connection.json'),PORSCHE981_CONNECTION_FIXTURE:devices};
delete env.ELECTRON_RUN_AS_NODE;delete env.VITE_DEV_SERVER_URL;
let app,passed=false;const checks=[],errors=[];
const timer=setTimeout(()=>{console.error('topology recovery total timeout');void app?.close();},150000);
try {
 app=await electron.launch({args:[process.env.PORSCHE981_ACCEPT_APP_ROOT || path.join(root,'apps/desktop')],env,timeout:30000});
 const page=await app.firstWindow();page.setDefaultTimeout(16000);page.on('pageerror',error=>errors.push(error.message));
 await app.evaluate(({ipcMain})=>{
  const s=globalThis.__scanRecovery={calls:[],legacy:[],jobs:{},next:0,failOnce:true,holdFailure:true,holdClear:false,clearMode:false,code:'0011',
   connection:{ok:true,selectedDeviceId:'bt:AABBCCDDEEFF',model:'vLinker',purpose:'diagnostic',connected:true,linkState:'connected',
    devices:[{id:'bt:AABBCCDDEEFF',brand:'vLinker',available:true,paired:true}],voltageVolts:12.3}};
  const faults=ecu=>ecu===1?[{displayCode:'P'+s.code,dtcHex:s.code,statusHex:'28',originalEnglish:'Original engine description',observedStatusText:'当前故障'}]:
   ecu===9?[{displayCode:'C13002',dtcHex:'C13002',statusHex:'09',originalEnglish:'Original gateway description',observedStatusText:'历史故障'}]:[];
  const connection=req=>{
   if(req.action==='connect'){s.connection.connected=false;s.connection.linkState='connecting';}
   else if(req.action==='status'&&s.connection.linkState==='connecting'){s.connection.connected=true;s.connection.linkState='connected';}
   return {...s.connection};
  };
  ipcMain.removeHandler('diagnostics:connection');ipcMain.handle('diagnostics:connection',(_event,req)=>connection(req));
  ipcMain.removeHandler('diagnostics:manufacturerStream');ipcMain.handle('diagnostics:manufacturerStream',(_event,req)=>{
   s.calls.push(req);
   if(['identify-vehicle','read-dtc'].includes(req.action)){
    if(req.action==='read-dtc'&&s.failureMode){s.rawErrors ||= [];s.rawErrors.push(s.privateError);
     if(s.failureMode==='throw')throw new Error(s.privateError);return {ok:false,error:s.privateError};}
    const sessionId='source-fixture-'+(++s.next);s.jobs[sessionId]={...req,sessionId,failed:req.action==='read-dtc'&&req.ecuId===1&&s.failOnce};
    if(s.jobs[sessionId].failed)s.failOnce=false;
    return {ok:true,sessionId,state:'running',released:false};
   }
   const job=s.jobs[req.sessionId];if(!job)return {ok:false,error:'unknown_session'};
   if(req.action==='stop'){job.cancelled=true;return {ok:true};}
   if(req.action!=='status')throw new Error('unplanned fixture action');
   const released=!job.failed || !s.holdFailure;
   if(job.failed&&released){s.connection.connected=false;s.connection.linkState='retry-required';s.connection.connectionError='disconnect';}
   const pid=job.ecuId===1?'9x1:DME_BDE_Continental:SDI9_1_981_3_4L_EU5':'9x1:CAN_CAN_Gateway:CAN_CAN_Gateway_A7_1';
   return {ok:true,sessionId:job.sessionId,state:'finished',released,releaseVerified:released,error:null,
    result:{ok:!job.failed&&!job.cancelled,error:job.cancelled?'cancelled':job.failed?'port-io:lost':null,
     vehicleGeneration:'981',vehicleIdentitySha256:'a'.repeat(64),identifiedProfileId:pid,
     transport:{qualification:{observedProfileMatch:true,freshRead:true,sourceCatalogId:'fixture-source',generation:'981',variantLink:{profile_id:pid}},closeErrors:[]},
     dtcRead:{decoded:{ok:true,type:job.ecuId===1?'kwp-dtc-18':'uds-dtc-19-02',records:faults(job.ecuId)}}}};
  });
  ipcMain.removeHandler('diagnostics:session');ipcMain.handle('diagnostics:session',(_event,req)=>{
   if(req.action==='overview')return {ok:true,taskState:'idle',voltageVolts:12.3};
   if(req.action==='start'){
    s.legacy.push(req);const jobId='legacy-'+(++s.next);s.jobs[jobId]={...req,jobId};return {ok:true,jobId};
   }
   if(req.action==='cancel')return {ok:true};
   if(req.action!=='status')throw new Error('unexpected legacy action');
   const job=s.jobs[req.jobId],ecu=job.profileId.endsWith('gateway')?9:1,clear=job.sessionTask==='clear';
   if(clear&&!s.holdClear){s.connection.connected=false;s.connection.linkState='retry-required';}
   return {ok:!clear,jobId:job.jobId,state:clear?'failed':'completed',released:!clear||!s.holdClear,releaseVerified:!clear||!s.holdClear,
    error:clear?'disconnect':null,final:{ok:!clear,status:clear?'failed':'completed',error:clear?'disconnect':null,mode:job.mode,
     profileId:job.profileId,simulation:false,identityQualification:{observedProfileMatch:true},results:[
      {role:'identity',field:'hardware',ok:true,decoded:{ok:true,text:'TEST-HW'}},
      {role:'dependent',operationId:ecu===9?'gw-vin':'dme-vin',ok:true,freshlyRead:true,historical:false,decoded:{ok:true,type:'vin-ascii',text:'WP0ZZZ98ZES000000'}},
      {role:'dependent',operationId:ecu===9?'gw-dtc':'dme-dtc',ok:true,freshlyRead:true,historical:false,
       decoded:{ok:true,type:ecu===9?'uds-dtc-19-02':'kwp-dtc-18',records:faults(ecu)}},
     ]}};
  });
 });
 await page.locator('nav.side [data-tab="obd"]').click();await page.locator('[data-obd-tab="topology"]').click();
 await page.getByTestId('topo-read-all').waitFor();await page.waitForFunction(()=>!document.querySelector('[data-testid="topo-read-all"]').disabled);
 await page.getByTestId('topo-read-all').click();await page.getByTestId('topo-release-wait').waitFor();
 assert.equal(await page.getByTestId('topo-recovery').count(),0);assert.equal(await page.getByTestId('topo-read-all').isDisabled(),true);
 assert.equal(await page.getByTestId('topo-node-gateway').first().getAttribute('data-kind'),'dtc-present');
 await app.evaluate(()=>{globalThis.__scanRecovery.holdFailure=false;});await page.getByTestId('topo-recovery').waitFor();
 assert.equal(await page.getByTestId('topo-retry').isEnabled(),true);
 await page.getByTestId('topo-retry').click();
 await page.waitForFunction(()=>!document.querySelector('[data-testid="topo-read-all"]').disabled);
 const reads=await app.evaluate(()=>globalThis.__scanRecovery.calls.filter(x=>x.action==='read-dtc'));
 assert.equal(reads.filter(x=>x.ecuId===9).length,1);assert.equal(reads.filter(x=>x.ecuId===1).length,2);
 assert.equal(new Set(reads.map(x=>x.ecuId)).size,29);assert.ok(reads.every(x=>x.expectedVehicleIdentitySha256==='a'.repeat(64)));
 checks.push('whole-car source read waits for release, retries only unfinished units with fresh vehicle binding, retains gateway results and completes 29 units');
 await page.getByTestId('topo-node-dme').first().click();
 assert.match(await page.getByTestId('topo-fault-info').innerText(),/P0011.*气缸组 1.*凸轮轴延迟.*当前故障/);
 await app.evaluate(()=>{globalThis.__scanRecovery.code='0502';});await page.getByTestId('topo-read-selected').click();
 await page.waitForFunction(()=>!document.querySelector('[data-testid="topo-read-selected"]').disabled);
 assert.match(await page.getByTestId('topo-fault-info').innerText(),/P0502.*车速信号 1 - 信号过低/);
 assert.doesNotMatch(await page.getByTestId('topo-fault-info').innerText(),/[\u3400-\u4dff\ufffd]/);
 checks.push('source DME records retain factory Chinese titles, punctuation and explicit fault status');
 await page.getByTestId('topo-node-gateway').first().click();await app.evaluate(()=>{globalThis.__scanRecovery.holdClear=true;});
 const prior=await app.evaluate(()=>globalThis.__scanRecovery.legacy.length);
 await page.getByTestId('topo-clear-all').click();await page.getByTestId('topo-release-wait').waitFor();
 assert.equal(await page.getByTestId('topo-recovery').count(),0);assert.equal(await page.getByTestId('topo-stop-read').count(),0);
 await app.evaluate(()=>{globalThis.__scanRecovery.holdClear=false;});await page.getByTestId('topo-recovery').waitFor();
 assert.match(await page.getByTestId('topo-recovery').innerText(),/原范围的 2 个/);await page.getByTestId('topo-retry').click();
 await page.waitForFunction(()=>!document.querySelector('[data-testid="topo-read-all"]').disabled);
 const legacy=await app.evaluate(()=>globalThis.__scanRecovery.legacy);const verification=legacy.slice(prior);
 assert.deepEqual(verification.map(x=>x.sessionTask),['clear','read','read']);
 assert.deepEqual(verification.slice(1).map(x=>x.profileId),['porsche-981-2014-gateway','porsche-981-2014-dme']);
 assert.ok(verification.slice(1).every(x=>x.confirmedReadOnly===true&&x.confirmedClearDtc===undefined));
 assert.match(await page.getByTestId('topo-progress').innerText(),/清除响应未确认.*重新读取核对/s);
 checks.push('uncertain clear retains the two-unit write scope, waits for release and performs only original-range legacy readback after manual reconnect');
 // Independent IPC-failure cases start with an explicitly connected fixture.
 await app.evaluate(()=>Object.assign(globalThis.__scanRecovery.connection,{connected:true,linkState:'connected',connectionError:null}));
 await page.getByTestId('topo-node-dme').first().click();
 const privateError='PRIVATE_TOPOLOGY_DETAIL C:\\not-a-real-path\\trace.log RAW_STACK_TEST';
 for(const [index,mode] of ['return','throw'].entries()) {
  await app.evaluate((_electron,{mode,privateError})=>Object.assign(globalThis.__scanRecovery,{failureMode:mode,privateError}),{mode,privateError});
  await page.getByTestId('topo-read-selected').click();
  const end=Date.now()+12000;
  while((await app.evaluate(()=>globalThis.__scanRecovery.rawErrors?.length || 0))<index+1 && Date.now()<end)await page.waitForTimeout(80);
  if((await app.evaluate(()=>globalThis.__scanRecovery.rawErrors?.length || 0))<index+1)
   await fs.writeFile(path.join(output,'failure-state.json'),JSON.stringify({fixture:await app.evaluate(()=>globalThis.__scanRecovery),body:await page.locator('body').innerText()},null,2));
  assert.equal(await app.evaluate(()=>globalThis.__scanRecovery.rawErrors?.length || 0),index+1,'source error action actually reached the IPC fixture');
  await page.waitForFunction(()=>!document.querySelector('[data-testid="topo-read-selected"]').disabled);
  assert.doesNotMatch(await page.locator('[data-page="obd"]').innerText(),/PRIVATE_TOPOLOGY_DETAIL|not-a-real-path|RAW_STACK_TEST/);
  assert.doesNotMatch(await page.getByTestId('topo-fault-info').innerText(),/故障码：0/);
 }
 assert.deepEqual(await app.evaluate(()=>globalThis.__scanRecovery.rawErrors),[privateError,privateError]);
 checks.push('returned and thrown source-start errors retain backend details, expose no private paths and never display zero faults');
 assert.deepEqual(errors,[]);await page.screenshot({path:path.join(output,'recovery.png')});passed=true;
} finally {
 clearTimeout(timer);await app?.close();
 const moduleRoot=path.resolve(process.env.PORSCHE981_ACCEPT_APP_ROOT || root),sourceCode={};
 const names=['apps/desktop/dist/index.html',...(await fs.readdir(path.join(moduleRoot,'apps/desktop/dist/assets'))).map(name=>'apps/desktop/dist/assets/'+name)];
 for(const name of names)sourceCode[name]=createHash('sha256').update(await fs.readFile(path.join(moduleRoot,name))).digest('hex');
 await fs.writeFile(path.join(output,'result.json'),JSON.stringify({passed,checks,errors,moduleRoot,sourceCode,deviceIO:false,vehicleIO:false},null,2)+'\n');
 if(passed)await fs.rm(temp,{recursive:true,force:true});
}
console.log(`PASS ${checks.length} topology source/clear recovery UI checks`);
