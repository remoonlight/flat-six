/** Real renderer/preload/controller/storage, synthetic owned transport results. */
import {_electron as electron} from 'playwright';
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import path from 'node:path';
const cabinTarget=['parking','cluster','airbag','hvac','eps','clock'].includes(process.env.PORSCHE981_CODING_UI_ECU)?process.env.PORSCHE981_CODING_UI_ECU:null;
const explicitModel=process.env.PORSCHE981_CODING_UI_MODEL;
const chassisTarget=['psm','epb','pasm'].includes(process.env.PORSCHE981_CODING_UI_ECU)?process.env.PORSCHE981_CODING_UI_ECU:null;
const bodyTarget=['bcm-front','bcm-rear'].includes(process.env.PORSCHE981_CODING_UI_ECU)?process.env.PORSCHE981_CODING_UI_ECU:null;
const root=path.resolve(import.meta.dirname,'..'),gateway=process.env.PORSCHE981_CODING_UI_ECU==='gateway',dme=process.env.PORSCHE981_CODING_UI_ECU==='dme',pdk=process.env.PORSCHE981_CODING_UI_ECU==='pdk',ecu=(explicitModel?process.env.PORSCHE981_CODING_UI_ECU:null)||cabinTarget||chassisTarget||bodyTarget|| (pdk?'pdk':dme?'dme':gateway?'gateway':'headlamp-left');
const task=path.resolve(cabinTarget?'.local/obd-cabin-coding-source-20261018':chassisTarget?'.local/obd-chassis-coding-source-20261018':bodyTarget?'.local/obd-body-coding-source-20261018':pdk?'.local/obd-pdk-coding-source-20261018':dme?'.local/obd-dme-coding-source-20261018':gateway?'.local/obd-gateway-coding-source-20261018':'.local/obd-coding-desktop-20261018');
const appRoot=process.env.PORSCHE981_ACCEPT_APP_ROOT && path.resolve(process.env.PORSCHE981_ACCEPT_APP_ROOT);
const out=path.resolve(process.env.PORSCHE981_ACCEPT_OUTPUT||path.join(task,'source-ui'));
await fs.mkdir(out,{recursive:true});const temp=await fs.mkdtemp(path.join(out,'isolated-')),desktop=path.join(temp,'Desktop');await fs.mkdir(desktop);
const fixture=path.join(temp,'devices.json');await fs.writeFile(fixture,JSON.stringify({devices:[],errors:[]}));
const model=JSON.parse(await fs.readFile(explicitModel||path.join(task,cabinTarget?({parking:'source-models/models/cabin-00-981-18-elm.json',cluster:'source-models/models/cabin-06-981-8-elm.json',airbag:'source-models/models/cabin-14-981-4-elm.json',hvac:'source-models/models/cabin-19-981-7-elm.json',eps:'source-models/models/cabin-31-981-69-elm.json',clock:'source-models/models/cabin-36-981-29-elm.json'}[cabinTarget]):chassisTarget?({psm:'source-models/models/chassis-00-981-5-elm.json',epb:'source-models/models/chassis-08-981-50-elm.json',pasm:'source-models/models/chassis-09-981-19-elm.json'}[chassisTarget]):bodyTarget?(bodyTarget==='bcm-front'?'source-models/models/body-00-981-32-elm.json':'source-models/models/body-08-981-35-elm.json'):pdk?'source-models/models/pdk-00-981-elm.json':dme?'source-models/models/dme-00-981-elm.json':gateway?'source-gateway-models/models/gateway-00-981-elm.json':'source-models/models/coding-00-981-47-elm.json')));
const env={...process.env,PORSCHE981_HEADLESS:'1',PORSCHE981_OBD_SMOKE:'1',PORSCHE981_SESSION_DENY_LIVE:'1',
 PORSCHE981_USER_DATA:path.join(temp,'user'),PORSCHE981_DB:path.join(temp,'ui.db'),PORSCHE981_LOCAL_ROOT:path.join(temp,'assets'),
 PORSCHE981_CONNECTION_FIXTURE:fixture,PORSCHE981_CONNECTION_STATE:path.join(temp,'connection.json')};delete env.ELECTRON_RUN_AS_NODE;delete env.VITE_DEV_SERVER_URL;
let app,passed=false;const errors=[],checks=[];
const timer=setTimeout(()=>{console.error('complete coding UI timeout');void app?.evaluate(({app})=>app.exit(1));},90000);
try{
 app=await electron.launch({args:[appRoot||path.join(root,'apps/desktop')],env,timeout:30000});const page=await app.firstWindow();page.setDefaultTimeout(15000);page.on('pageerror',e=>errors.push(e.message));
 await page.locator('nav.side [data-tab="obd"]').click();await page.locator('[data-obd-tab="coding"]').click();await page.locator(`[data-coding-system="${ecu}"]`).click();
 await page.getByTestId('coding-read-original').filter({hasText:/^读取原车设码$/}).waitFor();
 assert.deepEqual(await page.evaluate(()=>window.porsche981.codingWorkspace({action:'capability',ecu:'whole-vehicle'})),{ok:true,readReady:false,backupReady:false});
 assert.equal((await page.evaluate(()=>window.porsche981.codingWorkspace({action:'read',ecu:'headlamp-left',dataHex:'FF'}))).error,'coding-workspace-invalid');
 checks.push('production IPC enables only the implemented unit reader and rejects caller-supplied bytes');
 await app.evaluate(async({ipcMain},{root,model,directory,desktop})=>{
  const vm=process.getBuiltinModule('vm'),{pathToFileURL}=process.getBuiltinModule('url');
  const load=name=>vm.runInThisContext(`import(${JSON.stringify(pathToFileURL(root+'/apps/desktop/electron/'+name).href)})`,{importModuleDynamically:vm.constants.USE_MAIN_CONTEXT_DEFAULT_LOADER});
  const {createCodingWorkspace}=await load('coding-workspace.mjs'),{validateCompleteCodingRead,validateCodingVehicleAnchor}=await load('coding-read-evidence.mjs');
  let observed=new Map(),anchor;
  const add=p=>{if(p.duringCleanup)return;const a=observed.get(p.requestHex)||[];a.push(p.pduHex);observed.set(p.requestHex,a);};
  if(model.frames?.some(f=>f.event==='coding-vehicle-anchor')){
   for(const frame of model.frames){
    if(frame.event==='diagnostic-pdu')add(frame.result.pdu);
    else if(frame.event==='coding-vehicle-anchor'){
     anchor=validateCodingVehicleAnchor(frame.result,observed,model.result.vehicleGeneration,model.result.vehicleIdentitySha256);observed=new Map();
    }
   }
  }else for(const p of model.pdus)add(p);
  const identity=validateCompleteCodingRead(model.result,observed,anchor),state={desktopFailure:true,readFailure:false,unsupportedVersion:false,reads:0};globalThis.__codingReadUi=state;
  const controller=createCodingWorkspace({directory,desktopDirectory:()=>state.desktopFailure?desktop+'/missing':desktop,
   readClient:async()=>{state.reads++;return state.unsupportedVersion?{ok:false,error:'coding-complete-scope-unqualified'}:state.readFailure?{ok:false}:{ok:true,result:structuredClone(model.result),identity,releaseVerified:true};}});
  ipcMain.removeHandler('diagnostics:codingWorkspace');ipcMain.handle('diagnostics:codingWorkspace',(e,r)=>controller.handle(r,e.sender.id));
 },{root:appRoot||root,model,directory:path.join(temp,'coding-store'),desktop});
 await page.getByTestId('coding-read-original').click();await page.getByTestId('coding-original-notice').filter({hasText:'备份未完成'}).waitFor();assert.equal(await page.locator('[data-coding-live-field]').count(),0);
 assert.deepEqual(await fs.readdir(desktop),[]);checks.push('failed first Desktop publication exposes no values or edit controls');
 await app.evaluate(()=>{__codingReadUi.desktopFailure=false;});await page.getByTestId('coding-backup-original').click();await page.getByTestId('coding-original-notice').filter({hasText:'已备份'}).waitFor();
 assert.equal(await page.locator('[data-coding-live-field]').count(),0);const files=await fs.readdir(desktop);assert.equal(files.length,1);const original=await fs.readFile(path.join(desktop,files[0]));
 await page.getByTestId('coding-read-original').click();await page.getByTestId('coding-live-fields').waitFor();
 assert.equal(await page.locator('[data-coding-live-field]').count(),model.result.codingRead.definitionFieldCount);assert.equal(await page.locator('[data-coding-choice]').count(),0);
 assert.match(await page.getByTestId('coding-restore-original').innerText(),/未完成/);assert.equal(await page.getByTestId('coding-confirm-write').count(),0);
 checks.push('backup only saves; complete read shows all source fields/current values with no write or restore qualification');
 const firstField=model.result.codingRead.fields[0];await page.getByTestId('coding-live-search').fill(firstField.name);assert.ok(await page.locator('[data-coding-live-field]').count()>0);await page.getByTestId('coding-live-search').fill('');
 await page.getByTestId('coding-live-group').selectOption(firstField.group);assert.equal(await page.locator('[data-coding-live-field]').count(),model.result.codingRead.definitionFieldCount);
 assert.deepEqual(await fs.readdir(desktop),files);assert.deepEqual(await fs.readFile(path.join(desktop,files[0])),original);checks.push('source field search/group and later reads reuse the immutable first Desktop file');
 await app.evaluate(()=>{__codingReadUi.readFailure=true;});await page.getByTestId('coding-read-original').click();await page.getByTestId('coding-previous-read').waitFor();assert.equal(await page.locator('[data-coding-live-field]').count(),model.result.codingRead.definitionFieldCount);
 checks.push('failed reread preserves previous values marked as old and cannot open writing');
 if(gateway||dme||pdk||bodyTarget||chassisTarget||cabinTarget||explicitModel){await app.evaluate(()=>{__codingReadUi.readFailure=false;__codingReadUi.unsupportedVersion=true;});await page.getByTestId('coding-read-original').click();await page.getByTestId('coding-original-notice').filter({hasText:'尚未完成'}).waitFor();assert.deepEqual(await fs.readdir(desktop),files);assert.deepEqual(await fs.readFile(path.join(desktop,files[0])),original);checks.push('unqualified versions show incomplete client status, preserve prior values and create no backup');}
 await page.screenshot({path:path.join(out,'coding-read.png'),fullPage:false});assert.deepEqual(errors,[]);passed=true;
}finally{clearTimeout(timer);await fs.writeFile(path.join(out,'result.json'),JSON.stringify({passed,checks,errors,syntheticTransport:true,deviceIO:false,vehicleIO:false},null,2)+'\n');await app?.close();}
console.log('PASS complete coding read UI',checks.length,'checks; synthetic transport only');
