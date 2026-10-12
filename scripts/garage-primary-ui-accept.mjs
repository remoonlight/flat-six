/** Built desktop + bundled model/seed acceptance, isolated local state and SQLite. */
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import {_electron as electron} from 'playwright';
const root=path.resolve(import.meta.dirname,'..');
const output=fs.mkdtempSync(path.join(root,'.local','garage-primary-ui-'));
const local=path.join(output,'assets');
const seed=JSON.parse(fs.readFileSync(path.join(root,'data/seed/xray/garage-assemblies.json')));
const expectedLinks=JSON.parse(fs.readFileSync(path.join(root,'data/seed/xray/model-oem-links.seed.json')));
const expectedState=JSON.parse(fs.readFileSync(path.join(root,'data/seed/xray/mesh-state.template.json')));
const existingOem=process.argv.includes('--existing-oem');
if(existingOem){
  fs.mkdirSync(local,{recursive:true});
  fs.writeFileSync(path.join(local,'model-oem-links.json'),JSON.stringify({version:1,links:[]}));
}
for(const rel of [seed.bodyShell.rel,seed.bodyShell.fallbackRel,...seed.assemblies.map(a=>a.glbRel)]){
  const source=path.join(root,'.local',rel);if(!fs.existsSync(source))continue;
  fs.mkdirSync(path.dirname(path.join(local,rel)),{recursive:true});fs.copyFileSync(source,path.join(local,rel));
}
const manifest=JSON.parse(fs.readFileSync(path.join(root,'.local',seed.bodyShell.rel.replace(/\.glb$/,'.manifest.json'))));
assert.equal(manifest.composition,'saved-visible');assert.equal(manifest.supplementMeshes,0);
assert(Object.values(manifest.sourceHashes).every(h=>/^[a-f0-9]{64}$/.test(h)));
const expectedExterior=manifest.parts.filter(p=>p.category==='Exterior').length;
const expectedInterior=manifest.parts.filter(p=>p.category==='Cabin').length;
const env={...process.env,PORSCHE981_DB:path.join(output,'ui.db'),PORSCHE981_LOCAL_ROOT:local,
  PORSCHE981_USER_DATA:path.join(output,'user'),PORSCHE981_HEADLESS:'1',PORSCHE981_OBD_SMOKE:'1',PORSCHE981_SESSION_DENY_LIVE:'1'};
delete env.ELECTRON_RUN_AS_NODE;delete env.VITE_DEV_SERVER_URL;
const errors=[],checks=[];let app;const timer=setTimeout(()=>process.exit(1),180000);
async function state(page){return page.evaluate(()=>{
  const h=document.querySelector('.locator-glb');let f=h[Object.keys(h).find(k=>k.startsWith('__reactFiber$'))],rt;
  for(;f&&!rt;f=f.return)for(let s=f.memoizedState;s&&!rt;s=s.next)if(s.memoizedState?.current?.pickRoot?.traverse)rt=s.memoizedState.current;
  window.__rt=rt;const meshes=[];rt.pickRoot.traverse(o=>{if(o.isMesh)meshes.push({name:o.name,part:o.userData.garagePartName,
    mats:(Array.isArray(o.material)?o.material:[o.material]).map(m=>({reviewed:m.userData.garageInteriorSurface,xray:m.userData.garageXraySurface,color:m.color?.getHexString()}))});});return meshes;
});}
try{
  app=await electron.launch({args:[path.join(root,'apps/desktop')],env,timeout:30000});const page=await app.firstWindow();
  page.on('pageerror',e=>errors.push(e.message));page.on('console',m=>{if(m.type()==='error')errors.push(m.text())});
  await page.setViewportSize({width:1480,height:980});await page.waitForFunction(()=>document.querySelectorAll('[data-model-key]').length>100,null,{timeout:45000});
  const fresh=await page.evaluate(async()=>({links:await window.porsche981.modelOemLinksGet(),state:await window.porsche981.xrayGetMeshState(),
    prices:(await window.porsche981.listParts()).map(p=>({sku:p.sku,oem_price:p.oem_price,aftermarket_price:p.aftermarket_price,aftermarket_quotes:p.aftermarket_quotes,price_note:p.price_note,price_as_of:p.price_as_of}))}));
  assert.deepEqual(fresh.links.links,expectedLinks.links);assert.deepEqual(fresh.state.layers,expectedState.layers);
  const partSkus=new Set(fresh.prices.map(p=>p.sku));
  for(const link of fresh.links.links)for(const sku of link.skus)assert(partSkus.has(sku),`linked SKU missing from bundled catalog: ${sku}`);
  assert(fresh.prices.length>8000);fs.writeFileSync(path.join(output,'fresh-prices.json'),JSON.stringify(fresh.prices));
  assert.equal(await page.locator('[data-model-key]').count(),expectedExterior);const exterior=await state(page);
  // Check actual rendered labels, not just the number of rows in the seed.
  // GLTFLoader removes punctuation from single-primitive object names.
  const oemChecks=[
    ['Exterior__soft-top__SM_SoftTop_0000001_MAT_SoftTop_0','981.561.015.00'],
    ['Exterior__soft-top__SM_SoftTop_0000001_MAT_Glass067_0','981.561.015.00'],
    ['Exterior__windows__718__GEO_INNER_DOOR_GLASS_L','981.542.511.00'],
    ['Exterior__windows__718__GEO_INNER_DOOR_GLASS_R','981.542.512.00'],
  ];
  for(const [ref,oem] of oemChecks){
    const row=page.locator(`[data-model-key="mesh:${ref}"]`);
    assert((await row.innerText()).includes(oem),`${ref} must display ${oem}`);
    await row.click();assert(!(await row.locator('..').innerText()).includes('未关联'));
  }
  const exteriorLinked=await page.locator('[data-model-key]').evaluateAll(es=>es.filter(e=>!e.textContent.includes('未关联')).length);
  checks.push('soft top and left/right window OEM labels',existingOem?'existing local OEM upgrade':'fresh OEM seed');
  assert(exterior.some(m=>m.mats.some(x=>x.reviewed?.finish==='wheel-black')));
  await page.screenshot({path:path.join(output,'exterior.png')});
  await page.getByRole('button',{name:'内饰',exact:true}).click();await page.waitForTimeout(250);
  const cabinKeys=await page.locator('[data-model-key]').evaluateAll(es=>es.map(e=>e.getAttribute('data-model-key')));
  const cabinNames=manifest.parts.filter(p=>p.category==='Cabin').map(p=>'mesh:'+p.name).sort();
  assert.deepEqual(cabinKeys.filter(k=>k.startsWith('mesh:')).sort(),cabinNames);
  assert.deepEqual(cabinKeys.filter(k=>k.startsWith('asm:')).sort(),seed.assemblies.filter(a=>a.interiorZone&&fs.existsSync(path.join(local,a.glbRel))).map(a=>'asm:'+a.id).sort());
  const cabin=await state(page);assert(cabin.some(m=>m.name==='Cabin__liner__718__GEO_INT_PLASTIC_BLACK_SUB0_Part_005'&&m.mats.every(x=>x.reviewed?.finish==='polymer')));
  await page.screenshot({path:path.join(output,'interior.png')});
  await page.getByRole('button',{name:'外观',exact:true}).click();await page.waitForTimeout(200);
  const before=await page.locator('.locator-glb canvas').screenshot();
  await page.evaluate(async()=>{const v=await window.porsche981.getVehicle();await window.porsche981.setVehicleSettings({paint_name:'Guards Red',paint_code:'L84A',interior:v.interior,top:v.top});window.dispatchEvent(new Event('focus'));});
  await page.getByRole('button',{name:'外观',exact:true}).click();await page.waitForTimeout(500);
  const painted=await state(page);assert(painted.some(m=>m.mats.some(x=>x.reviewed?.finish==='body-paint')));
  assert.notDeepEqual(before,await page.locator('.locator-glb canvas').screenshot());checks.push('vehicle paint');
  const base=page.url().split('#')[0];await page.goto(base+'#xray-tune?asset=garage-primary');await page.reload();
  const edited=JSON.parse(fs.readFileSync(path.join(root,'data/seed/flat-six/garage-exterior-edits.json')));
  await page.getByRole('button',{name:edited.merged[0].name,exact:true}).waitFor({timeout:45000});
  for(const part of edited.merged)assert.equal(await page.getByRole('button',{name:part.name,exact:true}).count(),1);
  await state(page);await page.screenshot({path:path.join(output,'tune.png')});
  assert.deepEqual(errors,[]);fs.writeFileSync(path.join(output,'report.json'),JSON.stringify({ok:true,exterior:expectedExterior,exteriorLinked,interior:expectedInterior,links:fresh.links.links.length,prices:fresh.prices.length,checks,errors},null,2));
  console.log(JSON.stringify({ok:true,output,exterior:expectedExterior,exteriorLinked,interior:expectedInterior,links:fresh.links.links.length,prices:fresh.prices.length}));
}finally{clearTimeout(timer);if(app)await app.close();}
