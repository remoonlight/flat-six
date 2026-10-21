/** Production topology renderer with complete source worker results; IPC is modeled. */
import {_electron as electron} from 'playwright';
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import path from 'node:path';
import {createHash} from 'node:crypto';

const source=path.resolve(import.meta.dirname,'..'),root=path.resolve(process.env.PORSCHE981_ACCEPT_APP_ROOT || source);
const output=path.resolve(process.env.PORSCHE981_ACCEPT_OUTPUT || '.local/obd-headlamp-identity-20261020/integration/ui');
const directory=path.resolve(process.env.PORSCHE981_DTC_MODELS || '.local/obd-headlamp-identity-20261020/integration/models');
await fs.mkdir(output,{recursive:true});
const candidates=[];
for(const name of await fs.readdir(directory)) {
  if(!/^dtc-worker-\d+-\d+-elm\.json$/.test(name))continue;
  const file=path.join(directory,name),bytes=await fs.readFile(file),model=JSON.parse(bytes);
  if(!model.profileId.startsWith('9x1:SCHEINWERFER_LED:'))continue;
  assert.equal(model.vehicleIO,false);assert.equal(model.deviceIO,false);
  const doc=model.frames.at(-1).result;assert.equal(doc.ok,true);assert.equal(doc.transport.closed,true);
  candidates.push({name,sha256:createHash('sha256').update(bytes).digest('hex'),doc});
}
assert.equal(new Set(candidates.map(c=>c.doc.identifiedProfileId)).size,4);
assert.equal(new Set(candidates.map(c=>c.doc.vehicleGeneration)).size,2);
assert.equal(candidates.length,8);
const temp=await fs.mkdtemp(path.join(output,'isolated-')),fixture=path.join(temp,'devices.json');
await fs.writeFile(fixture,JSON.stringify({devices:[],errors:[]}));
const env={...process.env,PORSCHE981_HEADLESS:'1',PORSCHE981_OBD_SMOKE:'1',PORSCHE981_SESSION_DENY_LIVE:'1',
  PORSCHE981_USER_DATA:path.join(temp,'user'),PORSCHE981_DB:path.join(temp,'garage.db'),PORSCHE981_LOCAL_ROOT:path.join(temp,'assets'),
  PORSCHE981_CONNECTION_STATE:path.join(temp,'connection.json'),PORSCHE981_CONNECTION_FIXTURE:fixture};
delete env.ELECTRON_RUN_AS_NODE;delete env.VITE_DEV_SERVER_URL;
let app,passed=false;const checks=[],errors=[],timer=setTimeout(()=>{void app?.close();},150000);
try {
  app=await electron.launch({args:[process.env.PORSCHE981_ACCEPT_APP_ROOT?root:path.join(root,'apps/desktop')],env,timeout:30000});
  const page=await app.firstWindow();page.setDefaultTimeout(15000);page.on('pageerror',error=>errors.push(error.message));
  await app.evaluate(({ipcMain})=>{
    const state=globalThis.__ledFault={doc:null,calls:[],jobs:{},next:0};
    ipcMain.removeHandler('diagnostics:connection');ipcMain.handle('diagnostics:connection',()=>({ok:true,
      selectedDeviceId:'bt:AABBCCDDEEFF',model:'vLinker',purpose:'diagnostic',connected:true,linkState:'connected',
      devices:[{id:'bt:AABBCCDDEEFF',brand:'vLinker',available:true,paired:true}],voltageVolts:12.3}));
    ipcMain.removeHandler('diagnostics:session');ipcMain.handle('diagnostics:session',(_event,request)=>{
      if(request.action==='overview')return{ok:true,taskState:'idle',voltageVolts:12.3};throw new Error('no legacy or write operation');});
    ipcMain.removeHandler('diagnostics:manufacturerStream');ipcMain.handle('diagnostics:manufacturerStream',(_event,request)=>{
      state.calls.push(request);
      if(['identify-vehicle','read-dtc'].includes(request.action)) {
        const sessionId='led-model-'+(++state.next);state.jobs[sessionId]={...request};
        return {ok:true,sessionId,state:'running',released:false};
      }
      const job=state.jobs[request.sessionId];if(!job)throw new Error('unknown modeled job');
      if(request.action!=='status')throw new Error('only manual fault reads allowed');
      job.finished=true;
      const doc=state.doc;
      const result=job.action==='identify-vehicle'?{ok:true,vehicleGeneration:doc.vehicleGeneration,
        vehicleIdentitySha256:doc.vehicleIdentitySha256,identityOnly:true}:doc;
      return {ok:true,sessionId:request.sessionId,state:'finished',released:true,releaseVerified:true,result,error:null};
    });
  });
  await page.locator('nav.side [data-tab="obd"]').click();await page.locator('[data-obd-tab="topology"]').click();
  await page.getByTestId('topo-node-headlamp-left').first().click();
  for(const item of candidates) {
    const doc=item.doc;
    await app.evaluate((_electron,doc)=>{globalThis.__ledFault.doc=doc;},doc);
    const before=await app.evaluate(()=>globalThis.__ledFault.calls.filter(c=>c.action==='read-dtc').length);
    await page.waitForFunction(()=>!document.querySelector('[data-testid="topo-read-selected"]').disabled);
    await page.getByTestId('topo-read-selected').click();
    let delivered=false;
    for(let i=0;i<200;i++) {
      delivered=await app.evaluate((_electron,before)=>{
        const s=globalThis.__ledFault;return s.calls.filter(c=>c.action==='read-dtc').length>before
          && Object.values(s.jobs).filter(job=>job.action==='read-dtc'&&job.finished).length>before;
      },before);
      if(delivered)break;await new Promise(resolve=>setTimeout(resolve,50));
    }
    assert.equal(delivered,true,item.name+' result delivered');
    await page.waitForFunction(()=>!document.querySelector('[data-testid="topo-read-selected"]').disabled);
    const info=await page.getByTestId('topo-fault-info').innerText();
    assert.ok(info.includes(doc.dtcRead.decoded.records[0].dtcHex),item.name+' original fault number');
    assert.equal(await page.getByTestId('topo-clear-selected').isDisabled(),true);
    assert.equal(await page.getByTestId('topo-unit-details').count(),0);
    assert.equal(doc.transport.qualification.observedProfileMatch,true,item.name+' source version retained');
    assert.ok(doc.identityInfo.length>0,item.name+' source identity retained');
    checks.push(item.name+': source identity retained, fault visible, no detail element or clear permission');
  }
  const calls=await app.evaluate(()=>globalThis.__ledFault.calls.filter(c=>c.action==='read-dtc'));
  assert.equal(calls.length,8);assert.ok(calls.every(c=>c.ecuId===47&&c.confirmedReadOnly===true&&c.x431Inactive===true));
  assert.deepEqual(errors,[]);await page.screenshot({path:path.join(output,'headlamp-fault.png')});passed=true;
} finally {
  clearTimeout(timer);await app?.close();
  await fs.writeFile(path.join(output,'result.json'),JSON.stringify({passed,checks,errors,models:candidates.map(({name,sha256})=>({name,sha256})),
    moduleRoot:root,ipcExplicitlyModeled:true,deviceIO:false,vehicleIO:false},null,2)+'\n');
  if(passed)await fs.rm(temp,{recursive:true,force:true});
}
console.log('PASS '+checks.length+' LED source-result topology UI contexts; no vehicle IO');
