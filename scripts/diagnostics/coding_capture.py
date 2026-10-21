"""Offline coding observations from FlatSix application-PDU PCAPNG; no device IO.

Requested profile/run context is not responder identity. Preserve each response,
including repeats and failures; never create a coding baseline or enable writes.
"""
from __future__ import annotations
import argparse
from collections import Counter
import hashlib
import json
import os
from pathlib import Path
import re
import struct

from .coding_read_plan import coding_read_plan
from .response_values import normalize_response
from .capture_clock import valid_capture_clock

MAX_FILE = 512 * 1024 * 1024
MAX_BLOCK = 32 * 1024
VARIANTS_SHA256 = '8a9e0ee94c25e98b11affbc083615bbd4c8113fe9c2a820ca8e4147aa16d5c20'
FLAGS = dict(executionEnabled=False, liveVerified=False, freshReadProven=False,
             baselineCreated=False, completeOriginalBackup=False, writePayload=None)


def digest(file):
    with Path(file).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def options(data):
    out = {}; pos = 0
    while pos + 4 <= len(data):
        code, size = struct.unpack_from('<HH', data, pos); pos += 4
        if not code:
            if size or any(data[pos:]): raise ValueError('capture-options-end-invalid')
            return out
        padded = (size + 3) & ~3
        if pos+padded > len(data): raise ValueError('capture-option-truncated')
        out.setdefault(code, []).append(data[pos:pos+size]); pos += padded
    if pos != len(data): raise ValueError('capture-options-truncated')
    return out


def valid_context(meta, stamp):
    if not isinstance(meta, dict): return False
    def integer(n): return type(n) is int and 0 < n <= 9007199254740991
    request = meta.get('requestHex')
    if not (request is None or isinstance(request,str) and re.fullmatch(r'(?:[0-9A-F]{2}){1,7}', request)): return False
    if type(meta.get('timestampUs')) is not int or meta['timestampUs'] != stamp or not integer(stamp) or meta.get('timestampSource') != 'host-pdu-observed': return False
    start = meta.get('startedTimestampUs', stamp)
    if (not integer(start) or (start > stamp and 'startedMonotonicTimestampUs' not in meta)
            or not valid_capture_clock(meta) or type(meta.get('simulation')) is not bool
            or type(meta.get('duringCleanup')) is not bool): return False
    if not all(isinstance(meta.get(k),str) and 0 < len(meta[k]) <= 240 for k in ('targetProfileId','runId')): return False
    if meta.get('transportKind') == 'elm-iso-tp': return meta.get('observation') == 'reassembled-complete-pdu'
    return (((meta.get('transportKind') == 'd-pdu-iso-tp' and meta.get('observation') == 'native-result-data')
        or (meta.get('transportKind') == 'd-pdu-mode50' and meta.get('observation') == 'reassembled-complete-pdu'))
        and all(type(meta.get(k)) is int and 0 <= meta[k] <= 0xffffffff for k in ('primitive','uniqueId'))
        and ('expectedPrimitive' not in meta or type(meta['expectedPrimitive']) is int and 0 <= meta['expectedPrimitive'] <= 0xffffffff)
        and isinstance(meta.get('flagsHex'),str) and re.fullmatch(r'(?:[0-9A-F]{2}){0,16}',meta['flagsHex']) is not None)


def native_result_error(meta):
    if meta['transportKind'] not in ('d-pdu-iso-tp','d-pdu-mode50'):return None
    if 'expectedPrimitive' not in meta:return 'native-primitive-context-missing'
    if meta['expectedPrimitive']!=meta['primitive']:return 'native-primitive-mismatch'
    if meta['uniqueId']!=1 or any(bytes.fromhex(meta['flagsHex'])):return 'native-result-unqualified'
    return None


def scan_capture(file, visit):
    """Read bounded blocks; keep a valid prefix when an interrupted tail exists.

    Only Upper PDU/data captures with complete packets and matching host-time
    comments are accepted. A corrupt/unsupported block stops parsing; no resync.
    """
    file = Path(file); before = file.stat()
    if not 0 < before.st_size <= MAX_FILE: raise ValueError('capture-file-size-limit')
    source_hash = digest(file)
    summary = dict(bytes=before.st_size, sha256=source_hash, packets=0, packetErrors=0,
                   prefixComplete=True, stoppedAt=None, error=None, syntheticSection=False,endStatisticsPresent=False)
    section = False; interface = False; offset = 0
    with file.open('rb') as stream:
        while offset < before.st_size:
            try:
                head = stream.read(8)
                if len(head) != 8: raise ValueError('capture-block-header-truncated')
                kind, length = struct.unpack('<II', head)
                if length < 12 or length % 4 or length > MAX_BLOCK: raise ValueError('capture-block-length-invalid')
                rest = stream.read(length-8)
                if len(rest) != length-8: raise ValueError('capture-block-truncated')
                if struct.unpack_from('<I', rest, len(rest)-4)[0] != length: raise ValueError('capture-block-trailer-mismatch')
                body = rest[:-4]
                if kind == 0x0a0d0d0a:
                    if section or offset or len(body)<16 or struct.unpack_from('<IHH',body)!=(0x1a2b3c4d,1,0):
                        raise ValueError('capture-section-unsupported')
                    desc = options(body[16:]).get(1,[])
                    summary['syntheticSection'] = any(b'SYNTHETIC;' in item for item in desc)
                    section = True
                elif not section: raise ValueError('capture-section-missing')
                elif kind == 1:
                    if interface or len(body)<8: raise ValueError('capture-interface-unsupported')
                    link, reserved, snap = struct.unpack_from('<HHI',body)
                    opts=options(body[8:])
                    if link!=252 or reserved or snap!=8192 or opts.get(9)!=[b'\x06']:
                        raise ValueError('capture-application-interface-required')
                    interface = True
                elif kind == 6:
                    if not interface or len(body)<20: raise ValueError('capture-packet-header-invalid')
                    net, high, low, captured, original = struct.unpack_from('<IIIII',body)
                    if net or captured!=original or not 13<=captured<=4107 or 20+((captured+3)&~3)>len(body):
                        raise ValueError('capture-packet-length-invalid')
                    summary['packets'] += 1
                    packet=body[20:20+captured]; stamp=(high<<32)|low
                    row={'packet':summary['packets'],'fileOffset':offset,'timestampUs':stamp,'packetHex':packet.hex().upper()}
                    try:
                        if packet[:12]!=b'\x00\x0c\x00\x04data\x00\x00\x00\x00':
                            raise ValueError('capture-data-dissector-required')
                        row['pduHex']=packet[12:].hex().upper()
                        comments=options(body[20+((captured+3)&~3):]).get(1,[])
                        if len(comments)!=1: raise ValueError('capture-context-required')
                        meta=json.loads(comments[0].decode('utf-8'))
                        if not valid_context(meta,stamp): raise ValueError('capture-context-invalid')
                        row.update(context=meta,synthetic=summary['syntheticSection'] or meta['simulation'])
                    except (ValueError,UnicodeError) as error:
                        row['error']=str(error);summary['packetErrors']+=1
                    visit(row)
                elif kind == 5:
                    if not interface or len(body)<12: raise ValueError('capture-statistics-invalid')
                    if struct.unpack_from('<I',body)[0] != 0:
                        raise ValueError('capture-statistics-interface-invalid')
                    received = options(body[12:]).get(4, [])
                    if len(received) != 1 or len(received[0]) != 8:
                        raise ValueError('capture-statistics-count-invalid')
                    if struct.unpack('<Q',received[0])[0] != summary['packets']:
                        raise ValueError('capture-statistics-count-mismatch')
                    # This project's writer emits one closing ISB. A marker
                    # before more blocks does not close the observed prefix.
                    if offset + length != before.st_size:
                        raise ValueError('capture-statistics-not-final')
                    summary['endStatisticsPresent']=True
                else: raise ValueError('capture-block-unsupported')
                offset += length
            except (ValueError,struct.error) as error:
                summary.update(prefixComplete=False,stoppedAt=offset,error=str(error));break
    after=file.stat()
    if (before.st_size,before.st_mtime_ns,before.st_ino)!=(after.st_size,after.st_mtime_ns,after.st_ino):
        raise ValueError('capture-changed-during-analysis')
    if not section or not interface:
        summary.update(prefixComplete=False,error=summary['error'] or 'capture-application-interface-missing')
    return summary


class CodingBlockSet:
    """Reconcile complete PDUs, never concatenate unrelated response payloads.

    Retain one representative per request/provenance and permanent failure or
    conflict state. The full response stream remains the authoritative history.
    Consistent source groups are not an atomic read or a complete coding scope.
    """
    def __init__(self, groups):
        self.groups = groups
        self.inputs = {'observed': {}, 'synthetic': {}}

    def observe(self, row, data, reason):
        category = 'synthetic' if row['synthetic'] else 'observed'
        value = self.inputs[category].setdefault(row['requestHex'], {
            'observations': 0, 'validResponses': 0, 'failures': {}, 'conflicting': False,
            'firstPacket': row['packet'], 'lastPacket': row['packet'],
            'representative': None})
        value['observations'] += 1
        value['lastPacket'] = row['packet']
        if reason:
            value['failures'][reason] = value['failures'].get(reason, 0) + 1
            return
        value['validResponses'] += 1
        block = {'dataHex': data.hex().upper(), 'dataSha256': hashlib.sha256(data).hexdigest(),
                 'pduHex': row['pduHex'], 'packet': row['packet'], 'timestampUs': row['timestampUs']}
        if value['representative'] is None:
            value['representative'] = block
        elif value['representative']['dataSha256'] != block['dataSha256']:
            value['conflicting'] = True

    def finish(self, capture, definition_missing, emit):
        integrity = capture['prefixComplete'] and not capture['packetErrors'] and capture['endStatisticsPresent']
        reports = []
        for category, inputs in self.inputs.items():
            counts = Counter()
            for request, group in self.groups.items():
                value = inputs.get(request, {'observations': 0, 'validResponses': 0, 'failures': {},
                    'conflicting': False, 'firstPacket': None, 'lastPacket': None, 'representative': None})
                status = ('missing' if not value['observations'] else 'conflicting' if value['conflicting']
                          else 'failed' if value['failures'] else 'consistent')
                counts[status] += 1
                emit({'requestHex': request, 'source': category, 'status': status, **value,
                    'minimumKnownBytes': group['minimumKnownBytes'],
                    'expectedTotalBytes': group.get('expectedTotalBytes'),
                    'completeLengthQualified': False, 'atomicReadProven': False, **FLAGS})
            reports.append({'source': category, 'counts': dict(counts),
                'consistentReadSetAvailable': bool(self.groups) and integrity and not definition_missing
                    and counts['consistent'] == len(self.groups),
                'atomicReadProven': False, 'completeLengthQualified': False, **FLAGS})
        return {'captureIntegrityComplete': integrity, 'reports': reports,
                'representativeIsSelectedOriginal': False, 'atomicReadProven': False, **FLAGS}


def analyze_capture(file, plan, run_id, emit, *, capture_target_profile_id=None, emit_block=lambda row: None):
    groups={g['requestHex']:g for g in plan['requestGroups']};counts=Counter(); hashes={};sessions={}
    target = plan['profileId'] if capture_target_profile_id is None else capture_target_profile_id
    blocks = CodingBlockSet(groups)
    def visit(row):
        if row.get('error'):
            emit({**row,'status':'capture-packet-error'});return
        meta=row['context'];key=(meta['targetProfileId'],meta['runId'])
        sessions[key]=sessions.get(key,0)+1
        if key!=(target,run_id):return
        request=meta['requestHex']
        if request not in groups:return
        item={**row,'requestHex':request};group=groups[request];reason=None;data=None
        if meta['duringCleanup']:reason='cleanup-response-unqualified'
        elif native_result_error(meta):reason=native_result_error(meta)
        else:
            req=bytes.fromhex(request)
            record={'read_request_candidate_hex':request,'readSID':req[0],'pid':int(request[2:],16)}
            response=normalize_response(bytes.fromhex(row['pduHex']),record,'pdu',source='imported-application-capture')
            if not response.get('ok'):reason=response.get('reason') or 'coding-response-invalid'
            else:
                data=response['payload'];expected=group.get('expectedTotalBytes')
                if len(data)<group['minimumKnownBytes']:reason='coding-read-short-block'
                elif expected is not None and len(data)!=expected:reason='coding-read-block-length-mismatch'
                else:
                    value_hash=hashlib.sha256(data).hexdigest()
                    item.update(dataHex=data.hex().upper(),dataSha256=value_hash,completeLengthQualified=False)
                    if not row['synthetic']:hashes.setdefault(request,set()).add(value_hash)
        blocks.observe(item, data, reason)
        item.update(status='synthetic-capture-excluded' if row['synthetic'] else reason or 'candidate-block',
                    validationError=reason, **FLAGS)
        counts[item['status']]+=1;emit(item)
    capture=scan_capture(file,visit)
    missing=[hx for hx in groups if hx not in hashes]
    return {'kind':'captured-coding-observations','schemaVersion':1,'profileId':plan['profileId'],
        'generation':plan['generation'],'runId':run_id,'definitionSha256':plan['definitionSha256'],
        'captureTargetProfileId':target,'sourceProfileId':plan['profileId'],
        'blockSet':blocks.finish(capture,plan['missing'],emit_block),
        'definitionFieldCount':plan['definitionFieldCount'],'requestGroupCount':len(groups),
        'definitionMissing':plan['missing'],'missingReadRequests':missing,
        'allDefinedGroupsObserved':bool(groups) and not missing and not plan['missing'],
        'candidateDataHashes':{hx:sorted(values) for hx,values in hashes.items()},'counts':dict(counts),
        'capture':capture,'availableSessions':[dict(targetProfileId=p,runId=r,packets=n) for (p,r),n in sessions.items()],
        'requestedTargetIsResponderProof':False,'identityQualified':False,'provenance':'imported-capture-unqualified',**FLAGS}


def load_variant(variants, profile, generation):
    if generation not in ('981','982'):raise ValueError('capture-generation-invalid')
    selected=None;hasher=hashlib.sha256()
    with Path(variants).open('rb') as stream:
        for line in stream:
            hasher.update(line)
            variant=json.loads(line)
            if variant.get('profile_id')==profile:
                if selected is not None:raise ValueError('coding-profile-ambiguous')
                selected=variant
    if hasher.hexdigest()!=VARIANTS_SHA256:raise ValueError('coding-source-hash-mismatch')
    if selected is None:raise ValueError('coding-profile-missing')
    if selected.get('generation') not in (None,'9x1',generation):raise ValueError('coding-generation-mismatch')
    from scripts.x431_re.request_fields import coding_profile_variant
    return coding_profile_variant(selected)


def load_plan(variants, profile, generation):
    return coding_read_plan(load_variant(variants,profile,generation),generation)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--capture',type=Path,required=True);parser.add_argument('--list-sessions',action='store_true')
    parser.add_argument('--variants',type=Path);parser.add_argument('--profile');parser.add_argument('--generation',choices=['981','982'])
    parser.add_argument('--run-id');parser.add_argument('--output-dir',type=Path)
    args=parser.parse_args()
    if args.list_sessions:
        sessions=Counter()
        def inventory(row):
            if not row.get('error'):
                meta=row['context'];sessions[(meta['targetProfileId'],meta['runId'],row['synthetic'])]+=1
        capture=scan_capture(args.capture,inventory)
        print(json.dumps({'capture':capture,'sessions':[dict(targetProfileId=p,runId=r,synthetic=s,packets=n)
            for (p,r,s),n in sessions.items()],**FLAGS}))
        return 0 if capture['prefixComplete'] and not capture['packetErrors'] else 2
    if any(getattr(args,k) is None for k in ('variants','profile','generation','run_id','output_dir')):
        parser.error('analysis requires --variants, --profile, --generation, --run-id and --output-dir')
    plan=load_plan(args.variants,args.profile,args.generation)
    args.output_dir.mkdir(parents=True,exist_ok=False)
    with (args.output_dir/'responses.jsonl').open('x',encoding='utf-8') as output, (args.output_dir/'blocks.jsonl').open('x',encoding='utf-8') as blocks:
        result=analyze_capture(args.capture,plan,args.run_id,lambda row:output.write(json.dumps(row,ensure_ascii=False)+'\n'),
            emit_block=lambda row:blocks.write(json.dumps(row,ensure_ascii=False)+'\n'))
        for stream in (output,blocks):stream.flush();os.fsync(stream.fileno())
    result['sourceVariantsSha256']=VARIANTS_SHA256
    result['responsesSha256']=digest(args.output_dir/'responses.jsonl')
    result['blocksSha256']=digest(args.output_dir/'blocks.jsonl')
    code=Path(__file__).parent
    result['code']=[{'path':'scripts/diagnostics/'+name,'sha256':digest(code/name)} for name in
        ('coding_capture.py','coding_read_plan.py','coding_fields.py','coding_numeric.py',
         'response_values.py','x431_values.py','x431_formula.py')]
    result['code'].append({'path':'scripts/x431_re/request_fields.py',
        'sha256':digest(code.parent/'x431_re/request_fields.py')})
    (args.output_dir/'report.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({'output':str(args.output_dir),'prefixComplete':result['capture']['prefixComplete'],
        'candidateGroups':len(result['candidateDataHashes']),'missingGroups':len(result['missingReadRequests']),**FLAGS}))
    return 0 if result['capture']['prefixComplete'] and not result['capture']['packetErrors'] else 2


if __name__=='__main__':raise SystemExit(main())
