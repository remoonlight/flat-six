"""Desktop-owned offline capture files. No vehicle imports, transport or writes."""
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import zipfile
from collections import Counter

from .coding_capture import FLAGS, VARIANTS_SHA256, analyze_capture, digest, load_plan, scan_capture


def inventory(file):
    sessions=Counter()
    def visit(row):
        if not row.get('error'):
            m=row['context'];sessions[(m['targetProfileId'],m['runId'],row['synthetic'])]+=1
    capture=scan_capture(file,visit)
    return {'capture':capture,'sessions':[dict(targetProfileId=p,runId=r,synthetic=s,packets=n)
        for (p,r,s),n in sessions.items()]}


def coding_scope(request):
    from .dtc_capture import load_profile, capture_targets
    library=Path(os.environ.get('PORSCHE981_DEFINITION_ROOT') or Path(__file__).resolve().parents[2])
    profile,source=load_profile(library,request['sourceCatalogId'],request['generation'],request['ecuId'],request['profileId'])
    if not source['generatorCodeMatchesCurrent']:raise ValueError('coding-capture-generator-drift')
    plan=profile['coding']
    if plan['profileId']!=profile['profileId']:raise ValueError('coding-capture-plan-profile-mismatch')
    return {'plan':{**plan,'generation':request['generation']},'source':source,
        'captureTargets':capture_targets(profile,request['generation'],request['ecuId']),**FLAGS}


def coding_units(request):
    from .source_catalog import handle_source_catalog
    from .realtime_preparation import NODE_ECU
    library=Path(os.environ.get('PORSCHE981_DEFINITION_ROOT') or Path(__file__).resolve().parents[2])
    if request['systemId'] not in NODE_ECU:raise ValueError('coding-capture-system-invalid')
    result=handle_source_catalog({'action':'source-units','generation':request['generation'],
        'sourceCatalogId':request['sourceCatalogId']},library)
    if not result['generatorCodeMatchesCurrent']:raise ValueError('coding-capture-generator-drift')
    units=[u for u in result['units'] if u['ecuId']==NODE_ECU[request['systemId']]]
    if len(units)>1:raise ValueError('coding-capture-unit-ambiguous')
    return {'unit':units[0] if units else None,**FLAGS}


def analyze(request):
    variants=Path(os.environ.get('PORSCHE981_VARIANTS') or Path(__file__).resolve().parents[2]/'.local/x431-re/2026-09-27-981982/expansion/variants.jsonl')
    if request.get('kind')=='identity':
        from .identity_application_capture import catalog_profiles, review
        library=Path(os.environ.get('PORSCHE981_DEFINITION_ROOT') or Path(__file__).resolve().parents[2])
        bindings,sources=catalog_profiles(library,request['sourceCatalogId'])
        if digest(Path(request['capture']))!=request['captureSha256']:raise ValueError('capture-changed-reopen')
        output=Path(request['output'])
        result=review(request['capture'],bindings,sources,request['generation'],request['ecuId'],
            request['captureTargetProfileId'],request['runId'],output)
        if result['capture']['sha256']!=request['captureSha256']:raise ValueError('capture-changed-reopen')
        result['code'].append({'path':'scripts/diagnostics/coding_capture_bridge.py','sha256':digest(Path(__file__))})
        (output/'report.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
        artifact={name:digest(output/name) for name in ('report.json','responses.jsonl','identity-values.jsonl','identity-profiles.json')}
        return {'report':{**result,'definitionFieldCount':sum(p['sourceFieldCount'] for p in result['profiles']),
            'requestGroupCount':len({g['requestHex'] for _,p in bindings if p['profileId'] in {s['sourceProfileId'] for s in result['profiles']} for g in p['identityGroups']}),
            'missingReadRequests':sorted({f['requestHex'] for p in result['profiles'] for f in p['missingObservedFields']}),
            'definitionMissing':[f for p in result['profiles'] for f in p['sourceMissing']], 'changedGroups':[]},'artifact':artifact}
    if request.get('kind')=='dtc':
        from .dtc_capture import write_analysis
        output=Path(request['output'])
        library=Path(os.environ.get('PORSCHE981_DEFINITION_ROOT') or Path(__file__).resolve().parents[2])
        result=write_analysis(request['capture'],library,request['sourceCatalogId'],request['generation'],request['ecuId'],
            request['profileId'],request['linkIndex'],request['runId'],output,expected_capture_sha256=request['captureSha256'],
            capture_target_profile_id=request.get('captureTargetProfileId'))
        result['code'].append({'path':'scripts/diagnostics/coding_capture_bridge.py','sha256':digest(Path(__file__))})
        (output/'report.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
        artifact={name:digest(output/name) for name in ('report.json','responses.jsonl','faults.jsonl','dictionary.json')}
        return {'report':{**result,'definitionFieldCount':result['definitionCount'],'requestGroupCount':result['requestCount'],
            'definitionMissing':result['readScope']['missing'],'changedGroups':[]},'artifact':artifact}
    if request.get('kind')=='measurement':
        from .measurement_capture import write_analysis
        output=Path(request['output'])
        result=write_analysis(request['capture'],variants,request['profileId'],request['generation'],request['runId'],output,
            expected_capture_sha256=request['captureSha256'])
        result['code'].append({'path':'scripts/diagnostics/coding_capture_bridge.py','sha256':digest(Path(__file__))})
        (output/'report.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
        artifact={name:digest(output/name) for name in ('report.json','responses.jsonl','values.jsonl','fields.json')}
        return {'report':{**result,'definitionFieldCount':result['sourceFieldCount'],'changedGroups':[]},'artifact':artifact}
    scope=coding_scope(request) if 'sourceCatalogId' in request else None
    plan=scope['plan'] if scope else load_plan(variants,request['profileId'],request['generation'])
    target=request.get('captureTargetProfileId',plan['profileId'])
    if scope and target not in {t['targetProfileId'] for t in scope['captureTargets']}:
        raise ValueError('coding-capture-target-source-mismatch')
    output=Path(request['output']);output.mkdir(exist_ok=False)
    with (output/'responses.jsonl').open('x',encoding='utf-8') as stream, (output/'blocks.jsonl').open('x',encoding='utf-8') as blocks:
        result=analyze_capture(request['capture'],plan,request['runId'],lambda row:stream.write(json.dumps(row,ensure_ascii=False)+'\n'),
            capture_target_profile_id=target,emit_block=lambda row:blocks.write(json.dumps(row,ensure_ascii=False)+'\n'))
        for file in (stream,blocks):file.flush();os.fsync(file.fileno())
    if result['capture']['sha256']!=request['captureSha256']:raise ValueError('capture-changed-reopen')
    result['sourceVariantsSha256']=VARIANTS_SHA256
    result['responsesSha256']=digest(output/'responses.jsonl')
    result['blocksSha256']=digest(output/'blocks.jsonl')
    if scope:result['sourceCatalog']=scope['source']
    code=Path(__file__).parent
    result['code']=[{'path':'scripts/diagnostics/'+name,'sha256':digest(code/name)} for name in
        ('coding_capture_bridge.py','coding_capture.py','coding_read_plan.py','coding_fields.py','coding_numeric.py',
         'response_values.py','x431_values.py','x431_formula.py', 'source_catalog.py', 'dtc_capture.py')]
    result['code'].append({'path':'scripts/x431_re/request_fields.py',
        'sha256':digest(code.parent/'x431_re/request_fields.py')})
    (output/'report.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    artifact={name:digest(output/name) for name in ('report.json','responses.jsonl','blocks.jsonl')}
    # The complete response stream stays in the temporary artifact; IPC sends
    # coverage and hash-variation counts, not an arbitrarily truncated stream.
    values=result.pop('candidateDataHashes')
    result['changedGroups']=[{'requestHex':key,'distinctValues':len(hashes)} for key,hashes in values.items() if len(hashes)>1]
    return {'report':result,'artifact':artifact}


def verified_files(request):
    folder=Path(request['output']);expected=request['artifact']
    names=({'report.json','responses.jsonl','identity-values.jsonl','identity-profiles.json'} if request.get('kind')=='identity' else
        {'report.json','responses.jsonl','faults.jsonl','dictionary.json'} if request.get('kind')=='dtc' else
        {'report.json','responses.jsonl','values.jsonl','fields.json'} if request.get('kind')=='measurement' else {'report.json','responses.jsonl','blocks.jsonl'})
    if set(expected)!=names:raise ValueError('capture-artifact-invalid')
    for name,value in expected.items():
        if (folder/name).is_symlink() or digest(folder/name)!=value:raise ValueError('capture-artifact-changed')
    return folder


def page(request):
    folder=verified_files(request);cursor=request['cursor'];stream_name=request.get('stream','responses')
    if (stream_name not in ('responses','values','faults','blocks') or (stream_name=='values' and request.get('kind') not in ('measurement','identity'))
            or (stream_name=='faults' and request.get('kind')!='dtc') or (stream_name=='blocks' and request.get('kind') not in (None,'coding'))):raise ValueError('capture-stream-invalid')
    file=folder/('identity-values.jsonl' if stream_name=='values' and request.get('kind')=='identity' else stream_name+'.jsonl')
    if type(cursor) is not int or not 0<=cursor<=file.stat().st_size:raise ValueError('capture-cursor-invalid')
    rows=[]
    with file.open('rb') as stream:
        if cursor:
            stream.seek(cursor-1)
            if stream.read(1)!=b'\n':raise ValueError('capture-cursor-invalid')
        stream.seek(cursor)
        for _ in range(20):
            line=stream.readline(65537)
            if not line:break
            if len(line)>65536 or not line.endswith(b'\n'):raise ValueError('capture-artifact-line-invalid')
            rows.append(json.loads(line))
        next_cursor=stream.tell() if stream.tell()<file.stat().st_size else None
    return {'rows':rows,'nextCursor':next_cursor}


def export(request):
    folder=verified_files(request);destination=Path(request['destination'])
    descriptor,temp=tempfile.mkstemp(prefix='.flat-six-capture-',suffix='.zip',dir=destination.parent)
    os.close(descriptor)
    try:
        with zipfile.ZipFile(temp,'w',compression=zipfile.ZIP_DEFLATED) as archive:
            for name in request['artifact']:archive.write(folder/name,name)
        with zipfile.ZipFile(temp) as archive:
            for name,value in request['artifact'].items():
                with archive.open(name) as stream:
                    if hashlib.file_digest(stream,'sha256').hexdigest()!=value:raise ValueError('capture-export-changed')
        with open(temp,'r+b') as stream:os.fsync(stream.fileno())
        os.replace(temp,destination)
    finally:Path(temp).unlink(missing_ok=True)
    return {'saved':True}


def main():
    try:
        raw=sys.stdin.buffer.read(1024*1024+1)
        if len(raw)>1024*1024:raise ValueError('capture-request-too-large')
        request=json.loads(raw)
        if not isinstance(request,dict):raise ValueError('capture-request-invalid')
        action=request.get('action')
        shapes={'inventory':{'action','capture'},'analyze':{'action','capture','captureSha256','output','profileId','generation','runId'},
            'page':{'action','output','artifact','cursor'},'export':{'action','output','artifact','destination'},
            'scope':{'action','generation','profileId','sourceCatalogId','ecuId'},
            'units':{'action','generation','sourceCatalogId','systemId'}}
        extra={'kind'} if request.get('kind') in ('coding','measurement','dtc','identity') else set()
        if request.get('kind') in (None,'coding') and action=='page' and 'stream' in request:extra.add('stream')
        if request.get('kind') in (None,'coding') and action=='analyze' and any(k in request for k in ('sourceCatalogId','ecuId','captureTargetProfileId')):
            extra|={'sourceCatalogId','ecuId','captureTargetProfileId'}
        if request.get('kind') in ('measurement','dtc','identity') and action=='page':extra.add('stream')
        if request.get('kind')=='identity':
            if action in ('scope','analyze'):
                shapes[action]=shapes[action]-{'profileId'}
            if action=='analyze':extra|={'sourceCatalogId','ecuId','captureTargetProfileId'}
        if request.get('kind')=='dtc' and action=='analyze':extra|={'sourceCatalogId','ecuId','linkIndex','captureTargetProfileId'}
        if action=='scope' and request.get('kind') not in ('coding','dtc','identity'):raise ValueError('capture-request-invalid')
        if action=='units' and request.get('kind')!='coding':raise ValueError('capture-request-invalid')
        if action not in shapes or set(request)!=shapes[action]|extra:raise ValueError('capture-request-invalid')
        if action=='scope':
            library=Path(os.environ.get('PORSCHE981_DEFINITION_ROOT') or Path(__file__).resolve().parents[2])
            if request.get('kind')=='coding':result=coding_scope(request)
            elif request.get('kind')=='identity':
                from .identity_application_capture import catalog_scope
                result=catalog_scope(library,request['sourceCatalogId'],request['generation'],request['ecuId'])
            else:
                from .dtc_capture import source_scope
                result=source_scope(library,request['sourceCatalogId'],request['generation'],request['ecuId'],request['profileId'])
        elif action=='units':result=coding_units(request)
        elif action=='inventory':result=inventory(request['capture'])
        elif action=='analyze':result=analyze(request)
        elif action=='page':result=page(request)
        elif action=='export':result=export(request)
        else:raise ValueError('capture-action-invalid')
        result={'ok':True,**result,**FLAGS}
    except OSError as error:result={'ok':False,'error':'capture-io-'+str(error.errno),**FLAGS}
    except (ValueError,KeyError,TypeError) as error:result={'ok':False,'error':str(error),**FLAGS}
    print(json.dumps(result,ensure_ascii=False))


if __name__=='__main__':main()
