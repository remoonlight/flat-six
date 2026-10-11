"""Hash-bound historical X431 coding observations; never a fresh ECU backup.

An explicit reference address permits a candidate join, not ECU/version
identification. Preserve complete PDUs and repeated observations, and keep
before-coding and coding-workflow phases separate. No device transport.
"""
from __future__ import annotations
import argparse
from collections import Counter, defaultdict
import hashlib
import json
import os
from pathlib import Path
from scripts.x431_re.request_fields import coding_profile_variant

from .coding_capture import FLAGS, VARIANTS_SHA256, digest
from .coding_sequence import sequence, vehicle_information_report, periodic_link_report, read_context_report
from .decoder_reference import compare_reference, reference_value
from .identity_capture import AUDIT_SHA, SUMMARY_SHA, observations, reference_routes, source_profiles
from .offline_match import ANCHORS, verify_frames, verify_source
from .response_values import decode_application_response, normalize_response, request_from_record

GUARDS={**FLAGS,'identityQualified':False,'completeLengthQualified':False,
        'completeVehicleCodingScopeQualified':False,'vehicleValuesQualified':False,
        'noDeviceIO':True,'fittedClaim':False}


def candidate_index(observed):
    index=defaultdict(list)
    for row in observed:index[(row['txId'],row['requestHex'])].append(row)
    return index


def group_observation(group, row, route):
    """Echo, explicit RX and source minimum span qualify only a candidate block."""
    out={'observationId':row['observationId'],'capturePhase':row['capturePhase'],
         'status':row['status'],'dataHex':None,'observedDataBytes':None,**GUARDS}
    if row['status']!='positive-candidate' or row['rxId']!=route['rxId']:
        if row['status']=='positive-candidate':out['status']='address-unqualified'
        return out
    hx=group['requestHex'];raw=bytes.fromhex(hx)
    record={'read_request_candidate_hex':hx,'readSID':raw[0],'pid':int(hx[2:],16)}
    normalized=normalize_response(bytes.fromhex(row['pduHex']),record,'pdu',source='historical-x431-coding')
    if not normalized.get('ok'):
        out['status']=normalized.get('reason') or 'coding-response-invalid';return out
    data=normalized['payload']
    out.update(dataHex=data.hex().upper(),observedDataBytes=len(data))
    out['status']='historical-candidate-block' if len(data)>=group['minimumKnownBytes'] else 'coding-read-short-block'
    # Even a recorded historical length is not the ECU's complete-length rule.
    return out


def profile_evidence(unit, profile, index, routes):
    route=routes.get(unit['ecuId']) if unit['generation']=='981' else None
    groups=[]
    for source in profile['coding']['requestGroups']:
        found=index.get((route['txId'],source['requestHex']),[]) if route else []
        samples=[group_observation(source,row,route) for row in found]
        candidates=[s for s in samples if s['status']=='historical-candidate-block']
        lengths=sorted({s['observedDataBytes'] for s in candidates})
        by_phase={}
        for phase in ('before-coding','coding-workflow'):
            phase_samples=[s for s in candidates if s['capturePhase']==phase]
            by_phase[phase]={'observations':len(phase_samples),
                'distinctDataSha256':sorted({hashlib.sha256(bytes.fromhex(s['dataHex'])).hexdigest() for s in phase_samples})}
        groups.append({'requestHex':source['requestHex'],'minimumKnownBytes':source['minimumKnownBytes'],
            'expectedTotalBytes':None,'fieldCount':len(source['fields']),
            'status':'historical-candidate-present' if candidates else 'historical-read-missing',
            'observedDataLengths':lengths,'observedLengthConflict':len(lengths)>1,
            'phases':by_phase,'samples':samples,**GUARDS})
    return {'generation':unit['generation'],'ecuId':unit['ecuId'],'profileId':profile['profileId'],
        'module':profile['module'],'variant':profile['variant'],'sourceGeneration':profile['sourceGeneration'],
        'referenceRoute':route,'definitionFieldCount':profile['coding']['definitionFieldCount'],
        'definitionMissing':profile['coding']['missing'],'requestGroups':groups,
        'missingReadRequests':[g['requestHex'] for g in groups if g['status']=='historical-read-missing'],
        'routeMissing':route is None,'joinBasis':'explicit reference address + exact request; version and identity unqualified',**GUARDS}


def field_observation(record, row):
    """Matching rejections are evidence of a failure, never decoded values."""
    request=request_from_record(record)
    if not request.get('ok') or request['request'].hex().upper()!=row['requestHex']:
        raise ValueError('coding-history-field-request-mismatch')
    value=decode_application_response(record,bytes.fromhex(row['pduHex']),'pdu',source='historical-x431-coding')
    value.pop('evidence',None)
    agreement=False;failure_agreement=False;issue=None
    try:
        data=bytes.fromhex(row['pduHex'])[len(request['request']):]
        expected=reference_value(record,data)
        differences=compare_reference(expected,value)
        agreement=bool(value.get('ok') and expected.get('ok') and not differences)
        failure_agreement=bool(not value.get('ok') and expected.get('ok') is False
            and isinstance(expected.get('reason'),str) and expected['reason'] and not differences)
        if not agreement:
            issue='independent-matching-rejection:'+expected['reason'] if failure_agreement else 'independent-decode-gap-or-disagreement'
    except (ValueError,KeyError,TypeError,ArithmeticError) as error:
        issue='independent-reference-unresolved-'+type(error).__name__
    try:json.dumps(value,allow_nan=False)
    except (ValueError,TypeError):
        value={'ok':False,'reason':'coding-result-not-json-finite'};agreement=False;failure_agreement=False;issue='coding-result-not-json-finite'
    return {'sourceOffset':record.get('at'),'source':record.get('source'),
        'requestHex':row['requestHex'],'observationId':row['observationId'],'capturePhase':row['capturePhase'],
        'adapterStream':row.get('adapterStream'),
        'value':value,'independentDecoderAgreement':agreement,'independentFailureAgreement':failure_agreement,
        'independentIssue':issue,**GUARDS}


def build(portable, expected_sha256, capture_root, output):
    output=Path(output)
    if output.exists():raise ValueError('coding-history-fresh-output-required')
    profiles,source=source_profiles(portable,expected_sha256)
    root=Path(capture_root)
    folder=root/'.local/vehicle-analysis/981-x431-20261001'
    variants=root/'.local/x431-re/2026-09-27-981982/expansion/variants.jsonl'
    paths=[(folder/'app-frames.jsonl',ANCHORS['frames']),(folder/'transport-audit.json',AUDIT_SHA),
        (folder/'app-frame-summary.json',SUMMARY_SHA),
        (root/'docs/research/can-data/diagnostic/definitions.json',ANCHORS['addresses']),
        (root/'.local/vehicle-runs/981-x431-coding-20261001T095850Z/final/btsnoop_hci.log',ANCHORS['hci']),
        (variants,VARIANTS_SHA256)]
    inputs=[verify_source(path,expected) for path,expected in paths]
    audit=json.loads(paths[1][0].read_text());summary=json.loads(paths[2][0].read_text())
    if audit['sha256']!=ANCHORS['hci'] or summary['sourceTransportSha256']!=ANCHORS['hci']:
        raise ValueError('coding-history-transport-chain-mismatch')
    with paths[0][0].open(encoding='utf8') as source_stream:
        frames=[json.loads(line) for line in source_stream]
    verify_frames(frames)
    routes,pairs=reference_routes(json.loads(paths[3][0].read_text()))
    observed=observations(frames,pairs,audit['checkpoints'][0]['record']);index=candidate_index(observed)
    results=[profile_evidence(u,p,index,routes) for u,p in profiles]
    wanted=defaultdict(list);used=set()
    for result in results:
        for group in result['requestGroups']:
            used.update(s['observationId'] for s in group['samples'])
            if any(s['status'] in ('historical-candidate-block','coding-read-short-block') for s in group['samples']):
                wanted[result['profileId']].append((result,group))
    by_id={o['observationId']:o for o in observed};output.mkdir(parents=True)
    def write(name,data):(output/name).write_text(json.dumps(data,ensure_ascii=False,sort_keys=True,indent=2,allow_nan=False)+'\n',encoding='utf8')
    def line(stream,data):stream.write(json.dumps(data,ensure_ascii=False,sort_keys=True,allow_nan=False)+'\n')
    commands,access_report=sequence(frames,pairs,audit['checkpoints'][0]['record'])
    with (output/'command-sequence.jsonl').open('x',encoding='utf8') as stream:
        for command in commands:line(stream,command)
    write('access-observations.json',access_report)
    write('vehicle-information-observations.json',vehicle_information_report(commands))
    write('periodic-link-observations.json',periodic_link_report(commands))
    contexts=read_context_report(commands,pairs,used)
    write('read-context-observations.json',contexts)
    field_counts=Counter();failure_counts=Counter();rejection_counts=Counter();seen=set()
    with (output/'field-values.jsonl').open('x',encoding='utf8') as stream, variants.open(encoding='utf8') as source_stream:
        for raw in source_stream:
            variant=json.loads(raw);pid=variant['profile_id']
            if pid not in wanted:continue
            if pid in seen:raise ValueError('coding-history-duplicate-source-profile')
            seen.add(pid)
            variant=coding_profile_variant(variant)
            records=variant.get('pool_records',{}).get('coding',{}).get('records',[])
            for result,group in wanted[pid]:
                if len(records)!=result['definitionFieldCount']:raise ValueError('coding-history-source-field-count-mismatch')
                selected=[]
                for record in records:
                    req=request_from_record(record)
                    if req.get('ok') and req['request'].hex().upper()==group['requestHex']:selected.append(record)
                if len(selected)!=group['fieldCount']:raise ValueError('coding-history-source-group-count-mismatch')
                for sample in group['samples']:
                    if sample['status'] not in ('historical-candidate-block','coding-read-short-block'):continue
                    row=by_id[sample['observationId']]
                    for record in selected:
                        item=field_observation(record,row)
                        item.update(generation=result['generation'],ecuId=result['ecuId'],profileId=pid)
                        line(stream,item)
                        field_counts['independent-agreement' if item['independentDecoderAgreement'] else 'decode-gap']+=1
                        if not item['value'].get('ok'):
                            failure_counts[item['value'].get('reason') or 'unknown']+=1
                            rejection_counts['matched-rejection' if item['independentFailureAgreement'] else 'mismatch-or-unresolved']+=1
        stream.flush();os.fsync(stream.fileno())
    if seen!=set(wanted):raise ValueError('coding-history-source-profile-missing')
    for path,expected in paths:verify_source(path,expected)
    with (output/'coding-profiles.jsonl').open('x',encoding='utf8') as stream:
        for result in results:line(stream,result)
    write('observations.json',[o for o in observed if o['observationId'] in used])
    report={'kind':'x431-historical-all-unit-coding-evidence','profileBindings':len(results),
        'uniqueProfiles':len({r['profileId'] for r in results}),
        'bindingsByGeneration':dict(Counter(r['generation'] for r in results)),
        'groupStatuses':dict(Counter(g['status'] for r in results for g in r['requestGroups'])),
        'profilesWithCandidateGroups':len({r['profileId'] for r in results if any(g['status']=='historical-candidate-present' for g in r['requestGroups'])}),
        'profilesWithPositiveCodingObservations':len(wanted),'uniqueReadObservations':len(used),
        'readObservationStatuses':dict(Counter(o['status'] for o in observed if o['observationId'] in used)),
        'fieldObservationChecks':dict(field_counts),'fieldFailureReasons':dict(failure_counts),
        'independentRejectionChecks':dict(rejection_counts),'beforeCodingBoundaryRecord':audit['checkpoints'][0]['record'],
        'readContextObservations':contexts['readObservationCount'],
        'readContextResponseStatuses':contexts['responseStatuses'],
        'countBasis':'menu/generation/version candidates; fields may repeat across versions; not qualified ECUs',**GUARDS}
    write('report.json',report)
    code_names=('coding_history.py','coding_sequence.py','session_capture.py','identity_capture.py','offline_match.py','x431_capture.py',
        'coding_capture.py','source_catalog.py','response_values.py','decoder_reference.py','x431_values.py','x431_formula.py')
    write('manifest.json',{'schemaVersion':1,'sourceCatalog':source,'inputs':inputs,
        'code':[{'path':'scripts/diagnostics/'+n,'sha256':digest(Path(__file__).with_name(n))} for n in code_names] +
            [{'path':'scripts/x431_re/request_fields.py','sha256':digest(Path(__file__).parents[1]/'x431_re/request_fields.py')}],
        'outputs':[{'name':n,'bytes':(output/n).stat().st_size,'sha256':digest(output/n)} for n in
            ('coding-profiles.jsonl','field-values.jsonl','observations.json','command-sequence.jsonl','access-observations.json','vehicle-information-observations.json','periodic-link-observations.json','read-context-observations.json','report.json')],**GUARDS})
    return report


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--definitions-zip',required=True,type=Path)
    parser.add_argument('--definitions-sha256',required=True)
    parser.add_argument('--capture-root',required=True,type=Path)
    parser.add_argument('--output',required=True,type=Path)
    args=parser.parse_args()
    print(json.dumps(build(args.definitions_zip,args.definitions_sha256,args.capture_root,args.output),sort_keys=True))


if __name__=='__main__':main()
