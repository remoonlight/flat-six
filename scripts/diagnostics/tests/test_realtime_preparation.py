from __future__ import annotations
import json
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import patch

from scripts.diagnostics.realtime_preparation import (
    identity_record, plan_for, replay_series, handle_ready, hci_times, resolve_dynamic_name,
    dump, sha, FLAGS, POINT_LIMIT, NODE_ECU, zero_definition, resolve_source_reference, compile_bundle,
)
from scripts.diagnostics.response_values import decode_application_response
from scripts.diagnostics.workbench import _validate


def field(key='a', request='22F100', offset=0):
    return {'id': key * 64, 'name': key, 'unit': 'V', 'requestHex': request,
        'readSID': 0x22, 'pid': int(request[2:], 16), 'read_request_candidate_hex': request,
        'byteOffset': offset, 'bitOffset': 0, 'decoderReady': True,
        'categories': [{'id': '40000001', 'label': '通用信息'}],
        'dataSpan': {'kind': 'exact', 'dataMin': offset + 1},
        'formula': {'text': 'IDENTICAL:BitMask=0,BaseDataType=A_UINT32,BitLength=8,HighLow=1,Radix=10,DataType=A_UINT32;'}}


def profile():
    return {'profileId': 'test', 'generation': '981', 'ecuIds': [2],
        'parameters': [field('a'), field('b', offset=1)], 'identity': [], 'checklist': [], **FLAGS}


class SourceRelocationTests(unittest.TestCase):
    def test_foreign_windows_reference_preserves_relative_path(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / '新项目 with spaces'
            mapped = resolve_source_reference(r'D:\old project\.local\目录\variants.jsonl',
                original_source_root=r'D:\old project', destination_root=root)
            self.assertEqual(mapped, root.resolve() / '.local/目录/variants.jsonl')
            self.assertFalse(root.exists())

    def test_posix_and_relative_references(self):
        with tempfile.TemporaryDirectory() as directory:
            for reference in ('/old/project/.local/data.json', '.local/data.json'):
                with self.subTest(reference=reference):
                    self.assertEqual(resolve_source_reference(reference,
                        original_source_root='/old/project', destination_root=directory),
                        Path(directory).resolve() / '.local/data.json')

    def test_explicit_mapping_rejects_outside_or_ambiguous_paths(self):
        cases = [
            (r'D:\other\file.json', r'D:\project'),
            (r'D:\project-other\file.json', r'D:\project'),
            (r'D:\project\..\other\file.json', r'D:\project'),
            (r'D:file.json', r'D:\project'),
            ('/old/project/../other/file.json', '/old/project'),
            (r'D:\project\file.json', '/old/project'),
            ('/old/project/file.json', 'relative/root'),
            ('/old/project/file.json', '/old/../project'),
            ('', '/old/project'), ('file\x00.json', '/old/project'),
            (r'D:\project', r'D:\project'),
        ]
        with tempfile.TemporaryDirectory() as directory:
            for reference, original in cases:
                with self.subTest(reference=reference, original=original):
                    with self.assertRaisesRegex(ValueError, 'manifest-'):
                        resolve_source_reference(reference, original_source_root=original,
                            destination_root=directory)

    def test_no_mapping_keeps_existing_path_behavior(self):
        for reference in (r'D:\project\file.json', '/old/project/file.json', 'relative/file.json'):
            self.assertEqual(resolve_source_reference(reference), Path(reference))

    def test_symlink_cannot_escape_destination(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / 'checkout'; root.mkdir()
            outside = Path(directory) / 'outside'; outside.mkdir()
            try:
                (root / 'link').symlink_to(outside, target_is_directory=True)
            except OSError as error:
                if getattr(error, 'winerror', None) == 1314 or isinstance(error, PermissionError):
                    self.skipTest('host does not permit creating test symlinks')
                raise
            with self.assertRaisesRegex(ValueError, 'outside-destination-root'):
                resolve_source_reference('/old/project/link/file.json',
                    original_source_root='/old/project', destination_root=root)

    def test_compile_rejects_mapped_source_hash_before_creating_output(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); bundle = root / 'bundle'; bundle.mkdir()
            data = root / 'file.json'; data.write_bytes(b'changed')
            manifest = bundle / 'manifest.json'
            dump(manifest, {'inputs': [{'path': '/absent/file.json', 'sha256': '0' * 64}], 'outputs': []})
            original = manifest.read_bytes(); output = root / 'output'
            with patch('scripts.diagnostics.realtime_preparation.resolve_source_reference', return_value=data):
                with self.assertRaisesRegex(ValueError, 'source-hash-mismatch'):
                    compile_bundle(bundle, output, original_source_root='/absent')
            self.assertFalse(output.exists()); self.assertEqual(manifest.read_bytes(), original)

    def test_compile_checks_selected_bundle_copy_not_only_source(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); bundle = root / 'bundle'; bundle.mkdir()
            source = root / 'variants.json'; source.write_bytes(b'[]')
            (bundle / 'variants.json').write_bytes(b'["altered"]')
            manifest = bundle / 'manifest.json'
            dump(manifest, {'inputs': [], 'outputs': [{'path': str(source), 'sha256': sha(source)}]})
            original = manifest.read_bytes(); output = root / 'output'
            with self.assertRaisesRegex(ValueError, 'source-hash-mismatch:variants.json'):
                compile_bundle(bundle, output)
            self.assertFalse(output.exists()); self.assertEqual(manifest.read_bytes(), original)

    def test_compile_rejects_ambiguous_output_binding(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); bundle = root / 'bundle'; bundle.mkdir()
            sources = [root / name / 'variants.json' for name in ('first', 'second')]
            for source in sources:
                source.parent.mkdir(); source.write_bytes(b'[]')
            dump(bundle / 'manifest.json', {'inputs': [],
                'outputs': [{'path': str(source), 'sha256': sha(source)} for source in sources]})
            with self.assertRaisesRegex(ValueError, 'manifest-output-binding-unresolved:variants.json'):
                compile_bundle(bundle, root / 'output')
            self.assertFalse((root / 'output').exists())

class PreparationTests(unittest.TestCase):
    def test_zero_placeholder_only_not_measurement_or_offset(self):
        for name in ('0', '0.00', '0x00000000', '定义为0', '定义：0'):
            self.assertTrue(zero_definition({'name': name}))
        for name in ('电源电压', '状态 0', '0 级', '发动机转速'):
            self.assertFalse(zero_definition({'name': name, 'byteOffset': 0, 'value': 0}))

    def test_native_identity_layout_and_response_header(self):
        rec = field()
        rec.update(request_prefix_hex='22000000f10000', suffix_hex='020003010000f0',
            formula={'text': 'IDENTICAL:BitMask=0,BaseDataType=A_UINT32,BitLength=1,HighLow=1,Radix=10,DataType=A_UINT32;'})
        parsed = identity_record(rec)
        self.assertEqual((parsed['byteOffset'], parsed['bitOffset']), (2, 3))
        decoded = decode_application_response(parsed, bytes.fromhex('62F100FFFF08'), 'pdu')
        self.assertTrue(decoded['ok']); self.assertEqual(decoded['value'], 1)
        self.assertFalse(parsed['executionEnabled'])
        self.assertFalse(decode_application_response(parsed, bytes.fromhex('62F101FFFF08'), 'pdu')['ok'])
        self.assertFalse(decode_application_response(parsed, bytes.fromhex('62F100FF'), 'pdu')['ok'])

    def test_identity_bad_layout_and_unsupported_request(self):
        self.assertFalse(identity_record({'suffix_hex': '00'})['decoderReady'])
        self.assertFalse(identity_record({'request_prefix_hex': '2e000001000000', 'suffix_hex': '00000000000000'})['decoderReady'])

    def test_shared_request_plan_and_max_span(self):
        p = profile(); plan = plan_for(p, ['a' * 64, 'b' * 64])
        self.assertEqual(plan['requestCountPerCycle'], 1)
        self.assertEqual(plan['groups'][0]['minimumDataBytes'], 2)
        self.assertEqual(len(plan['groups'][0]['parameterIds']), 2)
        self.assertFalse(plan['executionEnabled']); self.assertIsNone(plan['writePayload'])
        p['parameters'][1]['dataSpan']['kind'] = 'lowerBound'
        self.assertEqual(plan_for(p, ['b' * 64])['blocked'][0]['reason'], 'parameter-record-span-mismatch')
        p['parameters'][1]['formula'] = {'text': 'IDENTICAL:DataType=A_BYTEFIELD,HighLow=1,Termination=END-OF-PDU;'}
        p['parameters'][1]['dataSpan'] = {'kind': 'lowerBound', 'dataMin': 1, 'unknownRemaining': True}
        self.assertEqual(plan_for(p, ['b' * 64])['groups'][0]['spanKind'], 'lowerBound')

    def test_selection_boundaries_and_unresolved(self):
        for selected in ([], ['a' * 64] * 2, ['f' * 64], ['a' * 64] * 13):
            with self.assertRaises(ValueError): plan_for(profile(), selected)
        p = profile(); p['parameters'][0]['decoderReady'] = False
        plan = plan_for(p, ['a' * 64]); self.assertEqual(plan['requestCountPerCycle'], 0); self.assertEqual(len(plan['blocked']), 1)

    def test_replay_real_time_partition_and_text(self):
        sample = {'pduHex': '62F1002A', 'responseRecord': 7, 'responseFrameId': 'f1', 'responseFrameSha256': '0' * 64}
        groups = [{'groupId': 'before', 'requestHex': '22F100', 'adapterStream': [1, 2], 'txId': 1, 'rxId': 9, 'capturePhase': 'before', 'samples': [sample, {**sample, 'responseRecord': 8}]},
            {'groupId': 'after', 'requestHex': '22F100', 'adapterStream': [2, 2], 'txId': 1, 'rxId': 9, 'capturePhase': 'after', 'samples': [sample]}]
        series = replay_series(field(), groups, {7: 1000000, 8: 1025000})
        self.assertEqual(len(series), 2); self.assertEqual(series[0]['points'][1]['elapsedMs'], 25)
        self.assertEqual(series[0]['points'][0]['value'], 42)
        self.assertEqual(series[1]['points'][0]['elapsedMs'], 0)
        with self.assertRaises(ValueError): replay_series(field(), groups, {})

    def test_replay_truncated_missing_and_downsampling(self):
        group = {'groupId': 'g', 'requestHex': '22F100', 'adapterStream': [1, 2], 'txId': 1, 'rxId': 9, 'capturePhase': 'before', 'samples': []}
        self.assertEqual(replay_series(field(), [group], {}), [])
        group['samples'] = [{'pduHex': '62F100', 'responseRecord': 1, 'responseFrameId': 'f', 'responseFrameSha256': '0' * 64}]
        s = replay_series(field(), [group], {1: 1})[0]; self.assertEqual(s['pointCount'], 0)
        self.assertIn('truncated', s['errors'])
        group['samples'] = [{**group['samples'][0], 'pduHex': '62F1002A', 'responseRecord': n} for n in range(1, 1000)]
        s = replay_series(field(), [group], {n: n * 1000 for n in range(1, 1000)})[0]
        self.assertEqual(s['pointCount'], 999); self.assertEqual(s['displayPointCount'], POINT_LIMIT)
        self.assertEqual(s['points'][-1]['elapsedMs'], 998)

    def test_replay_physical_curve_and_enum_text(self):
        rec = field()
        rec['formula'] = {'text': 'LINEAR:BaseDataType=A_UINT32,Xa=0,Xb=1,Xc=0.1,BitLength=8,BitMask=0,HighLow=1,DataType=A_FLOAT64,Precision=1;'}
        group = {'groupId': 'g', 'requestHex': '22F100', 'adapterStream': [1, 2], 'txId': 1, 'rxId': 9, 'capturePhase': 'before',
            'samples': [{'pduHex': '62F1008A', 'responseRecord': 1, 'responseFrameId': 'f', 'responseFrameSha256': '0' * 64}]}
        point = replay_series(rec, [group], {1: 1})[0]['points'][0]
        self.assertEqual(point['value'], 13.8); self.assertEqual(point['rawValue'], 138)
        self.assertEqual(point['text'], '13.8'); self.assertEqual(point['physicalExact'], '13.8')
        rec['formula'] = {'text': 'TEXTTABLE:DataType=A_UINT32,[0x00]->0xF000010F;[0x01]->0xF0000110;LengthInfo=Standard,BitLength=1,BitMask=0,HighLow=1;'}
        rec['enumText'] = {'F0000110': '是', 'F000010F': '否'}
        group['samples'][0]['pduHex'] = '62F10001'
        point = replay_series(rec, [group], {1: 1})[0]['points'][0]
        self.assertEqual(point['text'], '是'); self.assertFalse(point['numeric'])

    def test_hci_record_times_and_truncation(self):
        with tempfile.TemporaryDirectory() as t:
            path = Path(t) / 'hci.log'
            path.write_bytes(b'btsnoop\0' + struct.pack('>II', 1, 1002) + struct.pack('>IIIIQ', 2, 2, 0, 0, 1234) + b'ab')
            self.assertEqual(hci_times(path), {1: 1234})
            path.write_bytes(path.read_bytes()[:-1])
            with self.assertRaises(ValueError): hci_times(path)

    def test_dynamic_name_bounded_namespace_and_cycle(self):
        name = 'SubIndexNum=2#0xA0,0x00,0x03,0xE4#0xF0,0x01,0x9C,0xC9'
        values = {0xA00003E4: '调节角度', 0xF0019CC9: '值'}
        self.assertEqual(resolve_dynamic_name(name, values.get)[0], '调节角度 · 值')
        self.assertIsNone(resolve_dynamic_name(name, lambda _: name)[0])
        self.assertIsNone(resolve_dynamic_name(name.replace('Num=2', 'Num=1'), values.get)[0])
        self.assertIsNone(resolve_dynamic_name(name, lambda _: None)[0])

    def test_dynamic_name_valid_empty_component_is_not_missing_text(self):
        name='SubIndexNum=2#0xF0,0x01,0x08,0x24#0xF0,0x01,0x47,0xB3'
        values={0xF0010824:'',0xF00147B3:'当前使用的遥控器，ID'}
        text,refs=resolve_dynamic_name(name,values.get)
        self.assertEqual(text,'当前使用的遥控器，ID')
        self.assertEqual(refs,[0xF0010824,0xF00147B3])
        self.assertIsNone(resolve_dynamic_name(name,{0xF00147B3:'当前使用的遥控器，ID'}.get)[0])
        self.assertEqual(resolve_dynamic_name(name,lambda _:'')[0],'')

    def test_api_wrong_unit_generation_hash_and_missing(self):
        with tempfile.TemporaryDirectory() as t:
            root = Path(t); self.assertFalse(handle_ready({'action': 'ready-units'}, root)['present'])
            (root / 'profiles').mkdir(); filename = 'profiles/' + '0' * 64 + '.json'
            dump(root / filename, profile())
            dump(root / 'index.json', {'units': [], 'counts': {}, 'profiles': [{'profileId': 'test', 'ecuIds': [2], 'file': filename, 'sha256': sha(root / filename)}]})
            dump(root / 'manifest.json', {'outputs': [{'path': str(root / 'index.json'), 'sha256': sha(root / 'index.json')}]})
            req = {'action': 'ready-plan', 'profileId': 'test', 'ecuId': 2, 'parameterIds': ['a' * 64]}
            self.assertTrue(handle_ready(req, root)['ok'])
            listed = handle_ready({**req, 'action': 'ready-parameters', 'groupId': '40000001'}, root)
            self.assertEqual(listed['total'], 2); self.assertEqual(listed['categories'][0]['count'], 2)
            self.assertEqual(handle_ready({**req, 'action': 'ready-parameters', 'groupId': '40000002'}, root)['error'], 'unknown-category')
            self.assertEqual(handle_ready({**req, 'ecuId': 1}, root)['error'], 'profile-ecu-mismatch')
            self.assertEqual(handle_ready({**req, 'generation': '982'}, root)['error'], 'wrong-generation')
            (root / filename).write_text('{}')
            self.assertIn('source-hash-mismatch', handle_ready(req, root)['error'])

    def test_api_caps_and_no_vehicle_imports(self):
        for req in ({'action': 'ready-plan', 'parameterIds': ['a' * 64] * 13},
            {'action': 'ready-plan', 'parameterIds': ['../../db']}, {'action': 'ready-replay', 'serialPort': 'COM1'}):
            self.assertFalse(_validate(req)['ok'])
        import scripts.diagnostics.realtime_preparation as module
        source = Path(module.__file__).read_text(encoding='utf-8')
        for forbidden in ('import serial', 'import sqlite', 'subprocess', 'open_transport'):
            self.assertNotIn(forbidden, source)
        self.assertEqual(NODE_ECU['door-driver'], 30); self.assertEqual(NODE_ECU['door-passenger'], 31)


if __name__ == '__main__': unittest.main()
