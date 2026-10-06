"""Audit prepared DME read/decoder boundaries and historic values without hardware.

Synthetic boundary checks verify software behavior, not correctness on a vehicle.
Historic comparison checks saved source-derived values, not an independent oracle.
"""
from __future__ import annotations
import argparse
from collections import Counter, defaultdict
import json
import hashlib
from pathlib import Path
from unittest import mock

from .realtime_preparation import DEFAULT_OUTPUT, load, record_from_parameter, verify_source
from .manufacturer_acquisition import DME_PROFILE, historical_fixtures
from .manufacturer_transport import prepared_read_contract, transport_rehearsal
from .response_values import decode_application_response, normalize_response, request_from_record, data_span
from .decoder_reference import reference_value, reference_cases, compare_reference


def audit(bundle=DEFAULT_OUTPUT):
    bundle = Path(bundle)
    index = load(bundle / 'index.json')
    row = next(r for r in index['profiles'] if r['profileId'] == DME_PROFILE)
    source = verify_source(bundle / row['file'], row['sha256'])
    profile = load(bundle / row['file'])
    fixtures = load(bundle / 'rehearsal-fixtures.json')
    by_request = defaultdict(list)
    for parameter in profile['parameters']:
        by_request[parameter['requestHex']].append(parameter)
    checks = Counter()
    formulas = Counter()
    groups = []
    reference_failures = []
    for hx, parameters in by_request.items():
        observed = [p for p in parameters if p['decodedSampleCount']]
        for start in range(0, len(parameters), 12):
            selected = [p['id'] for p in parameters[start:start + 12]]
            contract = prepared_read_contract(profile, selected)
            assert contract['groups'][0]['requestHex'] == hx
            assert len(contract['groups']) == 1 and contract['executionEnabled'] is False
            checks['readContracts'] += 1
        for parameter in parameters:
            record = parameter.get('record') or record_from_parameter(parameter)
            request = request_from_record(record)
            assert request['ok'] and request['request'].hex().upper() == hx
            assert data_span(record) == parameter['dataSpan']
            formulas[parameter['formula']['text'].split(':', 1)[0]] += 1
            for data in reference_cases(record):
                expected = reference_value(record, data)
                actual = decode_application_response(record, data, 'data', source='synthetic-reference-audit')
                differences = compare_reference(expected, actual)
                checks['independentSyntheticFields'] += 1
                if differences:
                    reference_failures.append({'parameterId': parameter['id'], 'dataHex': data.hex().upper(),
                                               'mismatchedKeys': differences})
            header = bytes([request['sid'] + 0x40]) + request['request'][1:]
            for nrc in (0x11, 0x31, 0x78):
                result = decode_application_response(record, bytes([0x7F, request['sid'], nrc]), 'pdu')
                assert result['ok'] is False and result['nrc'] == nrc
                checks['negativeResponses'] += 1
            wrong = bytes([header[0]]) + header[1:-1] + bytes([header[-1] ^ 1]) + bytes(parameter['dataSpan']['dataMin'])
            assert normalize_response(wrong, record, 'pdu')['ok'] is False
            checks['wrongIdentifiers'] += 1
            short = header + bytes(max(0, parameter['dataSpan']['dataMin'] - 1))
            assert decode_application_response(record, short, 'pdu')['ok'] is False
            checks['shortFields'] += 1
            if parameter['decodedSampleCount']:
                raw = historical_fixtures(profile, [parameter['id']], fixtures)[hx]
                result = decode_application_response(record, raw, 'pdu')
                assert result['ok'], (parameter['id'], result)
                expected = next(e['decoded'] for e in parameter['examples'] if e['pduSha256'] == hashlib.sha256(raw).hexdigest())
                for key in ('raw', 'value', 'phys', 'text', 'display', 'numeric'):
                    assert result.get(key) == expected.get(key), (parameter['id'], key)
                checks['historicalFieldsCompared'] += 1
        groups.append({'requestHex': hx, 'parameters': len(parameters), 'historicalParameters': len(observed),
                       'missingResponseParameters': len(parameters) - len(observed),
                       'historicalResponsePresent': bool(observed), 'independentLiveQualified': False})
    opened = []
    out = transport_rehearsal(profile, [profile['parameters'][0]['id']], fixtures, mode='live',
                              port_factory=lambda: opened.append(True))
    assert out['error'] == 'manufacturer-transport-unqualified' and not opened
    return {'ok': not reference_failures, 'kind': 'precar-DME-decoder-audit', 'noDeviceIO': True, 'vehicleVerified': False,
            'syntheticChecksAreNotVehicleEvidence': True, 'historicalComparisonIsNotIndependentOracle': True,
            'source': source, 'fixtureSource': verify_source(bundle / 'rehearsal-fixtures.json'),
            'independentReference': {'method': 'literal-formula/individual-bits/rational-arithmetic',
                'productionParserUsedForExpectedValues': False, 'vehicleDefinitionVerified': False,
                'code': verify_source(Path(__file__).with_name('decoder_reference.py')),
                'parametersChecked': len(profile['parameters']), 'failures': reference_failures},
            'checks': dict(checks), 'formulaKinds': dict(formulas), 'requestGroups': groups,
            'parameters': len(profile['parameters']), 'missingResponseGroups': sum(not r['historicalResponsePresent'] for r in groups),
            'missingResponseParameters': sum(r['missingResponseParameters'] for r in groups), 'liveGateClosed': True}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--bundle', type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError('audit-output-already-exists')
    with mock.patch('serial.Serial', side_effect=AssertionError('audit forbids physical serial')):
        result = audit(args.bundle)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({k: v for k, v in result.items() if k in ('ok', 'checks', 'parameters', 'missingResponseGroups', 'missingResponseParameters', 'liveGateClosed')}))
    if not result['ok']:
        raise SystemExit(1)


if __name__ == '__main__': main()
