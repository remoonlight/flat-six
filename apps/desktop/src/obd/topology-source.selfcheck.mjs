import assert from 'node:assert/strict';
import { createSourceFaultInvoker, sourceTopologyEcu, withSourceFaultRoutes } from './topology-source.mjs';
import { createScanQueue, classifySessionFinal, KIND, dtcDetail, flattenNodes, branchAppearances } from '../can-topology-logic.mjs';
const hash='a'.repeat(64),flags={confirmedReadOnly:true,x431Inactive:true},deviceId='bt:001122334455',model='ELM327';
function rig({manual=false,wrongSource=false,vehicleGeneration='982',faultHash=null}={}) {
 const jobs=new Map(),calls=[],legacyCalls=[];let next=0,hold=false,failed=false,vehicleHash=hash,release=true,stopCount=0,startError=null;
 const stream=async request=>{
  calls.push(request);
  if(request.action==='read-dtc' && startError) {
   if(startError==='throw')throw new Error('PRIVATE_START_ERROR');
   return {ok:false,error:'PRIVATE_START_ERROR'};
  }
  if(request.action==='identify-vehicle'||request.action==='read-dtc'){
   const sessionId='s'+(++next);jobs.set(sessionId,{...request,sessionId});return {ok:true,sessionId,state:'running',released:false};
  }
  const job=jobs.get(request.sessionId);assert.ok(job);
  if(request.action==='stop'){stopCount++;job.cancelled=true;return {ok:true,sessionId:job.sessionId};}
  if(hold)return {ok:true,sessionId:job.sessionId,state:'running',released:false,releaseVerified:false};
  const pid=manual?'9x1:BCM_hinten:BCM_hinten_9x1_GT4':'9x1:PSM:PSM_a1',catalog='31bf93245fffc375b757fc19ba03491e101ccabc838c9ee742d70a88cb7805ba';
  const identity={observedProfileMatch:true,freshRead:true,...(manual?{ecuId:wrongSource?32:35,generation:'982',sourceCatalogId:catalog,variantLink:{profile_id:pid}}:{})},
   records=[manual?{displayCode:'C13235',dtcHex:'C13235',statusHex:'09',manufacturerCode:true,dictionaryMatch:true,originalEnglish:'Original English'}:{displayCode:'C13002',dtcHex:'C13002',statusHex:'09',originalEnglish:'Original English'}];
  return {ok:true,sessionId:job.sessionId,state:'finished',released:true,releaseVerified:release,error:null,
   result:{ok:!failed && !job.cancelled,error:job.cancelled?'cancelled':failed?'port-io:lost':null,
    vehicleGeneration:job.action==='identify-vehicle'?vehicleGeneration:'982',vehicleIdentitySha256:job.action==='read-dtc'&&faultHash!==null?faultHash:vehicleHash,identifiedProfileId:pid,
    transport:{qualification:identity,closeErrors:[]},dtcRead:{...(manual?{profileId:pid,ecuId:35,generation:'982',sourceCatalogId:catalog}:{}),decoded:{ok:true,type:'uds-dtc-19-02',records}}}};
 };
 const invoke=createSourceFaultInvoker({stream,legacy:async request=>{legacyCalls.push(request);return {ok:true};},
  connection:async()=>({selectedDeviceId:deviceId,model,purpose:'diagnostic',connected:true,linkState:'connected'}),deviceId,model});
 return {invoke,calls,legacyCalls,get stops(){return stopCount;},hold:value=>{hold=value;},fail:value=>{failed=value;},hash:value=>{vehicleHash=value;},release:value=>{release=value;},failStart:value=>{startError=value;}};
}
const start=(r,profileId='manufacturer-ecu-5')=>r.invoke({action:'start',mode:'live',sessionTask:'read',profileId,...flags});
const poll=(r,jobId)=>r.invoke({action:'status',jobId});
let checks=0;
{
 for(const wrongSource of [false,true]){
  const r=rig({manual:true,wrongSource}),job=await start(r,'manufacturer-ecu-35');
  await poll(r,job.jobId);const done=await poll(r,job.jobId),state=classifySessionFinal(done.final,{mode:'live',profileId:'manufacturer-ecu-35'});
  assert.equal(state.kind,KIND.dtc);
  assert.equal(dtcDetail(state.records[0],'manufacturer-ecu-35'),wrongSource?'Original English':'Drive Air deflector');
  const retained=structuredClone(state.records[0]);assert.equal(dtcDetail(retained,'manufacturer-ecu-35'),wrongSource?'Original English':'Drive Air deflector');
  assert.equal(dtcDetail(retained,'manufacturer-ecu-32'),'Original English');
 }
 checks++;
}
{
 for(const options of [{faultHash:'b'.repeat(64)},{vehicleGeneration:'981'}]){
  const r=rig({manual:true,...options}),job=await start(r,'manufacturer-ecu-35');
  await poll(r,job.jobId);const done=await poll(r,job.jobId);
  const row=done.final.results[0].decoded.records[0];
  assert.equal(row.manualScope,undefined);
  assert.equal(dtcDetail(row,'manufacturer-ecu-35'),'Original English');
 }
 checks++;
}
{
 for(const mode of ['return','throw']) {
  const r=rig(),job=await start(r);r.failStart(mode);
  const done=await poll(r,job.jobId);assert.equal(done.state,'failed');assert.equal(done.releaseVerified,false);
  assert.equal(done.final.ok,false);assert.equal(done.final.error,'PRIVATE_START_ERROR');
 }
 checks++;
}
{
 const r=rig(),first=await start(r);assert.equal(r.calls[0].action,'identify-vehicle');
 r.hold(true);assert.equal((await poll(r,first.jobId)).released,false);assert.equal(r.calls.filter(x=>x.action==='read-dtc').length,0);
 r.hold(false);assert.equal((await poll(r,first.jobId)).released,false);
 const read=r.calls.find(x=>x.action==='read-dtc');assert.equal(read.generation,'982');assert.equal(read.expectedVehicleIdentitySha256,hash);
 const done=await poll(r,first.jobId);assert.equal(classifySessionFinal(done.final,{profileId:'manufacturer-ecu-5',mode:'live'}).kind,KIND.dtc);
 assert.equal(dtcDetail(done.final.results[0].decoded.records[0],'manufacturer-ecu-5'),'Original English');
 const second=await start(r,'manufacturer-ecu-19');await poll(r,second.jobId);
 assert.equal(r.calls.filter(x=>x.action==='identify-vehicle').length,1);assert.equal(r.calls.filter(x=>x.action==='read-dtc').length,2);checks++;
}
{
 const r=rig(),job=await start(r);await r.invoke({action:'cancel',jobId:job.jobId});
 const done=await poll(r,job.jobId);assert.equal(done.state,'cancelled');assert.equal(done.releaseVerified,true);
 assert.equal(r.calls.some(x=>x.action==='read-dtc'),false);assert.equal(r.stops,1);checks++;
}
{
 const r=rig(),job=await start(r);r.release(false);
 const done=await poll(r,job.jobId);assert.equal(done.releaseVerified,false);assert.equal(r.calls.some(x=>x.action==='read-dtc'),false);checks++;
}
{
 const r=rig(),job=await start(r);await poll(r,job.jobId);r.fail(true);
 assert.equal((await poll(r,job.jobId)).final.error,'disconnect');r.fail(false);r.hash('b'.repeat(64));
 const retry=await start(r);const done=await poll(r,retry.jobId);
 assert.equal(done.error,'scan-vehicle-changed');assert.equal(done.releaseVerified,true);
 assert.equal(r.calls.filter(x=>x.action==='read-dtc').length,1);checks++;
}
{
 const r=rig(),job=await start(r);await poll(r,job.jobId);r.fail(true);
 await poll(r,job.jobId);r.fail(false);
 const retry=await start(r);assert.equal(r.calls.filter(x=>x.action==='identify-vehicle').length,2);
 await poll(r,retry.jobId);const done=await poll(r,retry.jobId);
 assert.equal(done.final.ok,true);assert.equal(done.final.vehicleIdentitySha256,hash);
 assert.equal(r.calls.filter(x=>x.action==='read-dtc').length,2);checks++;
}
{
 const r=rig();await r.invoke({action:'start',mode:'live',sessionTask:'clear',profileId:'porsche-981-2014-dme'});
 await r.invoke({action:'start',mode:'simulation',sessionTask:'read',profileId:'porsche-981-2014-gateway'});
 await r.invoke({action:'start',mode:'live',sessionTask:'read',profileId:'porsche-981-2014-gateway'});
 assert.equal(r.legacyCalls.length,3);assert.equal(r.calls.length,0);checks++;
}
{
 const r=rig(),q=createScanQueue({invoke:r.invoke,pollMs:0,sleep:async()=>{}}),seen=[];
 const out=await q.run({nodes:[{id:'psm',profileId:'manufacturer-ecu-5'},{id:'pasm',profileId:'manufacturer-ecu-19'}],
  ctx:{mode:'live',sessionTask:'read',...flags},hooks:{onKind:(id,kind)=>seen.push([id,kind.kind])}});
 assert.equal(out.results.length,2);assert.equal(out.results.every(row=>row.classified.kind===KIND.dtc),true);
 assert.equal(r.calls.filter(x=>x.action==='read-dtc').length,2);checks++;
}
{
 for(const id of [30,31,76,78])assert.equal(sourceTopologyEcu('manufacturer-ecu-'+id),id);
 assert.equal(sourceTopologyEcu('manufacturer-ecu-47'),47);assert.equal(sourceTopologyEcu('manufacturer-ecu-48'),48);assert.equal(sourceTopologyEcu('manufacturer-ecu-88'),88);
 const gen=withSourceFaultRoutes({gateway:{id:'gateway',profileId:'porsche-981-2014-gateway'},branches:[{id:'bus',nodes:[{id:'psm'},{id:'door-driver'}]}]});
 assert.equal(gen.branches[0].nodes[0].profileId,'manufacturer-ecu-5');assert.equal(gen.branches[0].nodes[1].profileId,'manufacturer-ecu-30');checks++;
 assert.deepEqual(gen.unplacedNodes.map(node=>sourceTopologyEcu(node.profileId)),[65,75,165]);
 assert.equal(flattenNodes(gen).filter(node=>node.branchId==='unplaced').length,3);
 assert.equal(branchAppearances(gen).flatMap(branch=>branch.appearances).some(node=>node.branchId==='unplaced'),false);checks++;
}
console.log(`PASS ${checks} source topology integration checks`);
