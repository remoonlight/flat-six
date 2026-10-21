import { LIVE_READ_PROFILES, liveReadTargets, faultReadTargets, faultProfileFitsGeneration } from './manufacturer-profiles.mjs';

const SOURCE_CATALOG='31bf93245fffc375b757fc19ba03491e101ccabc838c9ee742d70a88cb7805ba';
const MANUAL_MODULES={1:'dme',2:'pdk',9:'gateway',5:'psm',19:'pasm',55:'rdk',50:'epb',35:'bcm-rear',
  4:'airbag',7:'hvac',8:'cluster',29:'clock',54:'steering-column',88:'headlamp-sg',32:'bcm-front',47:'headlamp-left'};

/** A record keeps its original vehicle/unit scope even while an old result is
 * retained after a failed reread. Never infer a code conversion from that scope. */
export function sourceManualScope(scope,profileId){
  if(!scope||typeof scope!=='object'||Array.isArray(scope)
    ||Object.keys(scope).length!==5||Object.keys(scope).some(k=>!['generation','ecuId','sourceProfileId','sourceCatalogId','namespace'].includes(k))
    ||scope.namespace!=='manufacturer-hex'||scope.sourceCatalogId!==SOURCE_CATALOG
    ||!['981','982'].includes(scope.generation)||!Number.isInteger(scope.ecuId)
    ||scope.ecuId!==sourceTopologyEcu(profileId)||!MANUAL_MODULES[scope.ecuId]
    ||!faultProfileFitsGeneration(scope.sourceProfileId,scope.generation)
    ||!faultReadTargets(scope.sourceProfileId).includes(scope.ecuId))return null;
  return {generation:scope.generation,module:MANUAL_MODULES[scope.ecuId],namespace:scope.namespace};
}

function scopedDecoded(result,ecuId,vehicle,expectedHash){
  const decoded=result.dtcRead.decoded,q=result.transport?.qualification,pid=result.identifiedProfileId;
  const scope={generation:result.vehicleGeneration,ecuId,sourceProfileId:pid,sourceCatalogId:q?.sourceCatalogId,namespace:'manufacturer-hex'};
  if(result.vehicleGeneration!==vehicle?.generation||result.vehicleIdentitySha256!==expectedHash
    ||decoded?.type!=='uds-dtc-19-02'||!Array.isArray(decoded.records)||q?.freshRead!==true||q.observedProfileMatch!==true
    ||q.ecuId!==ecuId||q.generation!==scope.generation||q.variantLink?.profile_id!==pid
    ||result.dtcRead.profileId!==pid||result.dtcRead.ecuId!==ecuId||result.dtcRead.generation!==scope.generation
    ||result.dtcRead.sourceCatalogId!==SOURCE_CATALOG||!sourceManualScope(scope,`manufacturer-ecu-${ecuId}`))return decoded;
  return {...decoded,records:decoded.records.map(row=>row.manufacturerCode===true&&row.dictionaryMatch===true
    &&/^[0-9A-F]{6}$/.test(row.dtcHex||'')&&row.displayCode===row.dtcHex?{...row,manualScope:{...scope}}:row)};
}

// The system IDs are the same IDs used by the compiled ready-units catalogue.
// No bus placement or installation is inferred from this read route mapping.
const NODE_ECU = Object.freeze({gateway:9,dme:1,pdk:2,selector:68,airbag:4,shaker:87,psm:5,pasm:19,eps:69,epb:50,
  'bcm-front':32,'bcm-rear':35,'steering-column':54,'door-driver':30,'door-passenger':31,'seat-driver':76,'seat-passenger':78,
  roof:39,rdk:55,hvac:7,pcm:70,cluster:8,clock:29,parking:18,'reverse-camera':81,'headlamp-left':47,'headlamp-right':48,
  'headlamp-sg':88,acc:64,'front-camera':74,'swa-left':96,'swa-right':95,'ecu-75':75,'ecu-65':65,'ecu-165':165});
const supported = new Set([...LIVE_READ_PROFILES.keys()].flatMap(liveReadTargets));
export function sourceTopologyEcu(profileId) {
  if (profileId==='porsche-981-2014-dme') return 1;
  if (profileId==='porsche-981-2014-gateway') return 9;
  const match=typeof profileId==='string' && /^manufacturer-ecu-(\d+)$/.exec(profileId);
  const ecu=match?Number(match[1]):null;
  return supported.has(ecu)?ecu:null;
}
export function withSourceFaultRoutes(generation) {
  if (!generation) return generation;
  const qualify=node=> {
    const ecu=NODE_ECU[node.id];
    return supported.has(ecu) ? {...node,profileId:node.profileId || `manufacturer-ecu-${ecu}`} : node;
  };
  const existing=new Set([generation.gateway?.id,...(generation.branches || []).flatMap(branch=>(branch.nodes || []).map(node=>node.id))]);
  const unplaced=[{id:'ecu-65',short:'AMP',label:'功放'},{id:'ecu-75',short:'TV',label:'电视调谐器'},
    {id:'ecu-165',short:'ERA',label:'紧急呼叫'}].filter(node=>!existing.has(node.id)).map(qualify);
  return {...generation,unplacedNodes:unplaced,gateway:generation.gateway?qualify(generation.gateway):null,
    branches:(generation.branches || []).map(branch=>({...branch,nodes:(branch.nodes || []).map(qualify)}))};
}

const terminal = value => value?.state==='finished' && value.released===true;
const disconnected = error => /^(port-io:|prompt-timeout:|disconnect|transport-disconnected|monitor_resume_failed|short-write)/.test(error || '');

/** Adapt source-bound read workers to the existing topology queue. No clear action is routed here. */
export function createSourceFaultInvoker({ stream, legacy, connection, deviceId, model }) {
  let counter=0, vehicle=null, expectedHash=null, legacyReadback=false;
  const jobs=new Map();
  function normalize(job, state) {
    const result=state.result, error=state.error || result?.error || (job.cancelled?'cancelled':null);
    const closed=terminal(state), success=closed && !error && result?.ok===true;
    let code=disconnected(error)?'disconnect':error;
    if (code==='vehicle-identity-mismatch') code='scan-vehicle-changed';
    const health=job.linkHealthy===true && state.releaseVerified===true;
    const candidate=health && ['empty-payload','NO DATA','no-response','identity-mismatch','negative-response'].includes(code)
      ? code==='negative-response'?'negative-response':'no-response':null;
    return {ok:true,jobId:job.id,state:closed?(job.cancelled?'cancelled':success?'completed':'failed'):
      job.cancelled || ['closing','finished'].includes(state.state)?'cancelling':'running',
      released:closed,releaseVerified:closed && state.releaseVerified===true,error:closed?code:null,
      final:closed?{ok:success,status:job.cancelled?'cancelled':success?'completed':'failed',mode:'live',simulation:false,
        profileId:job.profileId,sourceProfileId:result?.identifiedProfileId,sourceDtcRead:true,
        vehicleIdentitySha256:result?.vehicleIdentitySha256,vehicleGeneration:result?.vehicleGeneration,
        identityQualification:result?.transport?.qualification,sourceIdentity:result?.identity,
        identityInfo:result?.identityInfo,
        candidateFailure:candidate,linkHealth:health?'verified':'unconfirmed',restoration:{errors:result?.transport?.closeErrors || []},
        error:code,results:success?[{operationId:'source-dtc',role:'dependent',ok:true,freshlyRead:true,historical:false,
          skippedResume:false,decoded:health?scopedDecoded(result,job.ecuId,vehicle,expectedHash):result.dtcRead.decoded}]:[]}:null};
  }
  async function checkConnection() {
    const status=await connection({action:'status'});
    if (status?.selectedDeviceId!==deviceId || status.model!==model) return {ok:false,error:'scan-device-changed'};
    if (status.purpose!=='diagnostic') return {ok:false,error:'diagnostic_purpose_required'};
    const connected=status.connected===true && status.linkState==='connected';
    return {ok:connected,error:connected?null:'disconnect'};
  }
  async function launchFault(job) {
    if (job.cancelled) return;
    const check=await checkConnection();if(job.cancelled)return;
    if(!check.ok){job.failure=check.error;job.failureReleaseVerified=job.state?.releaseVerified===true;return;}
    let value;
    try {
      value=await stream({action:'read-dtc',generation:vehicle.generation,ecuId:job.ecuId,
        expectedVehicleIdentitySha256:expectedHash,confirmedReadOnly:true,x431Inactive:true});
    } catch(error) {
      job.failure=error instanceof Error?error.message:String(error);job.failureReleaseVerified=false;return;
    }
    job.phase='dtc';job.state=value;
    if(!value?.ok || !value.sessionId){job.failure=value?.error || 'manufacturer-read-start-failed';job.failureReleaseVerified=false;return;}
    job.sessionId=value.sessionId;
    if(job.cancelled)await stream({action:'stop',sessionId:job.sessionId});
  }
  return async function invoke(request) {
    // The existing clear client owns its evidence and same-range readback.
    // Keep that complete transaction on one identity/result representation.
    if(request.action==='start' && request.sessionTask==='clear')legacyReadback=true;
    if(request.action==='start' && legacyReadback)return legacy(request);
    if(request.action==='start' && request.mode==='live' && request.sessionTask==='read') {
      const ecu=sourceTopologyEcu(request.profileId);
      if(ecu===null)return {ok:false,error:'manufacturer-profile-unqualified'};
      const check=await checkConnection();if(!check.ok)return check;
      const job={id:`source-dtc-${++counter}`,ecuId:ecu,profileId:request.profileId,phase:vehicle?'dtc':'vehicle',cancelled:false};
      jobs.set(job.id,job);
      if(vehicle)await launchFault(job);
      else {
        const value=await stream({action:'identify-vehicle',confirmedReadOnly:true,x431Inactive:true});
        job.state=value;
        if(!value?.ok || !value.sessionId){jobs.delete(job.id);return {ok:false,error:value?.error || 'vehicle-model-unidentified'};}
        job.sessionId=value.sessionId;
      }
      return {ok:true,jobId:job.id,state:'running',released:false,releaseVerified:false};
    }
    const job=jobs.get(request.jobId);
    if(!job)return legacy(request);
    if(request.action==='cancel') {
      job.cancelled=true;
      if(job.sessionId && !terminal(job.state))await stream({action:'stop',sessionId:job.sessionId});
      return {ok:true,jobId:job.id};
    }
    if(request.action!=='status')return {ok:false,error:'unsupported_action'};
    if(job.failure) return {ok:true,jobId:job.id,state:job.cancelled?'cancelled':'failed',released:true,releaseVerified:job.failureReleaseVerified===true,
      error:job.failure,final:{ok:false,status:'failed',mode:'live',profileId:job.profileId,error:job.failure}};
    const state=await stream({action:'status',sessionId:job.sessionId});job.state=state;
    if(!state?.ok || state.sessionId!==job.sessionId)throw new Error(state?.error || 'source-dtc-status-invalid');
    if(!terminal(state))return normalize(job,state);
    if(job.phase==='vehicle') {
      const result=state.result;
      if(job.cancelled || state.error || state.releaseVerified!==true || result?.ok!==true)return normalize(job,state);
      if(!['981','982'].includes(result.vehicleGeneration) || !/^[a-f0-9]{64}$/.test(result.vehicleIdentitySha256 || '')) {
        job.failure='scan-vehicle-unconfirmed';job.failureReleaseVerified=true;return invoke({action:'status',jobId:job.id});
      }
      if(expectedHash && result.vehicleIdentitySha256!==expectedHash) {
        job.failure='scan-vehicle-changed';job.failureReleaseVerified=true;return invoke({action:'status',jobId:job.id});
      }
      expectedHash ||= result.vehicleIdentitySha256;vehicle={generation:result.vehicleGeneration};
      await launchFault(job);
      if(job.cancelled && job.phase==='vehicle')return normalize(job,state);
      return job.failure?invoke({action:'status',jobId:job.id}):{ok:true,jobId:job.id,state:'running',released:false,releaseVerified:false};
    }
    job.linkHealthy=(await checkConnection()).ok;
    const final=normalize(job,state);
    // A retry re-identifies the vehicle. The original vehicle binding persists.
    if(!final.final?.ok)vehicle=null;
    return final;
  };
}
