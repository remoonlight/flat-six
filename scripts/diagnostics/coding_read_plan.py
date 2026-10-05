"""Version-specific coding read scopes. No guessed lengths, writes or hardware.

Minimum field spans are not evidence of the ECU's complete coding block length.
An unresolved record prevents describing a read as a complete coding baseline.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json

from .response_values import data_span, request_from_record, normalize_response


def coding_read_plan(variant, generation=None):
    groups, missing = {}, []
    records = variant.get('pool_records', {}).get('coding', {}).get('records', [])
    for record in records:
        request = request_from_record(record)
        if not request.get('ok') or request.get('sid') not in (0x21, 0x22):
            missing.append({'sourceOffset': record.get('at'), 'reason': request.get('reason') or 'coding-read-sid-unqualified'})
            continue
        hx = request['request'].hex().upper()
        group = groups.setdefault(hx, {'requestHex': hx, 'identifierKind': 'DID' if request['sid'] == 0x22 else 'LID',
            'identifierHex': hx[2:], 'minimumKnownBytes': 0, 'expectedTotalBytes': None,
            'fields': [], 'lengthQualified': False})
        span = data_span(record)
        if span:
            group['minimumKnownBytes'] = max(group['minimumKnownBytes'], span['dataMin'])
        else:
            missing.append({'sourceOffset': record.get('at'), 'requestHex': hx, 'reason': 'coding-field-span-unresolved'})
        group['fields'].append({'sourceOffset': record.get('at'), 'byteOffset': record.get('byteOffset'),
            'bitOffset': record.get('bitOffset'), 'span': span, 'source': record.get('source')})
    out = {'kind': 'ecu-coding-read-plan', 'schemaVersion': 1, 'profileId': variant['profile_id'],
        'module': variant['module'], 'variant': variant['name'], 'generation': generation or variant.get('generation'),
        'sourceGeneration': variant.get('generation'),
        'definitionFieldCount': len(records), 'requestGroups': list(groups.values()), 'missing': missing,
        'definitionCoverageComplete': bool(records) and not missing,
        'completeVehicleCodingScopeQualified': False, 'freshReadProven': False,
        'executionEnabled': False, 'liveVerified': False, 'writePayload': None,
        'blockers': ['需本次完整身份、VIN及版本匹配', '需确认各编码块的完整长度及整个编码范围',
                     '需核实诊断头、会话及读取资格；目前不执行车辆原码读取']}
    out['definitionSha256'] = hashlib.sha256(json.dumps(out, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    return out


def read_coding_rehearsal(plan, identity, receive, *, mode='simulation', cancelled=lambda: False):
    """Preserve full returned blocks, stop partial reads, never persist a baseline.

    The plan is server-owned. No caller assertion can promote it to a live read
    or a full original backup. Partial complete blocks remain in memory only.
    """
    out = {'ok': False, 'error': None, 'kind': 'ecu-coding-read-rehearsal', 'profileId': plan.get('profileId'),
        'simulation': True, 'freshReadProven': False, 'completeOriginalBackup': False,
        'baselineCreated': False, 'blocks': [], 'missing': [], 'liveVerified': False, 'writePayload': None}
    if mode != 'simulation':
        out['error'] = 'coding-read-transport-unqualified'
        return out
    try:
        if not (isinstance(identity, dict) and identity.get('profileId') == plan['profileId']
                and identity.get('generation') in ('981', '982') and identity.get('generation') == plan['generation']
                and all(isinstance(identity.get(key), str) and identity[key].strip() for key in ('ecu', 'hardware', 'software'))
                and len(identity.get('vin', '')) == 17):
            raise ValueError('coding-read-identity-mismatch')
        if not plan.get('definitionCoverageComplete') or not plan.get('requestGroups'):
            raise ValueError('coding-read-definition-incomplete')
        for group in plan['requestGroups']:
            if cancelled(): raise ValueError('cancelled')
            hx = group['requestHex']
            raw = bytes.fromhex(hx)
            if not ((raw[0] == 0x21 and len(raw) == 2) or (raw[0] == 0x22 and len(raw) == 3)):
                raise ValueError('coding-read-request-unqualified')
            pdu = receive(hx)
            if cancelled(): raise ValueError('cancelled')
            if not isinstance(pdu, bytes) or not 1 <= len(pdu) <= 4095:
                raise ValueError('coding-read-response-invalid')
            rec = {'read_request_candidate_hex': hx, 'readSID': raw[0], 'pid': int(hx[2:], 16)}
            decoded = normalize_response(pdu, rec, 'pdu', source='injected-coding-read-rehearsal')
            if not decoded.get('ok'): raise ValueError(decoded.get('reason') or 'coding-read-response-invalid')
            block = decoded['payload']
            if len(block) < group['minimumKnownBytes']:
                raise ValueError('coding-read-short-block')
            expected = group.get('expectedTotalBytes')
            if expected is not None and len(block) != expected:
                raise ValueError('coding-read-block-length-mismatch')
            out['blocks'].append({'requestHex': hx, 'identifierKind': group['identifierKind'],
                'identifierHex': group['identifierHex'], 'dataHex': block.hex().upper(), 'pduHex': pdu.hex().upper(),
                'sha256': hashlib.sha256(block).hexdigest(), 'receivedUtc': datetime.now(timezone.utc).isoformat(),
                'synthetic': True, 'completeLengthQualified': False})
        out['ok'] = True
    except (ValueError, KeyError, TypeError, OSError, TimeoutError) as error:
        out['error'] = str(error)
    completed = {block['requestHex'] for block in out['blocks']}
    out['missing'] = [g['requestHex'] for g in plan.get('requestGroups', []) if g['requestHex'] not in completed]
    return out
