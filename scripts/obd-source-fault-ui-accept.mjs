/** Production renderer + explicit IPC models. Does not open a head or vehicle. */
import {_electron as electron} from 'playwright';
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import {createHash} from 'node:crypto';
import path from 'node:path';
const root=path.resolve(import.meta.dirname,'..'),output=path.resolve(process.env.PORSCHE981_ACCEPT_OUTPUT || '.local/obd-dtc-source-client-20261017/source-ui');
await fs.mkdir(output,{recursive:true});const temp=await fs.mkdtemp(path.join(output,'isolated-'));
const devices=path.join(temp,'devices.json');await fs.writeFile(devices,JSON.stringify({devices:[],errors:[]}));
const env={...process.env,PORSCHE981_HEADLESS:'1',PORSCHE981_OBD_SMOKE:'1',PORSCHE981_SESSION_DENY_LIVE:'1',
 PORSCHE981_USER_DATA:path.join(temp,'user'),PORSCHE981_DB:path.join(temp,'garage.db'),PORSCHE981_LOCAL_ROOT:path.join(temp,'assets'),
 PORSCHE981_CONNECTION_STATE:path.join(temp,'connection.json'),PORSCHE981_CONNECTION_FIXTURE:devices};
delete env.ELECTRON_RUN_AS_NODE;delete env.VITE_DEV_SERVER_URL;
let app,passed=false;const checks=[],errors=[];
const timer=setTimeout(()=>{console.error('source fault UI timeout');void app?.close();},90000);
try {
 app=await electron.launch({args:[process.env.PORSCHE981_ACCEPT_APP_ROOT || path.join(root,'apps/desktop')],env,timeout:30000});
 const page=await app.firstWindow();page.setDefaultTimeout(12000);page.on('pageerror',error=>errors.push(error.message));
 await app.evaluate(({ipcMain})=>{
  const s=globalThis.__faultSource={calls:[],jobs:{},next:0,hold:false,failEcu:null,literal:false,wrongUnit:false,
   connection:{ok:true,selectedDeviceId:'bt:AABBCCDDEEFF',model:'vLinker',purpose:'diagnostic',connected:true,linkState:'connected',
    devices:[{id:'bt:AABBCCDDEEFF',brand:'vLinker',available:true,paired:true}],voltageVolts:12.3}};
  ipcMain.removeHandler('diagnostics:connection');ipcMain.handle('diagnostics:connection',()=>({...s.connection}));
  ipcMain.removeHandler('diagnostics:session');ipcMain.handle('diagnostics:session',(_event,req)=>{
   if(req.action==='overview')return {ok:true,taskState:'idle',voltageVolts:12.3};throw new Error('legacy fault/writes forbidden in this test');});
  ipcMain.removeHandler('diagnostics:manufacturerStream');ipcMain.handle('diagnostics:manufacturerStream',(_event,req)=>{
   s.calls.push(req);
   if(['identify-vehicle','read-dtc'].includes(req.action)){
    const sessionId='source-fixture-'+(++s.next);s.jobs[sessionId]={...req,sessionId};return {ok:true,sessionId,state:'running',released:false};}
   const job=s.jobs[req.sessionId];if(!job)return {ok:false,error:'unknown_session'};
   if(req.action==='stop'){job.cancelled=true;return {ok:true};}
   if(req.action!=='status')throw new Error('unplanned stream action');
   if(s.hold)return {ok:true,sessionId:job.sessionId,state:'running',released:false,releaseVerified:false};
   const error=job.cancelled?'cancelled':s.failEcu===job.ecuId?'port-io:lost':null;
   const literal=s.literal&&[35,47].includes(job.ecuId),pid=job.ecuId===35?'9x1:BCM_hinten:BCM_hinten_9x1_GT4':'9x1:SWSG:SWSG_Master_mit_AFS_ab_1310';
   const catalog='31bf93245fffc375b757fc19ba03491e101ccabc838c9ee742d70a88cb7805ba';
   const code=literal?(job.ecuId===35?'C13235':'C11004'):'C13002';
   return {ok:true,sessionId:job.sessionId,state:'finished',released:true,releaseVerified:true,error:null,
    result:{ok:!error,error,vehicleGeneration:'982',vehicleIdentitySha256:job.action==='read-dtc'&&s.faultHash?s.faultHash:'a'.repeat(64),
     identifiedProfileId:literal?pid:'fixture-source-'+job.ecuId,transport:{qualification:{observedProfileMatch:true,freshRead:true,
      ...(literal?{generation:'982',ecuId:s.wrongUnit?32:job.ecuId,sourceCatalogId:catalog,variantLink:{profile_id:pid}}:{})},closeErrors:[]},
     identityInfo:[{label:'软件版本',value:'1234'},{label:'硬件版本',value:'5678'}],
     dtcRead:{...(literal?{profileId:pid,ecuId:job.ecuId,generation:'982',sourceCatalogId:catalog}:{}),decoded:{ok:true,type:'uds-dtc-19-02',records:[{displayCode:code,dtcHex:code,statusHex:'09',originalEnglish:'Original fault description',...(literal?{manufacturerCode:true,dictionaryMatch:true}:{})}]}}}};
  });
 });
 await page.locator('nav.side [data-tab="obd"]').click();await page.locator('[data-obd-tab="topology"]').click();
 await page.getByTestId('topo-diagram').waitFor();await page.getByTestId('topo-node-psm').first().click();
 await page.getByTestId('topo-read-selected').waitFor({state:'visible'});
 await page.waitForFunction(()=>!document.querySelector('[data-testid="topo-read-selected"]').disabled);
 const reads=()=>app.evaluate(()=>globalThis.__faultSource.calls.filter(x=>x.action==='read-dtc'));
 assert.equal((await reads()).length,0);checks.push('opening and selecting a unit do not read vehicle faults');
 await page.getByTestId('topo-read-selected').click();
 await page.waitForFunction(()=>document.querySelector('[data-testid="topo-node-psm"]').getAttribute('data-kind')==='dtc-present');
 assert.match(await page.getByTestId('topo-fault-info').innerText(),/C13002.*Original fault description/);
 assert.equal((await reads())[0].ecuId,5);assert.equal((await reads())[0].expectedVehicleIdentitySha256,'a'.repeat(64));
 assert.equal(await page.getByTestId('topo-clear-selected').isDisabled(),true);checks.push('PSM manual fault read uses typed source entry, shows English fallback, and grants no clear permission');
 const beforeViewChange=(await reads()).length;
 assert.equal(await page.getByTestId('topo-unit-details').count(),0);
 assert.doesNotMatch(await page.locator('body').innerText(),/控制单元详情|软件版本\s+1234|硬件版本\s+5678/);
 await page.getByTestId('topo-view-list').click();
 assert.equal(await page.getByTestId('topo-unit-details').count(),0);
 assert.match(await page.getByTestId('topo-fault-info').innerText(),/C13002/);
 await page.getByTestId('topo-view-diagram').click();
 assert.equal((await reads()).length,beforeViewChange);
 checks.push('unit-detail element is absent in diagram and list views; fault results and vehicle-read count are preserved');
 await page.getByTestId('topo-node-gateway').first().click();
 await page.getByTestId('topo-read-all').click();
 await page.waitForFunction(()=>document.querySelector('[data-testid="topo-node-headlamp-sg"]').getAttribute('data-kind')==='dtc-present',{},{timeout:20000});
 await page.waitForFunction(()=>!document.querySelector('[data-testid="topo-read-all"]').disabled);
 const all=await reads();assert.equal(new Set(all.map(x=>x.ecuId)).size,35);assert.equal(all.some(x=>x.ecuId===47),true);assert.equal(all.some(x=>x.ecuId===48),true);
 checks.push('whole-car queue reads all 35 supported unit classes including both headlamps; write permissions stay unchanged');
 const count=all.length;
 assert.equal(await page.getByTestId('topo-unplaced').locator('.topo-bus,.topo-bus-track,.topo-vbus').count(),0);
 await page.getByTestId('topo-node-ecu-65').click();assert.match(await page.getByTestId('topo-fault-info').innerText(),/C13002/);
 await page.getByTestId('topo-view-list').click();await page.getByTestId('topo-list').getByRole('button',{name:'AMP 功放',exact:true}).waitFor();
 assert.equal((await reads()).length,count);await page.getByTestId('topo-view-diagram').click();
 assert.equal(await page.getByTestId('topo-node-ecu-65').getAttribute('aria-pressed'),'true');
 checks.push('amplifier, TV and emergency-call entries retain results across views without inventing bus placement or installation');
 await page.getByTestId('topo-node-gateway').first().click();
 await app.evaluate(()=>{globalThis.__faultSource.hold=true;});await page.getByTestId('topo-read-all').click();
 await page.getByTestId('topo-stop-read').click();await app.evaluate(()=>{globalThis.__faultSource.hold=false;});
 await page.waitForFunction(()=>!document.querySelector('[data-testid="topo-read-all"]').disabled);
 assert.match(await page.locator('body').innerText(),/读取已停止/);checks.push('stop completes the current owned read and retains results');
 await app.evaluate(()=>{globalThis.__faultSource.literal=true;});
 await page.getByTestId('topo-node-bcm-rear').first().click();await page.getByTestId('topo-read-selected').click();
 await page.getByTestId('topo-fault-info').filter({hasText:'Drive Air deflector'}).waitFor();
 assert.match(await page.getByTestId('topo-fault-info').innerText(),/C13235.*Drive Air deflector/);
 assert.equal(await page.getByTestId('topo-clear-selected').isDisabled(),true);
 await page.screenshot({path:path.join(output,'factory-literal-title.png')});
 const afterLiteral=(await reads()).length;
 await page.getByTestId('topo-view-list').click();await page.getByTestId('topo-view-diagram').click();
 assert.match(await page.getByTestId('topo-fault-info').innerText(),/Drive Air deflector/);assert.equal((await reads()).length,afterLiteral);
 checks.push('fresh source-bound rear-body faults resolve the factory title by the complete six-digit code; result and scope survive view changes without granting clear permission');
 await page.getByTestId('topo-node-headlamp-left').first().click();await page.getByTestId('topo-read-selected').click();
 await page.getByTestId('topo-fault-info').filter({hasText:'C11004'}).waitFor();
 assert.match(await page.getByTestId('topo-fault-info').innerText(),/Original fault description/);
 assert.doesNotMatch(await page.getByTestId('topo-fault-info').innerText(),/Control module Programming|Control unit, coded incorrectly/);
 await app.evaluate(()=>{globalThis.__faultSource.wrongUnit=true;});
 await page.getByTestId('topo-node-bcm-rear').first().click();await page.getByTestId('topo-read-selected').click();
 await page.getByTestId('topo-fault-info').filter({hasText:'Original fault description'}).waitFor();
 assert.doesNotMatch(await page.getByTestId('topo-fault-info').innerText(),/Drive Air deflector/);
 checks.push('ambiguous headlight titles and a mismatched unit qualification retain the original English fallback instead of borrowing another factory title');
 await app.evaluate(()=>{globalThis.__faultSource.wrongUnit=false;globalThis.__faultSource.faultHash='b'.repeat(64);});
 await page.getByTestId('topo-read-selected').click();
 await page.waitForFunction(()=>!document.querySelector('[data-testid="topo-read-selected"]').disabled);
 assert.match(await page.getByTestId('topo-fault-info').innerText(),/Original fault description/);
 assert.doesNotMatch(await page.getByTestId('topo-fault-info').innerText(),/Drive Air deflector/);
 checks.push('a fault result from a different vehicle fingerprint cannot acquire the current vehicle manual title');
 assert.deepEqual(errors,[]);await page.screenshot({path:path.join(output,'topology-source.png')});passed=true;
} finally {
 clearTimeout(timer);await app?.evaluate(({app})=>app.exit(0)).catch(()=>{});await app?.close().catch(()=>{});
 const moduleRoot=path.resolve(process.env.PORSCHE981_ACCEPT_APP_ROOT || root),sourceCode={};
 const names=['apps/desktop/dist/index.html',...(await fs.readdir(path.join(moduleRoot,'apps/desktop/dist/assets'))).map(name=>'apps/desktop/dist/assets/'+name)];
 for(const name of names)sourceCode[name]=createHash('sha256').update(await fs.readFile(path.join(moduleRoot,name))).digest('hex');
 await fs.writeFile(path.join(output,'result.json'),JSON.stringify({passed,checks,errors,moduleRoot,sourceCode,deviceIO:false,vehicleIO:false},null,2)+'\n');
 if(passed)await fs.rm(temp,{recursive:true,force:true});
}
console.log(`PASS ${checks.length} source topology renderer checks`);
