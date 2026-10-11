"""Decode all source measurement fields after disconnect; never send vehicle data.

One complete captured PDU can contain fields beyond the 12 selected at collection.
Requested profile/run metadata is not ECU identity. Synthetic/cleanup/native-invalid
responses remain explicit observations; no output qualifies a vehicle or coding.
"""
from __future__ import annotations
import argparse
from collections import Counter
import hashlib
import json
import os
from pathlib import Path
from functools import lru_cache

from .coding_capture import FLAGS, VARIANTS_SHA256, digest, load_variant, native_result_error, scan_capture
from .realtime_preparation import display_name
from .response_values import data_span, decode_application_response, normalize_response, request_from_record
from .x431_formula import formula_from_record
from .x431_values import classify_decode
from .x431_variant_selection import REVIEWED_LANGUAGE_SHA256
from scripts.x431_re.gag_lib import GgpLanguage, compact_text


def language_lookup(file):
    if digest(file)!=REVIEWED_LANGUAGE_SHA256:raise ValueError('measurement-language-hash-mismatch')
    language=GgpLanguage(file)
    @lru_cache(maxsize=8192)
    def lookup(key):return compact_text(language.lookup_id('DSTREAM_CN.GAG',key))
    return lookup


def field_plan(variant, generation, lookup=None):
    fields=[];groups={};missing=[]
    records=variant.get('pool_records',{}).get('measurement',{}).get('records',[])
    for position,record in enumerate(records):
        request=request_from_record(record)
        hx=request['request'].hex().upper() if request.get('ok') else None
        _text,parsed=formula_from_record(record)
        classified=classify_decode(parsed,record.get('byteOffset'),record.get('bitOffset'))
        span=data_span(record)
        name,resolved=display_name({**record,'requestHex':hx},lookup)
        # Same source-position key as the full desktop catalog. Repeated source
        # definitions retain their own offsets/category, never silently merge.
        key=hashlib.sha256(json.dumps([variant['profile_id'],position,record],sort_keys=True,ensure_ascii=False).encode()).hexdigest()
        reason=request.get('reason') if not request.get('ok') else classified.get('reason') if not classified.get('ok') else None
        if reason is None and span is None:reason='measurement-field-span-unresolved'
        item={'id':key,'sourcePosition':position,'sourceOffset':record.get('at'),'source':record.get('source'),
            'name':name,'originalName':record.get('name'),'nameResolved':resolved,'unit':record.get('unit'),
            'groupIdHex':record.get('group_id_hex') or record.get('groupIdHex'),
            'requestHex':hx,'byteOffset':record.get('byteOffset'),'bitOffset':record.get('bitOffset'),
            'dataSpan':span,'decoderReady':reason is None,'decoderIssue':reason,'record':record}
        fields.append(item)
        if reason:missing.append({'parameterId':key,'sourceOffset':record.get('at'),'requestHex':hx,'reason':reason})
        if hx:groups.setdefault(hx,[]).append(item)
    return {'kind':'full-source-measurement-plan','schemaVersion':1,'profileId':variant['profile_id'],
        'generation':generation,'sourceGeneration':variant.get('generation'),'module':variant.get('module'),
        'variant':variant.get('name'),'sourceFieldCount':len(fields),'fields':fields,'groups':groups,'definitionMissing':missing,
        'nameMissing':[{'parameterId':p['id'],'sourceOffset':p['sourceOffset'],'originalName':p['originalName']} for p in fields if not p['nameResolved']],
        'selectionLimit':None,'identityQualified':False,**FLAGS}


def analyze(file, plan, run_id, emit_response, emit_value):
    counts=Counter();field_counts=Counter();observed=set();decoded=set();synthetic_decoded=set()
    sessions=Counter();first=None;last=None;reversals=0;matched=0
    def visit(row):
        nonlocal first,last,reversals,matched
        if row.get('error'):
            emit_response({**row,'status':'capture-packet-error','scopeAssigned':False,**FLAGS});counts['capture-packet-error']+=1;return
        meta=row['context'];sessions[(meta['targetProfileId'],meta['runId'])]+=1
        if (meta['targetProfileId'],meta['runId'])!=(plan['profileId'],run_id):return
        matched+=1;stamp=row['timestampUs']
        if first is None:first=stamp
        if last is not None and stamp<last:reversals+=1
        last=stamp;hx=meta['requestHex'];group=plan['groups'].get(hx,[])
        if not group:
            counts['request-not-in-measurement-scope']+=1
            emit_response({**row,'status':'request-not-in-measurement-scope','requestHex':hx,**FLAGS});return
        reason=None
        if meta['duringCleanup']:reason='cleanup-response-unqualified'
        else:reason=native_result_error(meta)
        pdu=bytes.fromhex(row['pduHex'])
        if not reason:
            normalized=normalize_response(pdu,group[0]['record'],'pdu',source='imported-application-capture')
            if not normalized.get('ok'):reason=normalized.get('reason') or 'measurement-response-invalid'
        status=reason or ('synthetic-response' if row['synthetic'] else 'response-observed')
        counts[status]+=1
        emit_response({**row,'requestHex':hx,'status':status,'sourceFieldCount':len(group),**FLAGS})
        if not reason and not row['synthetic']:observed.add(hx)
        for parameter in group:
            item={'packet':row['packet'],'fileOffset':row['fileOffset'],'timestampUs':stamp,
                'startedTimestampUs':meta.get('startedTimestampUs',stamp),'timestampSource':meta['timestampSource'],
                'elapsedHostMs':(stamp-first)/1000,'requestHex':hx,'profileId':plan['profileId'],'runId':run_id,
                'parameterId':parameter['id'],'sourceOffset':parameter['sourceOffset'],
                'name':parameter['name'],'unit':parameter['unit'],'synthetic':row['synthetic'],
                'identityQualified':False,'vehicleValueQualified':False,**FLAGS}
            failure=reason or parameter['decoderIssue']
            if failure:result={'ok':False,'reason':failure,**FLAGS}
            else:
                try:result=decode_application_response(parameter['record'],pdu,'pdu',source='synthetic-capture' if row['synthetic'] else 'imported-application-capture')
                except (ValueError,TypeError,ArithmeticError,UnicodeError) as error:
                    result={'ok':False,'reason':'measurement-decode-error-'+type(error).__name__,**FLAGS}
                # Evidence shared in report; do not repeat it for every sample.
                result.pop('evidence',None)
                try:json.dumps(result,allow_nan=False)
                except (ValueError,TypeError):result={'ok':False,'reason':'measurement-result-not-json-finite',**FLAGS}
            item['result']=result
            field_counts[('synthetic-' if row['synthetic'] else 'observed-')+('decoded' if result.get('ok') else 'failed')]+=1
            if result.get('ok'):(synthetic_decoded if row['synthetic'] else decoded).add(parameter['id'])
            emit_value(item)
    capture=scan_capture(file,visit)
    return {'kind':'captured-measurement-analysis','schemaVersion':1,'profileId':plan['profileId'],
        'generation':plan['generation'],'runId':run_id,'sourceFieldCount':plan['sourceFieldCount'],
        'sourceGeneration':plan['sourceGeneration'],'variant':plan['variant'],'module':plan['module'],
        'sourceGenerationIsSelectedGeneration':plan['sourceGeneration']==plan['generation'],
        'requestGroupCount':len(plan['groups']),'counts':dict(counts),'fieldCounts':dict(field_counts),
        'matchedPackets':matched,'observedRequestCount':len(observed),'decodedSourceFieldCount':len(decoded),
        'syntheticDecodedSourceFieldCount':len(synthetic_decoded),'definitionMissing':plan['definitionMissing'],
        'nameMissing':plan['nameMissing'],
        'missingReadRequests':[hx for hx in plan['groups'] if hx not in observed],
        'missingDecodedParameterIds':[p['id'] for p in plan['fields'] if p['id'] not in decoded],
        'firstHostTimestampUs':first,'lastHostTimestampUs':last,'hostTimeReversalCount':reversals,
        'timeBasis':'host-observed-complete-PDU; not ECU sampling time; file order preserved',
        'availableSessions':[dict(targetProfileId=p,runId=r,packets=n) for (p,r),n in sessions.items()],
        'capture':capture,'identityQualified':False,'requestedTargetIsResponderProof':False,
        'vehicleValuesQualified':False,'selectionLimit':None,**FLAGS}


def write_analysis(capture, variants, profile, generation, run_id, output_dir, language=None, expected_capture_sha256=None):
    output_dir=Path(output_dir)
    explicit_language=language is not None or bool(os.environ.get('PORSCHE981_LANGUAGE'))
    language=Path(language or os.environ.get('PORSCHE981_LANGUAGE') or Path(__file__).resolve().parents[2]/'.local/x431-re/2026-09-27-protocol/package/PORSCHE_CN.GGP')
    lookup=language_lookup(language) if explicit_language or language.is_file() else None
    plan=field_plan(load_variant(variants,profile,generation),generation,lookup)
    if lookup and digest(language)!=REVIEWED_LANGUAGE_SHA256:raise ValueError('measurement-language-changed')
    output_dir.mkdir(parents=True,exist_ok=False)
    def write(stream,value):stream.write(json.dumps(value,ensure_ascii=False,allow_nan=False)+'\n')
    with (output_dir/'responses.jsonl').open('x',encoding='utf-8') as responses,(output_dir/'values.jsonl').open('x',encoding='utf-8') as values:
        report=analyze(capture,plan,run_id,lambda row:write(responses,row),lambda row:write(values,row))
        for stream in (responses,values):stream.flush();os.fsync(stream.fileno())
    if expected_capture_sha256 is not None and report['capture']['sha256']!=expected_capture_sha256:raise ValueError('capture-changed-reopen')
    report['sourceVariantsSha256']=VARIANTS_SHA256
    report['sourceLanguageSha256']=REVIEWED_LANGUAGE_SHA256 if lookup else None
    report['artifacts']={name:digest(output_dir/name) for name in ('responses.jsonl','values.jsonl')}
    code=Path(__file__).parent
    names=['scripts/diagnostics/'+name for name in ('measurement_capture.py','coding_capture.py','coding_read_plan.py',
        'realtime_preparation.py','response_values.py','x431_values.py','x431_formula.py','x431_variant_selection.py')]+[
        'scripts/x431_re/'+name for name in ('gag_lib.py','gag_codec.py','ggp_index.py','rijndael256.py')]
    report['code']=[{'path':name,'sha256':digest(code.parents[1]/name)} for name in names]
    definition_keys=('name','unit','labels','readSID','rawSID','wireSID','pid','byteOffset','bitOffset',
        'formula','enumText','disabledreadrequestcandidate','read_request_candidate_hex')
    catalog={**plan,'fields':[{**{k:v for k,v in p.items() if k!='record'},
        'decodingDefinition':{k:v for k,v in p['record'].items() if k in definition_keys}} for p in plan['fields']],
        'groups':{hx:[p['id'] for p in ps] for hx,ps in plan['groups'].items()}}
    (output_dir/'fields.json').write_text(json.dumps(catalog,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    report['artifacts']['fields.json']=digest(output_dir/'fields.json')
    (output_dir/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    return report


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--capture',type=Path,required=True);parser.add_argument('--variants',type=Path,required=True)
    parser.add_argument('--profile',required=True);parser.add_argument('--generation',choices=['981','982'],required=True)
    parser.add_argument('--run-id',required=True);parser.add_argument('--output-dir',type=Path,required=True)
    parser.add_argument('--language',type=Path)
    args=parser.parse_args()
    report=write_analysis(args.capture,args.variants,args.profile,args.generation,args.run_id,args.output_dir,args.language)
    print(json.dumps({'output':str(args.output_dir),'sourceFields':report['sourceFieldCount'],
        'decodedSourceFields':report['decodedSourceFieldCount'],'syntheticDecodedSourceFields':report['syntheticDecodedSourceFieldCount'],
        'missingRequests':len(report['missingReadRequests']),'capturePrefixComplete':report['capture']['prefixComplete'],**FLAGS}))
    return 0 if report['capture']['prefixComplete'] and not report['capture']['packetErrors'] else 2


if __name__=='__main__':raise SystemExit(main())
