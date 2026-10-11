"""Source-bound DTC review after disconnect, from complete application PCAPNG.

No transport. Requested target/run/link selectors are analysis context only.
Preserve repeated replies and failures; source labels never qualify ECU identity
or the meaning of an observed status byte.
"""
from __future__ import annotations
import argparse
from collections import Counter
import hashlib
import json
import os
from pathlib import Path
import re

from .coding_capture import FLAGS, digest, native_result_error, scan_capture
from .dtc_definitions import decode_observation
from .source_catalog import _catalog, _code_status, _document

GUARDS={**FLAGS,'identityQualified':False,'ecuVersionQualified':False,
    'perVariantDtcLayoutQualified':False,'vehicleValuesQualified':False,
    'liveStatusMeaningQualified':False,'requestedTargetIsResponderProof':False,'noDeviceIO':True}


def load_profile(library, catalog_id, generation, ecu_id, profile_id):
    if generation not in ('981','982') or type(ecu_id) is not int:
        raise ValueError('dtc-capture-selector-invalid')
    root,manifest=_catalog(library,catalog_id)
    index=_document(root,manifest,'all-units.json')
    refs=[r for u in index['units'] if u['generation']==generation and u['ecuId']==ecu_id
        for r in u['variants'] if r['profileId']==profile_id]
    if len(refs)!=1:raise ValueError('dtc-capture-profile-ecu-mismatch')
    profile=_document(root,manifest,refs[0]['file'])
    if (profile.get('profileId')!=profile_id or ecu_id not in profile.get('ecuIds',[])
            or generation not in profile.get('generations',[])):
        raise ValueError('dtc-capture-profile-identity-mismatch')
    return profile,{'sourceCatalogId':catalog_id,'profileFile':refs[0]['file'],**_code_status(manifest)}


def read_scope(profile, generation, ecu_id, link_index):
    if (generation not in ('981','982') or generation not in profile.get('generations',[])
            or type(ecu_id) is not int or ecu_id not in profile.get('ecuIds',[])
            or type(link_index) is not int):raise ValueError('dtc-capture-selector-invalid')
    refs=[r for r in profile.get('diagnosticSources',[]) if r.get('requestedEcuId')==ecu_id
        and r.get('variantName')==profile['variant']]
    if len(refs)!=1:raise ValueError('dtc-capture-reference-not-unique')
    links=[r for r in refs[0].get('links',[]) if r.get('linkIndex')==link_index]
    if len(links)!=1:raise ValueError('dtc-capture-link-not-unique')
    slots=[r for r in links[0].get('dtc',{}).get('readCommandSlots',[]) if r.get('role')=='dtcRead']
    requests=[];missing=[]
    for slot in slots:
        hx=slot.get('wireCandidateHex')
        if not isinstance(hx,str) or not re.fullmatch(r'(?:[0-9A-F]{2}){1,7}',hx):
            missing.append({'slot':slot,'reason':'dtc-source-request-unresolved'});continue
        if hx not in requests:requests.append(hx)
    # A valid empty capture still exports the full dictionary, so validate its
    # identity and canonical hash before any packet is visited.
    definition=profile.get('dtcDefinition') or {}
    if (definition.get('kind')!='x431-source-dtc-definition'
            or any(definition.get(k)!=profile.get(k) for k in ('profileId','module','variant'))):
        raise ValueError('dtc-profile-definition-mismatch')
    canonical=dict(definition);expected=canonical.pop('definitionSha256',None)
    if hashlib.sha256(json.dumps(canonical,sort_keys=True,ensure_ascii=True).encode()).hexdigest()!=expected:
        raise ValueError('dtc-definition-hash-mismatch')
    if not requests:missing.append({'reason':'dtc-source-read-request-missing'})
    return {'requests':requests,'readSlots':slots,'missing':missing}


def capture_targets(profile, generation, ecu_id):
    # Offline comparisons only. A named runtime target scopes the intended ECU,
    # never its responder identity, exact source version or vehicle applicability.
    targets=[{'targetProfileId':profile['profileId'],'association':'source-target-id'}]
    runtime={1:'porsche-981-2014-dme',9:'porsche-981-2014-gateway'}
    if generation=='981' and ecu_id in runtime and runtime[ecu_id]!=profile['profileId']:
        targets.append({'targetProfileId':runtime[ecu_id],'association':'explicit-named-runtime-comparison'})
    return targets


def comparison_context(profile, generation, ecu_id, capture_target):
    if not isinstance(capture_target,str) or not 0<len(capture_target)<=240:
        raise ValueError('dtc-capture-target-invalid')
    matches=[r for r in capture_targets(profile,generation,ecu_id) if r['targetProfileId']==capture_target]
    if len(matches)!=1:raise ValueError('dtc-capture-target-source-mismatch')
    return {'captureTargetProfileId':capture_target,'sourceProfileId':profile['profileId'],
        'association':matches[0]['association'],'sourceSelectionIsVersionProof':False,
        'generation':generation,'ecuId':ecu_id,**GUARDS}


def source_scope(library, catalog_id, generation, ecu_id, profile_id):
    profile,source=load_profile(library,catalog_id,generation,ecu_id,profile_id)
    refs=[r for r in profile.get('diagnosticSources',[]) if r.get('requestedEcuId')==ecu_id
        and r.get('variantName')==profile['variant']]
    if len(refs)!=1:raise ValueError('dtc-capture-reference-not-unique')
    links=[dict(linkIndex=l['linkIndex'],**read_scope(profile,generation,ecu_id,l['linkIndex'])) for l in refs[0].get('links',[])]
    return {'links':links,'captureTargets':capture_targets(profile,generation,ecu_id),'source':source,'sourceGeneration':profile.get('sourceGeneration'),
        'definitionCount':profile['dtcDefinition']['definitionCount'],**GUARDS}


def analyze(capture, profile, generation, ecu_id, link_index, run_id, emit, *, capture_target_profile_id=None):
    scope=read_scope(profile,generation,ecu_id,link_index)
    review=comparison_context(profile,generation,ecu_id,profile['profileId'] if capture_target_profile_id is None else capture_target_profile_id)
    counts=Counter();bindings=Counter();sessions=Counter();observed=set()
    first=None;last=None;reversals=0;matched=0;records=Counter()
    def visit(row):
        nonlocal first,last,reversals,matched
        if row.get('error'):
            emit({**row,'status':'capture-packet-error','scopeAssigned':False,**GUARDS})
            counts['capture-packet-error']+=1;return
        meta=row['context'];key=(meta['targetProfileId'],meta['runId']);sessions[key]+=1
        if key!=(review['captureTargetProfileId'],run_id):return
        matched+=1;stamp=row['timestampUs']
        if first is None:first=stamp
        if last is not None and stamp<last:reversals+=1
        last=stamp;hx=meta['requestHex'];decoded=None;nrc=None
        if hx not in scope['requests']:reason='request-not-in-dtc-scope'
        elif meta['duringCleanup']:reason='cleanup-response-unqualified'
        else:reason=native_result_error(meta)
        if not reason:
            pdu=bytes.fromhex(row['pduHex']);request=bytes.fromhex(hx)
            if len(pdu)==3 and pdu[:2]==bytes((0x7f,request[0])):
                nrc=pdu[2];reason='pending' if nrc==0x78 else 'negative-response'
            else:
                decoded=decode_observation(profile,ecu_id,link_index,hx,row['pduHex'],
                    source_kind='simulation' if row['synthetic'] else 'imported-unqualified')
                if not decoded['ok']:reason=decoded.get('error') or 'dtc-response-invalid'
        status=reason or ('synthetic-decoded-response' if row['synthetic'] else 'decoded-response-observed')
        counts[status]+=1
        if decoded and decoded['ok']:
            records['synthetic' if row['synthetic'] else 'observed']+=len(decoded['records'])
            for record in decoded['records']:bindings[record['definitionBindingStatus']]+=1
            if not row['synthetic']:observed.add(hx)
        emit({**row,'requestHex':hx,'status':status,'nrc':nrc,'decodedObservation':decoded,
            'elapsedHostMs':(stamp-first)/1000,'reviewContext':review,**GUARDS})
    scanned=scan_capture(capture,visit)
    return {'kind':'captured-dtc-analysis','schemaVersion':1,'profileId':profile['profileId'],
        'generation':generation,'sourceGeneration':profile.get('sourceGeneration'),
        'module':profile['module'],'variant':profile['variant'],'ecuId':ecu_id,'offlineLinkIndex':link_index,
        'runId':run_id,'captureTargetProfileId':review['captureTargetProfileId'],'sourceProfileId':profile['profileId'],
        'reviewContext':review,'requestCount':len(scope['requests']),'readScope':scope,
        'definitionCount':profile['dtcDefinition']['definitionCount'],
        'definitionSha256':profile['dtcDefinition']['definitionSha256'],
        'counts':dict(counts),'recordCounts':dict(records),'definitionBindingCounts':dict(bindings),
        'matchedPackets':matched,'observedRequestCount':len(observed),
        'missingReadRequests':[hx for hx in scope['requests'] if hx not in observed],
        'firstHostTimestampUs':first,'lastHostTimestampUs':last,'hostTimeReversalCount':reversals,
        'timeBasis':'host-observed-complete-PDU; file order preserved; not ECU sampling time',
        'availableSessions':[dict(targetProfileId=p,runId=r,packets=n) for (p,r),n in sessions.items()],
        'capture':scanned,**GUARDS}


def write_analysis(capture, library, catalog_id, generation, ecu_id, profile_id, link_index, run_id,
                   output_dir, expected_capture_sha256=None, *, capture_target_profile_id=None):
    profile,source=load_profile(library,catalog_id,generation,ecu_id,profile_id)
    comparison_context(profile,generation,ecu_id,profile_id if capture_target_profile_id is None else capture_target_profile_id)
    output=Path(output_dir);output.mkdir(parents=True,exist_ok=False)
    indices={}
    for i,record in enumerate(profile['dtcDefinition']['records']):indices.setdefault(record['numericKey'],[]).append(i)
    with (output/'responses.jsonl').open('x',encoding='utf8') as stream, (output/'faults.jsonl').open('x',encoding='utf8') as faults:
        def emit(row):
            decoded=row.get('decodedObservation')
            if decoded is not None:
                decoded=dict(decoded);records=decoded.pop('records',[])
                parsed=decoded.get('decodedApplicationResponse')
                if parsed is not None:
                    parsed=dict(parsed);parsed_records=parsed.pop('records',[])
                    decoded['decodedApplicationResponse']={**parsed,'recordCount':len(parsed_records),'recordsFile':'faults.jsonl'}
                for i,record in enumerate(records):
                    item={k:v for k,v in record.items() if k!='definitionMatches'}
                    item.update(packet=row['packet'],recordIndex=i,requestHex=row['requestHex'],timestampUs=row['timestampUs'],
                        elapsedHostMs=row['elapsedHostMs'],synthetic=row['synthetic'],runId=run_id,
                        captureTargetProfileId=row['context']['targetProfileId'],sourceProfileId=profile_id,
                        reviewContext=row['reviewContext'],
                        definitionRecordIndices=indices.get(int(record['dtcHex'],16),[]))
                    faults.write(json.dumps(item,ensure_ascii=False,allow_nan=False)+'\n')
                row={**row,'decodedObservation':{**decoded,'recordCount':len(records),'recordsFile':'faults.jsonl'}}
            stream.write(json.dumps(row,ensure_ascii=False,allow_nan=False)+'\n')
        report=analyze(capture,profile,generation,ecu_id,link_index,run_id,emit,capture_target_profile_id=capture_target_profile_id)
        for file in (stream,faults):file.flush();os.fsync(file.fileno())
    if expected_capture_sha256 is not None and report['capture']['sha256']!=expected_capture_sha256:
        raise ValueError('capture-changed-reopen')
    (output/'dictionary.json').write_text(json.dumps(profile['dtcDefinition'],ensure_ascii=False,indent=2)+'\n',encoding='utf8')
    report['source']=source
    report['artifacts']={n:digest(output/n) for n in ('responses.jsonl','faults.jsonl','dictionary.json')}
    root=Path(__file__).resolve().parents[2]
    report['code']=[{'path':'scripts/diagnostics/'+n,'sha256':digest(root/'scripts/diagnostics'/n)}
        for n in ('dtc_capture.py','dtc_definitions.py','coding_capture.py','coding_read_plan.py',
            'source_catalog.py','decode.py','offline_match.py')]
    (output/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf8')
    return report


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--capture',type=Path,required=True);p.add_argument('--library-root',type=Path,required=True)
    p.add_argument('--source-catalog',required=True);p.add_argument('--generation',choices=('981','982'),required=True)
    p.add_argument('--ecu-id',type=int,required=True);p.add_argument('--profile',required=True)
    p.add_argument('--link-index',type=int,required=True);p.add_argument('--run-id',required=True)
    p.add_argument('--capture-target',help='exact captured target ID; defaults to the source profile for existing captures')
    p.add_argument('--output-dir',type=Path,required=True);args=p.parse_args()
    r=write_analysis(args.capture,args.library_root,args.source_catalog,args.generation,args.ecu_id,
        args.profile,args.link_index,args.run_id,args.output_dir,capture_target_profile_id=args.capture_target)
    print(json.dumps({'output':str(args.output_dir),'matchedPackets':r['matchedPackets'],'counts':r['counts'],
        'missingRequests':len(r['missingReadRequests']),'capturePrefixComplete':r['capture']['prefixComplete'],**GUARDS}))
    return 0 if r['capture']['prefixComplete'] and not r['capture']['packetErrors'] else 2


if __name__=='__main__':raise SystemExit(main())
