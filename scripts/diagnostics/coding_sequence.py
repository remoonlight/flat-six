"""Historical command boundaries, service-27 and vehicle info, no transport.

The UDS reference supplies provisional odd/even labels only. It does not prove
Porsche KWP semantics, key acceptance, an algorithm, or permission to transmit.
"""
from collections import Counter
import hashlib
from .session_capture import application_request, response_status
from .x431_capture import envelope, extract_return

REFERENCE='https://udsoncan.readthedocs.io/en/latest/_modules/udsoncan/services/SecurityAccess.html'
INFO_REFERENCE='https://python-obd.readthedocs.io/en/latest/Command%20Tables/#mode-09'
FUNCTIONAL_REFERENCE='https://docs.autopi.io/cloud/obd-ii/create-pid-loggers/'
FLAGS={'executionEnabled':False,'writePayload':None,'identityQualified':False,
       'accessQualified':False,'keyAlgorithmQualified':False,'writeSequenceQualified':False,
       'freshReadProven':False,'baselineCreated':False,'completeOriginalBackup':False,'noDeviceIO':True}

# Statically reviewed retained originals. Matching the serialized shape does
# not establish the historical Android callback binding or controller outcome.
PERIODIC_SOURCE={
    'caller':{'library':'libPORSCHE_COMM.so',
        'sha256':'e1fd611820be63bb2c234eadeb5d56a3aeae8c31def2b8dbc56d020c280654fc',
        'symbol':'PorscheSetLinkKeep','entry':0x12324,'bytes':1200,
        'regionSha256':'9185729200e5cd5eb24ce9ee55fa39bb7c7bedc6abdc99e35fe454e083f54c94'},
    'provider':{'library':'libCOMM_ABSTRACT_LAYER.so',
        'sha256':'cc96cd1443f373c96853cd797be6a9bf84579cdf6e7031c35dc1df423efb91da',
        'symbol':'SetCanIIBusLinkKeep','entry':0x348a8,'bytes':300,
        'regionSha256':'f97a716dcd8c38472c635aff72a7fa5835445a29c901e065600c6c98a3a98c07'}}


def periodic_link_carrier(raw):
    """Only the reviewed stop/17-byte nested request and 11/120 parameters."""
    if not envelope(raw,b'\xf0\xf8') or raw[7:11]!=bytes.fromhex('2701600d'):return None
    base={'actualPeriodicTransmissionQualified':False,'actualStopQualified':False,
        'actualChannelCloseQualified':False,'actualDeviceReleaseQualified':False,
        'actualProviderBindingQualified':False,'source':PERIODIC_SOURCE,**FLAGS}
    if len(raw)==15 and raw[11:14]==bytes(3):
        return {'action':'stop-configuration-observed','nestedRequestHex':None,**base}
    if len(raw)!=32 or raw[11]!=17 or raw[29:31]!=bytes((11,120)):return None
    nested=raw[12:29]
    if nested[:6]!=bytes.fromhex('55aa0d610108'):return None
    checksum=0
    # The source initializes the checksum slot to 0x0d before XORing
    # bytes 3..15; include the length byte at index 2.
    for b in nested[2:16]:checksum^=b
    address=int.from_bytes(nested[6:8],'big');count=nested[8]
    if checksum!=nested[16] or address&31 or count not in (1,2):return None
    payload=nested[9:9+count]
    if payload not in (b'\x3e',b'\x3e\x00'):return None
    return {'action':'periodic-configuration-observed','nestedRequestHex':nested.hex().upper(),
        'nestedTxId':address>>5,'nestedPayloadHex':payload.hex().upper(),
        'providerParameterByte':11,'providerIntervalByte':120,
        'sourceCallerIntervalArgument':1200,'actualCadenceQualified':False,**base}


def periodic_link_report(rows):
    observations=[]
    for row in rows:
        item=row.get('periodicLinkObservation')
        if item is None:continue
        reply=row['pairedReply'] or {}
        observations.append({k:row[k] for k in ('observationId','requestRecord','requestFrameSha256',
            'adapterStream','capturePhase')} | {'observation':item,
            'pairedReplyFrameId':reply.get('frameId'),'pairedReplyFrameSha256':reply.get('frameSha256'),
            'pairedControllerReplyObserved':bool(reply),'replyMeaningQualified':False})
    return {'observations':observations,'observationCount':len(observations),
        'actions':dict(Counter(r['observation']['action'] for r in observations)),
        'source':PERIODIC_SOURCE,'actualProviderBindingQualified':False,
        'actualPeriodicTransmissionQualified':False,'actualStopQualified':False,
        'actualCadenceQualified':False,'actualChannelCloseQualified':False,
        'actualDeviceReleaseQualified':False,**FLAGS}


def service27_carrier(raw):
    if (len(raw)!=29 or not envelope(raw,b'\xf0\xf8') or raw[7:9]!=b'\x27\x01'
            or raw[9:18]!=bytes.fromhex('640001ff020d610108')):return None
    address=int.from_bytes(raw[18:20],'big');count=raw[20]
    if address&31 or not 2<=count<=7 or raw[21]!=0x27:return None
    request=raw[21:21+count];sub=request[1]
    if not 1<=sub<=0x7e:return None
    return {'txId':address>>5,'requestHex':request.hex().upper(),'subfunctionByte':sub,
        'requestDataHex':request[2:].hex().upper(),
        'provisionalRole':'seed-request' if sub&1 else 'key-send','roleReference':REFERENCE}


def vehicle_information_carrier(raw):
    if (len(raw)!=29 or not envelope(raw,b'\xf0\xf8') or raw[7:9]!=b'\x27\x01'
            or raw[9:18]!=bytes.fromhex('640001ff020d610108')):return None
    address=int.from_bytes(raw[18:20],'big')
    if address&31 or raw[20]!=2 or raw[21]!=9 or raw[22] not in (0,2):return None
    return {'txId':address>>5,'requestHex':raw[21:23].hex().upper(),'vehicleInformationPid':raw[22]}


def vehicle_information_observation(request, ret, pairs):
    result={'status':'return-layout-unqualified','vehicleIdentityQualified':False,
        'exactEcuVersionQualified':False,'vinLegalityChecked':False,'reference':INFO_REFERENCE,**FLAGS}
    if ret is None:return result
    expected=pairs.get(request['txId']);pdu=bytes.fromhex(ret['pduHex']);pid=request['vehicleInformationPid']
    functional=request['txId']==0x7df
    allowed={rx for rx in pairs.values() if 0x7e8<=rx<=0x7ef} if functional else {expected} if expected is not None else set()
    result.update(observedReplyRxId=ret['rxId'],functionalRequestObserved=functional,
        addressBasis='functional-request-and-explicit-reference-receiver' if functional else 'explicit-reference-address-pair',
        allFunctionalRepliesCaptured=False,functionalAddressReference=FUNCTIONAL_REFERENCE if functional else None)
    if not allowed:result['status']='reference-rx-missing'
    elif ret['rxId'] not in allowed:result['status']='address-unqualified'
    elif len(pdu)==3 and pdu[:2]==b'\x7f\x09':
        result.update(status='pending' if pdu[2]==0x78 else 'negative',nrc=pdu[2])
    elif len(pdu)<2 or pdu[:2]!=bytes((0x49,pid)):result['status']='echo-mismatch'
    elif pid==0:
        result['status']='supported-pids-length-unreviewed'
        if len(pdu)==6:
            mask=int.from_bytes(pdu[2:],'big')
            result.update(status='supported-pids-observed',maskHex=pdu[2:].hex().upper(),
                supportedPids=[i for i in range(1,33) if mask&(1<<(32-i))])
    else:
        result['status']='vin-layout-unreviewed'
        # Only the observed reassembled layout: one byte 01, then exactly
        # 17 printable ASCII bytes. Do not trim, pad or join other messages.
        if len(pdu)==20 and pdu[2]==1 and all(0x20<=b<=0x7e for b in pdu[3:]):
            result.update(status='vin-17-ascii-observed',leadingDataByte=1,vinBytes=17,
                vinSha256=hashlib.sha256(pdu[3:]).hexdigest())
    return result


def vehicle_information_report(rows):
    observations=[]
    for r in rows:
        if r.get('vehicleInformationObservation') is None:continue
        item={k:r[k] for k in ('observationId','requestRecord','requestFrameSha256','requestHex','txId',
            'adapterStream','capturePhase','vehicleInformationObservation')}
        reply=r['pairedReply'] or {}
        item.update(responseFrameId=reply.get('frameId'),responseFrameSha256=reply.get('frameSha256'))
        observations.append(item)
    associations=[]
    for supported,vin in zip(rows,rows[1:]):
        a=supported.get('vehicleInformationObservation');b=vin.get('vehicleInformationObservation')
        if (not a or not b or a['status']!='supported-pids-observed' or b['status']!='vin-17-ascii-observed'
                or supported['adapterStream']!=vin['adapterStream'] or supported['txId']!=vin['txId']
                or supported['capturePhase']!=vin['capturePhase'] or a['observedReplyRxId']!=b['observedReplyRxId']):continue
        associations.append({'supportedObservationId':supported['observationId'],'vinObservationId':vin['observationId'],
            'vinPidAdvertised':2 in a['supportedPids'],'vinSha256':b['vinSha256'],
            'vehicleIdentityQualified':False,'exactEcuVersionQualified':False,**FLAGS})
    return {'observations':observations,'observationCount':len(observations),
        'statuses':dict(Counter(r['vehicleInformationObservation']['status'] for r in observations)),
        'adjacentAssociations':associations,'vehicleIdentityQualified':False,'exactEcuVersionQualified':False,
        'vinTextIncluded':False,'reference':INFO_REFERENCE,**FLAGS}


def read_context_report(rows,pairs,observation_ids=None):
    """Reference preceding observations, never infer an active session/unlock.

    Walk every host command before filtering reads. Unknown commands, target or
    phase changes and stop configurations discard earlier context. A new session
    attempt discards older service-27 observations even without a positive reply.
    Key/seed bytes stay in the original command sequence, not this summary.
    """
    if observation_ids is not None and (not isinstance(observation_ids,(set,frozenset))
            or any(not isinstance(i,str) or not i for i in observation_ids)):
        raise ValueError('read-context-selected-ids-invalid')
    guards={**FLAGS,'activeSessionQualified':False,'service27UnlockQualified':False,
            'failureCauseQualified':False,'contextContinuityProven':False}
    states={};events={};reads=[];seen=set()
    def reset(row,reason,tx=None):
        return {'phase':row['capturePhase'],'tx':tx,'sessions':[],'accesses':[],'periodic':[],
            'boundary':{'observationId':row['observationId'],'requestRecord':row['requestRecord'],'reason':reason}}
    def event(row,status):
        reply=row['pairedReply'] or {};ret=reply.get('returnShape') or {}
        return {k:row[k] for k in ('observationId','requestRecord','requestFrameSha256','adapterStream','capturePhase')} | {
            'txId':row.get('txId'),'kind':row['kind'],'status':status,
            'responseFrameId':reply.get('frameId'),'responseFrameSha256':reply.get('frameSha256'),
            'responseRecord':reply.get('record'),'observedRxId':ret.get('rxId'),**guards}
    def status(row):
        expected=pairs.get(row['txId'])
        return ('reference-rx-missing' if expected is None else
                response_status(row['requestHex'],(row['pairedReply'] or {}).get('returnShape'),expected))
    for index,row in enumerate(rows):
        if row['hostCommandIndex']!=index or row['observationId'] in seen:
            raise ValueError('read-context-command-order')
        seen.add(row['observationId']);stream=tuple(row['adapterStream']);oid=row['observationId']
        state=states.setdefault(stream,reset(row,'capture-start'))
        if state['phase']!=row['capturePhase']:state=reset(row,'capture-phase-change')
        if row['kind']=='unreviewed-host-boundary':
            states[stream]=reset(row,'unreviewed-host-boundary');continue
        periodic=row.get('periodicLinkObservation')
        if periodic is not None:
            if periodic['action']=='stop-configuration-observed':state=reset(row,'periodic-stop-configuration')
            elif state['tx'] is not None and state['tx']!=periodic.get('nestedTxId'):
                state=reset(row,'periodic-target-change',periodic.get('nestedTxId'))
            else:state['tx']=periodic.get('nestedTxId')
            events[oid]={**event(row,periodic['action']),'nestedTxId':periodic.get('nestedTxId')}
            state['periodic'].append(oid);states[stream]=state;continue
        tx=row.get('txId');request=row.get('requestHex')
        if tx is None or not request:
            states[stream]=reset(row,'unresolved-command-boundary');continue
        if state['tx'] is not None and tx!=state['tx']:state=reset(row,'target-change',tx)
        else:state['tx']=tx
        states[stream]=state;sid=int(request[:2],16)
        if sid==0x10:
            events[oid]={**event(row,status(row)),'requestHex':request}
            state['sessions'].append(oid);state['accesses']=[]
        elif row.get('accessObservation') is not None:
            events[oid]={**event(row,row['accessObservation']['status']),
                'subfunctionByte':row['subfunctionByte'],'provisionalRole':row['provisionalRole']}
            state['accesses'].append(oid)
        elif sid in (0x1a,0x21,0x22) and (observation_ids is None or oid in observation_ids):
            ret=(row['pairedReply'] or {}).get('returnShape') or {}
            pdu=bytes.fromhex(ret.get('pduHex',''))
            reads.append({**event(row,status(row)),'requestHex':request,'responseStatus':status(row),
                'pduSha256':hashlib.sha256(pdu).hexdigest() if pdu else None,
                'nrc':pdu[2] if len(pdu)==3 and pdu[:2]==bytes((0x7f,sid)) else None,
                'precedingSessionObservationIds':list(state['sessions']),
                'service27AfterLatestSessionObservationIds':list(state['accesses']),
                'precedingPeriodicConfigObservationIds':list(state['periodic']),
                'continuityBoundary':dict(state['boundary'])})
    if observation_ids is not None and {r['observationId'] for r in reads}!=observation_ids:
        raise ValueError('read-context-selected-read-missing')
    used={oid for r in reads for key in ('precedingSessionObservationIds',
        'service27AfterLatestSessionObservationIds','precedingPeriodicConfigObservationIds') for oid in r[key]}
    return {'kind':'historical-read-preceding-command-context','reads':reads,
        'events':{oid:value for oid,value in events.items() if oid in used},
        'readObservationCount':len(reads),'responseStatuses':dict(Counter(r['responseStatus'] for r in reads)),
        'readsWithPrecedingSessionRequests':sum(bool(r['precedingSessionObservationIds']) for r in reads),
        'readsWithService27SinceLatestSessionAttempt':sum(bool(r['service27AfterLatestSessionObservationIds']) for r in reads),
        'countBasis':'historical request observations; preceding events do not prove current ECU state or failure cause',
        **guards}


def sequence(frames,pairs,boundary):
    rows=[]
    for i,frame in enumerate(frames):
        if frame['stream'][1]!='host_to_controller':continue
        raw=bytes.fromhex(frame['frameHex']);access=service27_carrier(raw);info=vehicle_information_carrier(raw)
        periodic=periodic_link_carrier(raw)
        request=access or info or application_request(raw)
        row={'observationId':frame['frameId'],'hostCommandIndex':len(rows),
            'requestRecord':frame['startRecord'],'requestFrameSha256':frame['frameSha256'],
            'requestFrameHex':frame['frameHex'],'adapterStream':[frame['stream'][0],frame['stream'][2]],
            'capturePhase':'before-coding' if frame['startRecord']<=boundary else 'coding-workflow',
            'kind':'service27-carrier' if access else 'vehicle-information-carrier' if info else 'periodic-link-config-carrier' if periodic else 'read-or-session-carrier' if request else 'unreviewed-host-boundary',
            'requestHex':None,'txId':None,'pairedReply':None,'accessObservation':None,**FLAGS}
        if request:row.update(request)
        if periodic:row['periodicLinkObservation']=periodic
        reply=frames[i+1] if i+1<len(frames) else None
        if (reply and reply['stream'][1]=='controller_to_host'
                and (reply['stream'][0],reply['stream'][2])==(frame['stream'][0],frame['stream'][2])
                and reply['sequenceByte']==frame['sequenceByte']
                and reply['commandByte']==(frame['commandByte']|0x40)):
            ret=extract_return(bytes.fromhex(reply['frameHex']))
            row['pairedReply']={'frameId':reply['frameId'],'record':reply['startRecord'],
                'frameHex':reply['frameHex'],'frameSha256':reply['frameSha256'],'returnShape':ret}
        if info:row['vehicleInformationObservation']=vehicle_information_observation(info,(row['pairedReply'] or {}).get('returnShape'),pairs)
        if access:
            ret=(row['pairedReply'] or {}).get('returnShape');status='return-layout-unqualified'
            result={'status':status,'zeroSeedObserved':False,'uninterpretedResponseDataHex':None,**FLAGS}
            if ret:
                pdu=bytes.fromhex(ret['pduHex']);sub=access['subfunctionByte'];expected=pairs.get(access['txId'])
                if expected is None:status='reference-rx-missing'
                elif ret['rxId']!=expected:status='address-unqualified'
                elif len(pdu)==3 and pdu[:2]==b'\x7f\x27':
                    status='pending' if pdu[2]==0x78 else 'negative';result['nrc']=pdu[2]
                elif len(pdu)<2 or pdu[:2]!=bytes((0x67,sub)):status='echo-mismatch'
                elif sub&1:
                    status='seed-bytes-observed' if len(pdu)>2 else 'seed-bytes-missing'
                    result['uninterpretedResponseDataHex']=pdu[2:].hex().upper()
                    result['zeroSeedObserved']=len(pdu)>2 and not any(pdu[2:])
                else:
                    status='key-positive-prefix-observed'
                    result['uninterpretedResponseDataHex']=pdu[2:].hex().upper()
            result['status']=status;row['accessObservation']=result
        rows.append(row)
    accesses=[r for r in rows if r['accessObservation'] is not None];adjacent=[]
    for seed,key in zip(rows,rows[1:]):
        a=seed['accessObservation'];b=key['accessObservation']
        if (a is None or b is None or seed.get('provisionalRole')!='seed-request'
                or key.get('provisionalRole')!='key-send' or a['status']!='seed-bytes-observed'
                or a['zeroSeedObserved'] or key['subfunctionByte']!=seed['subfunctionByte']+1
                or seed['txId']!=key['txId'] or seed['adapterStream']!=key['adapterStream']
                or seed['capturePhase']!=key['capturePhase']):continue
        adjacent.append({'seedObservationId':seed['observationId'],'keyObservationId':key['observationId'],
            'txId':seed['txId'],'adapterStream':seed['adapterStream'],'capturePhase':seed['capturePhase'],
            'keyResponseStatus':b['status'],'historicalPositivePrefixObserved':b['status']=='key-positive-prefix-observed',**FLAGS})
    report={'hostCommands':len(rows),'commandKinds':dict(Counter(r['kind'] for r in rows)),
        'service27Observations':len(accesses),'service27Statuses':dict(Counter(r['accessObservation']['status'] for r in accesses)),
        'zeroSeedObservations':sum(r['accessObservation']['zeroSeedObserved'] for r in accesses),
        'keyResponsesWithUninterpretedData':sum(bool(r['accessObservation']['uninterpretedResponseDataHex']) for r in accesses if r['provisionalRole']=='key-send'),
        'adjacentSeedKeyObservations':adjacent,'roleReference':REFERENCE,
        'unknownBoundariesPreventWriteAbsenceClaim':True,**FLAGS}
    return rows,report
