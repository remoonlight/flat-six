"""Independent, bounded source-field checks; no device or captured-value oracle."""
from __future__ import annotations

import hashlib
import json
import os
from collections import Counter
from pathlib import Path
import time

from .decoder_reference import reference_cases, reference_value, compare_reference
from .response_values import decode_application_response, request_from_record, data_span
from scripts.x431_re.request_fields import coding_profile_variant


def record_hash(record):
    return hashlib.sha256(json.dumps(record, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def audit_field(record):
    """Unsupported reference semantics stay explicit, never count as a match."""
    result = {'status': 'unverified', 'cases': 0, 'responseChecks': 0,
              'fieldStatus': 'unverified', 'responseStatus': 'unverified',
              'mismatches': [], 'vehicleVerified': False,
              'method': 'literal-formula/individual-bits/rational-arithmetic'}
    try:
        request = request_from_record(record)
        result['readRequestStatus'] = 'resolved' if request.get('ok') else 'unresolved'
        result['readRequestReason'] = None if request.get('ok') else request.get('reason') or 'unknown'
        span = data_span(record)
        if not span:
            raise ValueError('reference-layout-unresolved')
        cases = reference_cases(record)
        if not cases:
            raise ValueError('reference-no-boundary-cases')
        for data in cases:
            expected = reference_value(record, data)
            actual = decode_application_response(record, data, 'data', source='synthetic-reference-audit')
            differences = compare_reference(expected, actual)
            result['cases'] += 1
            if differences:
                result['mismatches'].append({'dataHex': data.hex().upper(), 'keys': differences})
        result['fieldStatus'] = 'mismatch' if result['mismatches'] else 'matched'
        if not request.get('ok'):
            result['reason'] = 'reference-read-request-unresolved:' + str(request.get('reason') or 'unknown')
            if result['mismatches']:
                result['status'] = 'mismatch'
            return result
        field_mismatches = len(result['mismatches'])
        header = bytes([request['sid'] + 0x40]) + request['request'][1:]
        bad_responses = [('wrong-identifier', header[:-1] + bytes([header[-1] ^ 1]) + bytes(span['dataMin']))]
        if span['dataMin']:
            bad_responses.append(('short', header + bytes(span['dataMin'] - 1)))
        bad_responses += [(f'nrc-{nrc:02X}', bytes([0x7F, request['sid'], nrc])) for nrc in (0x11, 0x31, 0x78)]
        for label, data in bad_responses:
            actual = decode_application_response(record, data, 'pdu')
            result['responseChecks'] += 1
            if actual.get('ok') or (label.startswith('nrc-') and actual.get('nrc') != data[-1]):
                result['mismatches'].append({'response': label, 'dataHex': data.hex().upper()})
        result['responseStatus'] = 'mismatch' if len(result['mismatches']) > field_mismatches else 'matched'
        result['status'] = 'mismatch' if result['mismatches'] else 'matched'
    except (ValueError, TypeError, KeyError, OverflowError, ZeroDivisionError) as error:
        # Errors in the limited reference implementation do not certify production.
        result['reason'] = str(error) or type(error).__name__
        if result['mismatches']:
            result['status'] = 'mismatch'
            if result['fieldStatus'] == 'unverified':
                result['fieldStatus'] = 'mismatch'
    return result


class DefinitionAuditor:
    def __init__(self):
        self.cache = {}
        self.counts = Counter()
        self.reasons = Counter()

    def check(self, record):
        # Names/provenance may differ; calculation semantics must match exactly.
        semantics = {key: record.get(key) for key in (
            'formula', 'byteOffset', 'bitOffset', 'unit', 'enumText',
            'read_request_candidate_hex', 'wireSID', 'rawSID', 'readSID', 'pid',
            'disabledreadrequestcandidate', 'request_prefix_hex')}
        # Validate every request/context before sharing pure calculation results;
        # reviewed detached records must not reuse an unreviewed fixture's result.
        request = request_from_record(record)
        semantics['resolvedRequest'] = {'ok': request['ok'], 'reason': request.get('reason'),
            'requestHex': request['request'].hex().upper() if request['ok'] else None}
        key = record_hash(semantics)
        if key not in self.cache:
            self.cache[key] = audit_field(record)
        result = self.cache[key]
        self.counts[result['status']] += 1
        if result.get('reason'):
            self.reasons[result['reason']] += 1
        return {**result, 'definitionSha256': key}

    def summary(self):
        return {'uniqueDefinitionsChecked': len(self.cache), 'sourceFields': dict(self.counts),
                'uniqueFieldStatus': dict(Counter(r['fieldStatus'] for r in self.cache.values())),
                'uniqueResponseStatus': dict(Counter(r['responseStatus'] for r in self.cache.values())),
                'uniqueReadRequestStatus': dict(Counter(r['readRequestStatus'] for r in self.cache.values())),
                'unverifiedReasons': dict(self.reasons),
                'uniqueSyntheticCases': sum(r['cases'] for r in self.cache.values()),
                'uniqueResponseChecks': sum(r['responseChecks'] for r in self.cache.values()),
                'productionParserUsedForExpectedValues': False, 'vehicleVerified': False}


def audit_source(variants, expected_sha256, output, *, timeout_seconds=180):
    """Audit every provided identity/measurement/coding field, without transport.

    A missing read request never suppresses the data-field check, and field
    agreement never supplies a response contract, ECU identity or write recipe.
    """
    from .coding_capture import digest
    from .offline_match import ANCHORS
    from .realtime_preparation import identity_record
    variants, output = Path(variants), Path(output)
    if not isinstance(expected_sha256, str) or len(expected_sha256) != 64 or any(c not in '0123456789abcdef' for c in expected_sha256):
        raise ValueError('source-audit-sha256-invalid')
    if type(timeout_seconds) is not int or not 1 <= timeout_seconds <= 1800:
        raise ValueError('source-audit-timeout-invalid')
    before = variants.stat()
    if not 0 < before.st_size <= 1024**3 or digest(variants) != expected_sha256:
        raise ValueError('source-audit-input-integrity')
    output.mkdir(parents=True, exist_ok=False)
    auditor, seen, counts = DefinitionAuditor(), set(), Counter()
    fields, responses, overall, requests = Counter(), Counter(), Counter(), Counter()
    started = time.monotonic(); error = None; line_number = 0
    with variants.open(encoding='utf8') as source, (output / 'fields.jsonl').open('x', encoding='utf8') as stream:
        try:
            for line_number, line in enumerate(source, 1):
                if len(line) > 32 * 1024**2:
                    raise ValueError('source-audit-profile-size-limit')
                profile = json.loads(line)
                pid = profile['profile_id']
                if not isinstance(pid, str) or not pid or pid in seen:
                    raise ValueError('source-audit-profile-duplicate-or-invalid')
                seen.add(pid)
                prepared = coding_profile_variant(profile)
                for category in ('identity', 'measurement', 'coding'):
                    originals = profile.get('pool_records', {}).get(category, {}).get('records', [])
                    records = prepared.get('pool_records', {}).get(category, {}).get('records', [])
                    for original, record in zip(originals, records):
                        if time.monotonic() - started > timeout_seconds:
                            raise ValueError('source-audit-timeout')
                        record = identity_record(original) if category == 'identity' else record
                        checked = auditor.check(record)
                        counts[category] += 1; fields[category, checked['fieldStatus']] += 1
                        responses[category, checked['responseStatus']] += 1; overall[category, checked['status']] += 1
                        requests[category, checked['readRequestStatus']] += 1
                        stream.write(json.dumps({'profileId': pid, 'category': category, 'sourceOffset': original.get('at'),
                            'sourceRecordSha256': record_hash(original), **checked}, ensure_ascii=False, allow_nan=False) + '\n')
        except (ValueError, KeyError, TypeError, ArithmeticError) as failure:
            error = str(failure) or type(failure).__name__
        stream.flush()
        os.fsync(stream.fileno())
    after = variants.stat()
    if before.st_size != after.st_size or before.st_mtime_ns != after.st_mtime_ns or digest(variants) != expected_sha256:
        error = 'source-audit-input-changed'
    if not counts:
        error = error or 'source-audit-no-fields'
    expected_counts = {'identity': 3920, 'measurement': 92634, 'coding': 59871} if expected_sha256 == ANCHORS['variants'] else None
    if expected_counts is not None and (dict(counts) != expected_counts or len(seen) != 291):
        error = error or 'source-audit-reviewed-coverage-mismatch'
    report = {'kind': 'all-source-fields-independent-audit', 'schemaVersion': 1,
        'source': {'path': str(variants.resolve()), 'bytes': before.st_size, 'sha256': expected_sha256},
        'reviewedOriginal': expected_counts is not None, 'expectedSourceCounts': expected_counts,
        'profiles': len(seen), 'sourceCounts': dict(counts), 'prefixComplete': error is None,
        'error': error, 'lastSourceLine': line_number,
        'fieldStatus': [{'category': c, 'status': s, 'count': n} for (c, s), n in fields.items()],
        'responseStatus': [{'category': c, 'status': s, 'count': n} for (c, s), n in responses.items()],
        'readRequestStatus': [{'category': c, 'status': s, 'count': n} for (c, s), n in requests.items()],
        'overallStatus': [{'category': c, 'status': s, 'count': n} for (c, s), n in overall.items()],
        'audit': auditor.summary(), 'executionEnabled': False, 'liveVerified': False, 'vehicleVerified': False,
        'ecuIdentityQualified': False, 'ecuVersionQualified': False, 'codingWriteQualified': False,
        'codingRestoreQualified': False, 'completeOriginalBackup': False, 'writePayload': None,
        'noDeviceIO': True, 'qualificationGaps': ['native-runtime-semantics', 'precise-ecu-identity-and-version',
            'physical-request-response', 'field-meaning-and-applicability', 'coding-write-and-recovery']}
    (output / 'definitions.json').write_text(json.dumps({'definitions': auditor.cache, 'vehicleVerified': False},
        ensure_ascii=False, indent=2, allow_nan=False) + '\n', encoding='utf8')
    report['artifacts'] = {name: digest(output / name) for name in ('fields.jsonl', 'definitions.json')}
    report['code'] = [{'path': 'scripts/diagnostics/' + name, 'sha256': digest(Path(__file__).with_name(name))}
        for name in ('definition_decoder_audit.py', 'decoder_reference.py', 'response_values.py', 'x431_values.py',
            'x431_formula.py', 'realtime_preparation.py', 'coding_capture.py', 'offline_match.py')]
    report['code'].append({'path': 'scripts/x431_re/request_fields.py',
        'sha256': digest(Path(__file__).parents[1] / 'x431_re/request_fields.py')})
    (output / 'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + '\n', encoding='utf8')
    return report


def main():
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--variants', required=True, type=Path)
    parser.add_argument('--variants-sha256', required=True)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--timeout-seconds', type=int, default=180)
    args = parser.parse_args()
    result = audit_source(args.variants, args.variants_sha256, args.output, timeout_seconds=args.timeout_seconds)
    print(json.dumps({k: result[k] for k in ('profiles', 'sourceCounts', 'fieldStatus', 'responseStatus', 'prefixComplete', 'error')}))
    return 0 if result['prefixComplete'] and all(r['status'] == 'matched' for r in result['overallStatus']) else 2


if __name__ == '__main__':
    raise SystemExit(main())
