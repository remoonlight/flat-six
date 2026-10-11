"""Version-specific coding read scopes. No guessed lengths, writes or hardware.

Minimum field spans are not evidence of the ECU's complete coding block length.
An unresolved record prevents describing a read as a complete coding baseline.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import re
from collections import Counter
from .coding_fields import coding_field_readiness

from .response_values import data_span, request_from_record, normalize_response
from scripts.x431_re.request_fields import coding_profile_variant


def coding_read_plan(variant, generation=None, *, field_readiness=coding_field_readiness):
    variant = coding_profile_variant(variant)
    groups, missing, editor_counts = {}, [], Counter()
    records = variant.get('pool_records', {}).get('coding', {}).get('records', [])
    for record in records:
        readiness = field_readiness(record)
        ready_kind = ('source-bounded-linear-preview-ready'
                      if readiness.get('previewKind') == 'source-bounded-linear' else 'named-options-ready')
        editor_counts[readiness['editorBlocker'] or ready_kind] += 1
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
        origin = record.get('codingReadOrigin')
        if origin:
            group['readOrigin'] = {k: v for k, v in origin.items() if k != 'recordIndex'}
        group['fields'].append({'sourceOffset': record.get('at'), 'byteOffset': record.get('byteOffset'),
            'bitOffset': record.get('bitOffset'), 'span': span, 'source': record.get('source'), **readiness})
        if origin:
            group['fields'][-1].update(originalPrefixHex=record['request_prefix_hex'],
                originalRecordReadSID=record['readSID'], originalCandidateHex=record['sourceReadRequestCandidateHex'],
                sourceRecordIndex=origin['recordIndex'])
    ordered_groups = sorted(groups.values(), key=lambda g: g['requestHex']) if records and records[0].get('codingReadOrigin') else list(groups.values())
    out = {'kind': 'ecu-coding-read-plan', 'schemaVersion': 1, 'profileId': variant['profile_id'],
        'module': variant['module'], 'variant': variant['name'], 'generation': generation or variant.get('generation'),
        'sourceGeneration': variant.get('generation'),
        'definitionFieldCount': len(records), 'requestGroups': ordered_groups, 'missing': missing,
        'editorReadiness': dict(editor_counts), 'previewReadiness': dict(editor_counts),
        'editorReadinessScope': 'offline-field-preview-only',
        'nativeEditingBehaviorVerified': False, 'codingWriteQualified': False, 'writeRecipeQualified': False,
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


def coding_read_observation(plan, identity, responses):
    """Validate imported complete PDUs against a server-owned scope.

    Missing/negative/short responses stay explicit and cannot become a baseline.
    Imported identity, timestamps and lengths never qualify a live write.
    """
    if (not isinstance(identity, dict) or set(identity) != {'generation', 'vin', 'ecu', 'hardware', 'software'}
            or identity.get('generation') != plan['generation']
            or not re.fullmatch(r'[A-HJ-NPR-Z0-9]{17}', identity.get('vin', ''))
            or not all(isinstance(identity.get(k), str) and 0 < len(identity[k]) <= 240 for k in ('ecu', 'hardware', 'software'))):
        raise ValueError('coding-observation-identity-invalid')
    if not isinstance(responses, list) or len(responses) > 256:
        raise ValueError('coding-observation-responses-invalid')
    groups = {g['requestHex']: g for g in plan['requestGroups']}
    seen, blocks, failures, total = set(), [], [], 0
    for item in responses:
        if not isinstance(item, dict) or set(item) != {'requestHex', 'pduHex', 'receivedUtc'}:
            raise ValueError('coding-observation-response-invalid')
        hx, pdu = item['requestHex'], item['pduHex']
        if not isinstance(hx, str) or hx not in groups or hx in seen:
            raise ValueError('coding-observation-request-mismatch')
        seen.add(hx)
        if not isinstance(pdu, str) or not re.fullmatch(r'(?:[0-9A-F]{2}){1,4095}', pdu):
            raise ValueError('coding-observation-pdu-invalid')
        total += len(pdu)
        if total > 128 * 1024:
            raise ValueError('coding-observation-size-limit')
        try:
            stamp = datetime.fromisoformat(item['receivedUtc'].replace('Z', '+00:00'))
            if stamp.tzinfo is None:
                raise ValueError()
        except (ValueError, TypeError, AttributeError):
            raise ValueError('coding-observation-time-invalid')
        raw = bytes.fromhex(hx)
        rec = {'read_request_candidate_hex': hx, 'readSID': raw[0], 'pid': int(hx[2:], 16)}
        result = normalize_response(bytes.fromhex(pdu), rec, 'pdu', source='imported-coding-observation')
        reason = result.get('reason') if not result.get('ok') else None
        payload = result.get('payload', b'')
        group = groups[hx]
        if not reason and len(payload) < group['minimumKnownBytes']:
            reason = 'coding-read-short-block'
        if reason:
            failures.append({**item, 'reason': reason})
        else:
            blocks.append({**item, 'identifierKind': group['identifierKind'], 'identifierHex': group['identifierHex'],
                'dataHex': payload.hex().upper(), 'sha256': hashlib.sha256(payload).hexdigest(),
                'completeLengthQualified': False})
    completed = {b['requestHex'] for b in blocks}
    missing = [hx for hx in groups if hx not in completed]
    return {'kind': 'ecu-coding-read-observation', 'schemaVersion': 1, 'profileId': plan['profileId'],
        'generation': plan['generation'], 'definitionSha256': plan['definitionSha256'],
        'identitySha256': hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest(),
        'blocks': blocks, 'failures': failures, 'missing': missing, 'definitionMissing': plan['missing'],
        'allDefinedGroupsReceived': bool(groups) and not missing and not plan['missing'],
        'provenance': 'imported-unqualified', 'freshReadProven': False, 'baselineCreated': False,
        'completeOriginalBackup': False, 'executionEnabled': False, 'liveVerified': False, 'writePayload': None}
