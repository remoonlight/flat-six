"""Exact source-field joins to hash-checked historical PDUs, no live claims."""
import hashlib
import json
from collections import Counter
from pathlib import Path

from .catalog import load_catalog, profile_by_id
from .qualification import STATIC_VARIANT_LINKS
from .offline_match import verify_source
from .realtime_preparation import DEFAULT_OUTPUT, DEFAULT_MATCH, ROOT, resolve_source_reference
from .response_values import request_from_record, decode_application_response
from .decoder_reference import reference_value, compare_reference


def semantics(record):
    formula = record.get('formula')
    return {'formula': formula.get('text') if isinstance(formula, dict) else formula,
            **{key: record.get(key) for key in ('byteOffset', 'bitOffset', 'unit', 'enumText')}}


class HistoricalCoverage:
    def __init__(self, parameters, groups, catalog=None):
        self.fields = {}
        self.groups = {g['groupId']: g for g in groups}
        self.catalog = catalog or load_catalog()
        self.counts = Counter()
        self.inputs = []
        for parameter in parameters:
            key = (parameter['profileId'], parameter['definitionOffset'])
            if key in self.fields: raise ValueError('duplicate-historical-source-field')
            self.fields[key] = {k: parameter.get(k) for k in
                ('profileId', 'definitionOffset', 'formula', 'unit', 'enumText', 'byteOffset', 'bitOffset',
                 'requestHex', 'examples', 'identityQualified', 'ecuIds')}

    @classmethod
    def load(cls, root=DEFAULT_OUTPUT, match=DEFAULT_MATCH, *, original_source_root=None, source_root=ROOT):
        root, match = Path(root), Path(match)
        bundle_manifest = json.loads((root / 'manifest.json').read_text(encoding='utf8'))
        bindings = [i for i in bundle_manifest['inputs']
                    if Path(i['path']).resolve() == (match / 'manifest.json').resolve()]
        if len(bindings) != 1:
            raise ValueError('historical-match-manifest-binding-required')
        digest = bindings[0]['sha256']
        inputs = [verify_source(match / 'manifest.json', digest)]
        manifest = json.loads((match / 'manifest.json').read_text(encoding='utf8'))
        for name in ('parameters.jsonl', 'request-groups.json'):
            bindings = [i for i in manifest['outputs'] if resolve_source_reference(
                i['path'], original_source_root=original_source_root,
                destination_root=source_root) == (match / name).resolve()]
            if len(bindings) != 1:
                raise ValueError('historical-selected-output-binding-required:' + name)
            inputs.append(verify_source(match / name, bindings[0]['sha256']))
        # Anchored capture/definitions are the provenance; old decoder code is
        # recorded by the source manifest, not silently treated as current code.
        for item in manifest['inputs']:
            selected = resolve_source_reference(item['path'], original_source_root=original_source_root,
                                                destination_root=source_root)
            inputs.append(verify_source(selected, item['sha256']))
        def parameters():
            with (match / 'parameters.jsonl').open(encoding='utf8') as stream:
                for line in stream: yield json.loads(line)
        instance = cls(parameters(), json.loads((match / 'request-groups.json').read_text(encoding='utf8')))
        instance.inputs = inputs
        return instance

    def check(self, record, profile_id, generation, ecu_id):
        result = {'status': 'definition-only', 'references': [], 'liveVerified': False,
                  'independentAdapterVerified': False, 'fittedClaim': False}
        parameter = self.fields.get((profile_id, record.get('at')))
        if parameter is None or not parameter.get('examples'):
            result['reason'] = 'historical-field-response-missing'
            return result
        request = request_from_record(record)
        if (semantics(record) != semantics(parameter) or not request.get('ok')
                or request['request'].hex().upper() != parameter['requestHex']):
            result.update(status='conflict', reason='historical-definition-changed')
            return result
        binding = next((name for (ecu, name), link in STATIC_VARIANT_LINKS.items()
                        if generation == '981' and ecu == ecu_id and link['profile_id'] == profile_id), None)
        named = profile_by_id(self.catalog, binding) if binding else None
        exact_version = bool(named and parameter.get('identityQualified') and ecu_id in (parameter.get('ecuIds') or []))
        conflicts = []
        for example in parameter['examples']:
            group = self.groups.get(example['groupId'])
            if group is None or group['requestHex'] != parameter['requestHex']:
                conflicts.append('historical-request-mismatch'); continue
            sample = next((s for s in group['samples'] if s['responseFrameId'] == example['responseFrameId']
                           and hashlib.sha256(bytes.fromhex(s['pduHex'])).hexdigest() == example['pduSha256']), None)
            if sample is None:
                conflicts.append('historical-pdu-hash-mismatch'); continue
            pdu = bytes.fromhex(sample['pduHex'])
            actual = decode_application_response(record, pdu, 'pdu')
            try:
                expected = reference_value(record, pdu[actual.get('headerLen', 0):])
                agreement = actual.get('ok') and not compare_reference(expected, actual)
            except (ValueError, KeyError, TypeError, OverflowError):
                agreement = False
            if not agreement:
                conflicts.append('historical-field-decode-mismatch'); continue
            address = bool(named and group['txId'] == int(named['txId'], 16)
                           and group['rxId'] == int(named['rxId'], 16))
            qualified = exact_version and address and group['capturePhase'] == 'before-coding' and group['adapterStream'] == [2, 2]
            result['references'].append({k: sample.get(k) for k in
                ('requestFrameId', 'responseFrameId', 'requestRecord', 'responseRecord',
                 'requestFrameSha256', 'responseFrameSha256')} | {
                'pduSha256': example['pduSha256'], 'groupId': group['groupId'],
                'requestHex': group['requestHex'], 'txId': group['txId'], 'rxId': group['rxId'],
                'adapterStream': group['adapterStream'], 'capturePhase': group['capturePhase'],
                'profileId': profile_id, 'captureGeneration': '981', 'targetGeneration': generation, 'ecuId': ecu_id,
                'exactVersionAndAddress': qualified, 'independentDecoderAgreement': True})
        if conflicts:
            result.update(status='conflict', reason=sorted(set(conflicts)))
        elif result['references']:
            result['status'] = 'capture-consistent' if all(r['exactVersionAndAddress'] for r in result['references']) else 'capture-candidate'
            if result['status'] == 'capture-candidate': result['reason'] = 'full-version/address/phase-unqualified'
        else:
            result['reason'] = 'historical-field-response-missing'
        return result
