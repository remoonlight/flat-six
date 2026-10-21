import gzip
import hashlib
import json
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import patch
import zlib

from scripts.diagnostics.piwis_database import audit, candidates, records, request_header, response_header, serialized_string, string_table


def table(values, unicode=False):
    data, index = b'', struct.pack('<I', len(values))
    for key, text in values:
        raw = text.encode('utf-16-le' if unicode else 'ascii')
        index += struct.pack('<II', len(data), key)
        data += struct.pack('<I', len(raw) // (2 if unicode else 1)) + raw
    return index, data


class PiwisDatabaseTests(unittest.TestCase):
    def request_pools(self):
        return {'AStringData': string_table(*table([(19, 'RQ_Read'), (22, 'object-id')])),
                'UStringData': string_table(*table([(19, '名称'), (22, '说明')], True), unicode=True)}

    def test_request_prefix_selects_exact_pool_despite_id_collision(self):
        raw = struct.pack('<H6I', 0x78, 19, 19, 22, 22, 0, 0) + b'UNPARSED'
        result = request_header(raw, self.request_pools())
        self.assertEqual([f['text'] for f in result['fields']], ['RQ_Read', '名称', '说明', 'object-id', None, None])
        self.assertEqual(result['headerBytes'], 26)
        self.assertEqual(result['remainingBytes'], 8)
        self.assertEqual(result['remainingSha256'], hashlib.sha256(b'UNPARSED').hexdigest())
        self.assertFalse(result['requestBodyVerified'])
        self.assertFalse(result['ecuApplicabilityVerified'])
        self.assertFalse(result['executionEnabled'])
        self.assertIsNone(request_header(struct.pack('<H', 0x79), self.request_pools()))

    def test_inline_strings_advance_by_native_width_and_keep_unknown_bytes(self):
        raw = struct.pack('<I', 0x80000003) + b'RQX' + struct.pack('<I', 0x80000002) + '😀'.encode('utf-16-le') + struct.pack('<I', 0x80000001) + b'\xff'
        first, pos = serialized_string(raw, 0, 'AStringData', self.request_pools())
        second, pos = serialized_string(raw, pos, 'UStringData', self.request_pools())
        third, pos = serialized_string(raw, pos, 'AStringData', self.request_pools())
        self.assertEqual((first['text'], second['text'], pos), ('RQX', '😀', len(raw)))
        self.assertEqual(third['rawHex'], 'ff')
        self.assertFalse(third['resolved'])
        self.assertEqual(first['storage'], 'inline')

    def test_missing_id_does_not_fall_back_to_other_namespace(self):
        pools = self.request_pools()
        del pools['UStringData'][19]
        value, pos = serialized_string(struct.pack('<I', 19), 0, 'UStringData', pools)
        self.assertIsNone(value['text'])
        self.assertFalse(value['resolved'])
        self.assertEqual(value['unresolvedReason'], 'id-not-in-selected-pool')
        zero, _ = serialized_string(struct.pack('<I', 0), 0, 'AStringData', pools)
        self.assertTrue(zero['nullReference'])

    def test_truncated_inline_invalid_unicode_and_incomplete_request_rejected(self):
        pools = self.request_pools()
        for raw, pool in ((b'\x00', 'AStringData'), (struct.pack('<I', 0x80000002) + b'a', 'AStringData'),
                (struct.pack('<I', 0xffffffff), 'UStringData'), (struct.pack('<I', 0x80000001) + b'\x00\xd8', 'UStringData')):
            with self.subTest(raw=raw), self.assertRaises((ValueError, UnicodeError)):
                serialized_string(raw, 0, pool, pools)
        with self.assertRaises(ValueError):
            serialized_string(b'\0' * 4, 0, 'unknown', pools)
        for raw in (b'', b'x', struct.pack('<H', 0x78), struct.pack('<H6I', 0x78, 19, 19, 22, 22, 0, 0)[:-1]):
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                request_header(raw, pools)

    def test_native_profile_drift_rejected_before_success_index(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            kernel = root / 'kernel.dll'
            kernel.write_bytes(b'unknown-library-bytes')
            with self.assertRaisesRegex(ValueError, 'unsupported-native-request-header-profile'):
                audit([], [], root / 'output', kernel)
            self.assertFalse((root / 'output/database-index.json').exists())
            self.assertEqual(kernel.read_bytes(), b'unknown-library-bytes')

    def test_parameter_audit_requires_native_profile(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ValueError, 'require-pinned-native-kernel'):
                audit([], [], Path(directory) / 'output', parse_request_parameters=True)
            self.assertFalse((Path(directory) / 'output').exists())

    def test_key_indexes_require_profile_matching_pairs_and_indexed_frames(self):
        from scripts.diagnostics.tests.test_piwis_key_index import page, MAGIC
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with self.assertRaisesRegex(ValueError, 'key-index-audit-requires'):
                audit([], [], root / 'missing-kernel', key_indexes=[])
            self.assertFalse((root / 'missing-kernel').exists())
            kernel = root / 'kernel.dll'
            kernel.write_bytes(b'unit-test-static-profile')
            db, key = root / 'retained.bv.db', root / 'retained.bv.key'
            data = struct.pack('<H', 0x2c) + b'RETAINED-OBJECT'
            db.write_bytes(zlib.compress(data))
            payload = struct.pack('<IBB', 0, db.stat().st_size, len(data))
            key.write_bytes(page(0, [MAGIC, (b'\x01\0\0\0', payload)], 1) + page(255, []))
            originals = (db.read_bytes(), key.read_bytes(), kernel.read_bytes())
            with patch('scripts.diagnostics.piwis_database.REQUEST_KERNEL_SHA256', hashlib.sha256(kernel.read_bytes()).hexdigest()):
                result = audit([db], [], root / 'indexed', kernel, key_indexes=[key])
                with self.assertRaisesRegex(ValueError, 'count-mismatch'):
                    audit([db], [], root / 'count', kernel, key_indexes=[])
                wrong_name = root / 'other.key'
                wrong_name.write_bytes(key.read_bytes())
                with self.assertRaisesRegex(ValueError, 'name-mismatch'):
                    audit([db], [], root / 'name', kernel, key_indexes=[wrong_name])
                key.write_bytes(page(0, [MAGIC, (b'\x01\0\0\0', struct.pack('<IBB', 0, len(originals[0]), len(data) + 1))], 1) + page(255, []))
                with self.assertRaisesRegex(ValueError, 'length-mismatch'):
                    audit([db], [], root / 'length', kernel, key_indexes=[key])
            self.assertFalse((root / 'length/database-index.json').exists())
            row = json.loads((root / 'indexed/indexed-records.jsonl').read_text())
            self.assertEqual((row['objectId'], row['recordType'], row['recordSha256']), (1, 0x2c, hashlib.sha256(data).hexdigest()))
            self.assertTrue(result['databases'][0]['keyIndexAudit']['frameCoverageExact'])
            self.assertFalse(result['databases'][0]['keyIndexAudit']['referenceSemanticsVerified'])
            self.assertFalse(row['executionEnabled'])
            self.assertFalse(row['recordSemanticsVerified'])
            self.assertEqual((db.read_bytes(), kernel.read_bytes()), (originals[0], originals[2]))

    def test_dop_compu_opt_in_profile_and_blocked_original_retention(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with self.assertRaisesRegex(ValueError, 'data-object-prefixes-require'):
                audit([], [], root / 'missing', parse_data_objects=True)
            self.assertFalse((root / 'missing').exists())
            kernel = root / 'kernel.dll'
            kernel.write_bytes(b'unit-test-static-profile')
            paths = []
            for pool, unicode in (('AStringData', False), ('UStringData', True)):
                index_path, data_path = root / (pool + '.idx.gz'), root / (pool + '.data.gz')
                idx, data = table([], unicode)
                index_path.write_bytes(gzip.compress(idx))
                data_path.write_bytes(gzip.compress(data))
                paths.append((pool, index_path, data_path, unicode))
            db = root / 'dop.db'
            good = b'\x2c\0\0\0\0\0\1\x0a\0\0\0\0UNREAD'
            blocked = b'\x2c\0\0\0\0\0\1\x0a\0\4\1\5\0\0\0\0\1\x42\0UNREAD'
            db.write_bytes(zlib.compress(good) + zlib.compress(blocked))
            original = db.read_bytes()
            with self.assertRaisesRegex(ValueError, 'unsupported-native'):
                audit([db], paths, root / 'drift', kernel, parse_data_objects=True)
            self.assertFalse((root / 'drift/database-index.json').exists())
            with patch('scripts.diagnostics.piwis_database.REQUEST_KERNEL_SHA256', hashlib.sha256(kernel.read_bytes()).hexdigest()):
                result = audit([db], paths, root / 'complete', kernel, parse_data_objects=True)
            summary = result['databases'][0]['dopCompuPrefixAudit']
            self.assertEqual((summary['dopSimpleRecords'], summary['completeCompuPrefixes']), (2, 1))
            self.assertEqual(summary['blockedReasons'], {'unsupported-compu-object-0x42-expected-0x41': 1})
            rows = [json.loads(s) for s in (root / 'complete/dop-compu-prefixes.jsonl').read_text().splitlines()]
            self.assertEqual(rows[0]['prefix']['remainingRawHex'], b'UNREAD'.hex())
            self.assertEqual(rows[1]['prefix']['remainingRawHex'], blocked[6:].hex())
            self.assertFalse(result['dataObjectPrefixProfile']['executionEnabled'])
            self.assertFalse(result['dataObjectPrefixProfile']['physicalConversionQualified'])
            self.assertEqual(db.read_bytes(), original)

    def test_profile_audit_writes_header_evidence_without_interpreting_body(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            kernel = root / 'kernel.dll'
            kernel.write_bytes(b'unit-test-static-profile')
            paths = []
            for pool, unicode in (('AStringData', False), ('UStringData', True)):
                pair = []
                for suffix, raw in zip(('idx', 'data'), table([(19, 'Read')], unicode)):
                    path = root / (pool + '.' + suffix + '.gz')
                    path.write_bytes(gzip.compress(raw))
                    pair.append(path)
                paths.append((pool, *pair, unicode))
            db = root / 'provided.db'
            db.write_bytes(zlib.compress(struct.pack('<H6I', 0x78, 19, 19, 0, 0, 0, 0) + b'\xff'))
            with patch('scripts.diagnostics.piwis_database.REQUEST_KERNEL_SHA256', hashlib.sha256(kernel.read_bytes()).hexdigest()):
                result = audit([db], paths, root / 'output', kernel)
            self.assertEqual(result['databases'][0]['requestHeaders'], 1)
            row = json.loads((root / 'output/request-record-headers.jsonl').read_text())
            self.assertEqual(row['header']['remainingBytes'], 1)
            self.assertFalse(row['header']['requestBodyVerified'])
            self.assertEqual(result['requestHeaderProfile']['nativeKernel']['file'], str(kernel.resolve()))

    def test_parameter_audit_retains_blocked_record_and_summarizes_collection_only(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            kernel = root / 'kernel.dll'
            kernel.write_bytes(b'unit-test-static-profile')
            paths = []
            for pool, unicode in (('AStringData', False), ('UStringData', True)):
                pair = []
                for suffix, raw in zip(('idx', 'data'), table([(19, 'Read')], unicode)):
                    path = root / (pool + '.' + suffix + '.gz')
                    path.write_bytes(gzip.compress(raw))
                    pair.append(path)
                paths.append((pool, *pair, unicode))
            db = root / 'provided.db'
            header = struct.pack('<H6I', 0x78, 19, 19, 0, 0, 0, 0)
            db.write_bytes(zlib.compress(header + b'\x00TRAILING') + zlib.compress(header + b'\x01\x79\x00\x01\x00\x01\xbe\x00'))
            with patch('scripts.diagnostics.piwis_database.REQUEST_KERNEL_SHA256', hashlib.sha256(kernel.read_bytes()).hexdigest()):
                result = audit([db], paths, root / 'output', kernel, True)
            rows = [json.loads(s) for s in (root / 'output/request-parameter-prefixes.jsonl').read_text().splitlines()]
            self.assertEqual(result['databases'][0]['requestParameterAudit'], {
                'parsedParameters': 0, 'completeCollections': 1,
                'blockedReasons': {'unsupported-parameter-type-0xbe': 1}})
            self.assertEqual(rows[0]['parameters']['remainingRawHex'], b'TRAILING'.hex())
            self.assertFalse(rows[1]['parameters']['parameterCollectionLayoutComplete'])
            self.assertFalse(result['requestParameterProfile']['executionEnabled'])
            self.assertFalse(rows[0]['parameters']['requestBodyVerified'])

    def test_response_header_requires_exact_tag_and_preserves_pool_and_unparsed_body(self):
        raw = struct.pack('<H6I', 0x91, 19, 19, 22, 22, 0, 0) + b'UNPARSED'
        response = response_header(raw, self.request_pools())
        self.assertEqual(response['recordType'], 0x91)
        self.assertEqual(response['nativeObjectType'], 'MCDDbResponseImpl')
        self.assertEqual([f['text'] for f in response['fields']], ['RQ_Read', '名称', '说明', 'object-id', None, None])
        self.assertEqual(response['remainingSha256'], hashlib.sha256(b'UNPARSED').hexdigest())
        self.assertFalse(response['responseBodyVerified'])
        self.assertFalse(response['executionEnabled'])
        self.assertIsNone(response_header(struct.pack('<H', 0x78), self.request_pools()))
        self.assertIsNone(request_header(raw, self.request_pools()))
        with self.assertRaises(ValueError):
            response_header(raw[:25], self.request_pools())

    def test_response_audit_requires_pinned_kernel_and_rejects_drift(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with self.assertRaisesRegex(ValueError, 'response-parameters-require-pinned-native-kernel'):
                audit([], [], root / 'no-kernel', parse_response_parameters=True)
            self.assertFalse((root / 'no-kernel').exists())
            kernel = root / 'kernel.dll'
            kernel.write_bytes(b'wrong-version')
            with self.assertRaisesRegex(ValueError, 'unsupported-native-request-header-profile'):
                audit([], [], root / 'drift', kernel, parse_response_parameters=True)
            self.assertFalse((root / 'drift/database-index.json').exists())

    def test_response_audit_opt_in_output_scope_and_partial_record(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            kernel = root / 'kernel.dll'
            kernel.write_bytes(b'unit-test-static-profile')
            paths = []
            for pool, unicode in (('AStringData', False), ('UStringData', True)):
                pair = []
                for suffix, raw in zip(('idx', 'data'), table([(19, 'Read')], unicode)):
                    path = root / (pool + '.' + suffix + '.gz')
                    path.write_bytes(gzip.compress(raw)); pair.append(path)
                paths.append((pool, *pair, unicode))
            db = root / 'provided.db'
            header = struct.pack('<H6I', 0x91, 19, 19, 0, 0, 0, 0)
            db.write_bytes(zlib.compress(header + b'\x00TAIL') + zlib.compress(header + b'\x01\x92\x00\x01\x00\x01\xbe\x00'))
            original = db.read_bytes()
            with patch('scripts.diagnostics.piwis_database.REQUEST_KERNEL_SHA256', hashlib.sha256(kernel.read_bytes()).hexdigest()):
                result = audit([db], paths, root / 'output', kernel, parse_response_parameters=True)
                audit([db], paths, root / 'headers-only', kernel)
            self.assertFalse((root / 'headers-only/response-parameter-prefixes.jsonl').exists())
            self.assertFalse((root / 'output/request-parameter-prefixes.jsonl').exists())
            self.assertEqual(result['databases'][0]['responseParameterAudit'], {
                'responseHeaders': 2, 'parsedParameters': 0, 'completeCollections': 1,
                'blockedReasons': {'unsupported-parameter-type-0xbe': 1}})
            self.assertEqual(result['responseParameterProfile']['collectionType'], 0x92)
            rows = [json.loads(s) for s in (root / 'output/response-parameter-prefixes.jsonl').read_text().splitlines()]
            self.assertFalse(rows[0]['parameters']['responseBodyVerified'])
            self.assertEqual(rows[1]['parameters']['remainingRawHex'], '01be00')
            self.assertEqual(db.read_bytes(), original)

    def test_ascii_and_utf16_offsets_are_exact(self):
        for unicode, texts in ((False, [(19, 'RPM'), (22, '')]), (True, [(19, '温度'), (22, '😀')])):
            idx, raw = table(texts, unicode)
            parsed = string_table(idx, raw, unicode)
            self.assertEqual([parsed[k]['text'] for k, _ in texts], [s for _, s in texts])
            for key, text in texts:
                start = parsed[key]['offset']
                units = struct.unpack_from('<I', raw, start)[0]
                self.assertEqual(units, parsed[key]['units'])

    def test_data_layouts_opt_in_bind_unit_type_without_vehicle_qualification(self):
        from scripts.diagnostics.tests.test_piwis_key_index import page, MAGIC
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with self.assertRaisesRegex(ValueError, 'data-object-layouts-require'):
                audit([], [], root / 'missing', parse_data_layouts=True)
            self.assertFalse((root / 'missing').exists())
            kernel = root / 'kernel.dll'
            kernel.write_bytes(b'unit-test-static-profile')
            paths = []
            for pool, wide in (('AStringData', False), ('UStringData', True)):
                pair = []
                for suffix, raw in zip(('idx', 'data'), table([(1, 'name'), (2, '2'), (3, 'fixture')], wide)):
                    path = root / (pool + '.' + suffix + '.gz')
                    path.write_bytes(gzip.compress(raw)); pair.append(path)
                paths.append((pool, *pair, wide))
            dop = b'\x2c\0' + struct.pack('<I', 1) + b'\0\0\0\0\0\0\0\1' + struct.pack('<II', 2, 3) + b'\0\0\x23\x3e\0'
            unit = b'\2\1' + struct.pack('<7I', 1, 1, 1, 0, 0, 0, 1) + struct.pack('<dd', 1, 0) + b'\0\0\x23\x3e\0'
            constraint = b'\3\2\1\xfb\0\x0b' + struct.pack('<I', 0) + b'\x0b' + struct.pack('<I', 255) + b'\x11\0\0\x23\x3e\0'
            table_record = struct.pack('<H6II4IIH', 0xab, 1, 1, 1, 1, 1, 1,
                1, 2, 6, 3, 1, 0, 0) + b'\1' + struct.pack('<II', 1, 3) + b'\0\x23\x3e\0'
            structure = struct.pack('<H6IH', 0xaa, 1, 1, 1, 1, 1, 1, 0) + b'\0\x23\x3e\0'
            base = struct.pack('<6I', 1, 1, 1, 0, 0, 0) + b'\0' + struct.pack('<I', 0) + b'\x28' + struct.pack('<II', 0, 0) + b'\1\x0b' + struct.pack('<II', 5, 3)
            table_row = b'\xac\0' + struct.pack('<I', 2) + b'\0\0\0' + base + b'#>\0A\1#>\1#<\0B\0\0#>\1#<\0'
            definitions = (dop, unit, constraint, table_record, structure, table_row)
            blobs = [zlib.compress(raw) for raw in definitions]
            db, key = root / 'fixture.db', root / 'fixture.key'
            db.write_bytes(b''.join(blobs))
            keys, offset = [], 0
            for object_id, raw, blob in zip(range(1, 7), definitions, blobs):
                keys.append((struct.pack('<I', object_id), struct.pack('<IBB', offset, len(blob), len(raw))))
                offset += len(blob)
            key.write_bytes(page(0, [MAGIC, *keys], 1) + page(255, []))
            originals = (db.read_bytes(), key.read_bytes())
            with patch('scripts.diagnostics.piwis_database.REQUEST_KERNEL_SHA256', hashlib.sha256(kernel.read_bytes()).hexdigest()):
                result = audit([db], paths, root / 'layout', kernel, key_indexes=[key], parse_data_layouts=True)
                audit([db], paths, root / 'off', kernel)
            self.assertFalse((root / 'off/data-object-layouts.jsonl').exists())
            rows = [json.loads(line)['layout'] for line in (root / 'layout/data-object-layouts.jsonl').read_text().splitlines()]
            self.assertEqual(len(rows), 6)
            self.assertTrue(rows[3]['keyMap'][0]['reference']['indexLookup']['indexTargetMatched'])
            self.assertEqual(rows[3]['keyMap'][0]['reference']['indexLookup']['target']['recordType'], 0xac)
            self.assertTrue(rows[3]['keyDopReference']['targetIsSimpleDopRecord'])
            self.assertEqual(rows[5]['baseParameter']['objectReference']['indexLookup']['target']['recordType'], 0xaa)
            self.assertTrue(rows[5]['fieldLayoutComplete'])
            self.assertTrue(rows[5]['rowPrefixLayoutComplete'])
            self.assertEqual(result['databases'][0]['dataObjectLayoutAudit']['completeTableRowPrefixes'], 1)
            self.assertTrue(all(row['fieldLayoutComplete'] for row in rows))
            reference = rows[0]['unitReference']
            self.assertTrue(reference['targetIsUnitRecord'])
            self.assertEqual(reference['indexLookup']['target']['recordType'], 0x102)
            self.assertFalse(reference['bindingVerified'])
            self.assertFalse(rows[0]['physicalConversionQualified'])
            self.assertFalse(rows[1]['unitSemanticsVerified'])
            self.assertEqual(result['databases'][0]['dataObjectLayoutAudit']['completeFieldLayouts'], {'44': 1, '258': 1, '515': 1, '171': 1, '170': 1, '172': 1})
            self.assertFalse(rows[2]['constraintSemanticsVerified'])
            self.assertEqual(rows[2]['interval']['upperLimit']['rawHex'], 'ff000000')
            self.assertEqual((db.read_bytes(), key.read_bytes()), originals)

    def test_numeric_probes_are_explicit_source_bound_synthetic_outputs(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with self.assertRaisesRegex(ValueError, 'numeric-probes-require-data-object-layouts'):
                audit([], [], root / 'missing', numeric_probes=True)
            self.assertFalse((root / 'missing').exists())
            kernel = root / 'kernel.dll'; kernel.write_bytes(b'numeric-test-profile')
            profile = hashlib.sha256(kernel.read_bytes()).hexdigest()
            paths = []
            for pool, wide in (('AStringData', False), ('UStringData', True)):
                pair = []
                for suffix, data in zip(('idx', 'data'), table([(1, 'fixture')], wide)):
                    path = root / (pool + '.' + suffix)
                    path.write_bytes(gzip.compress(data)); pair.append(path)
                paths.append((pool, *pair, wide))
            raw = (b'\x2c\0' + struct.pack('<I', 1) + b'\1\x0a\0\0\0\0'
                + b'\1\x23\0\2' + struct.pack('<I', 16) + b'\0\1\x0b\1\0'
                + b'\1\x3c\0\1\0\0' + b'\0'*7 + b'\x23\x3e\0')
            db = root / 'fixture.db'; db.write_bytes(zlib.compress(raw))
            original = db.read_bytes()
            with patch('scripts.diagnostics.piwis_database.REQUEST_KERNEL_SHA256', profile), \
                    patch('scripts.diagnostics.piwis_numeric.REQUEST_KERNEL_SHA256', profile):
                result = audit([db], paths, root / 'on', kernel, parse_data_layouts=True, numeric_probes=True)
                audit([db], paths, root / 'off', kernel, parse_data_layouts=True)
            self.assertFalse((root / 'off/numeric-probes.jsonl').exists())
            self.assertFalse((root / 'off/response-numeric-probes.jsonl').exists())
            self.assertFalse((root / 'off/nested-response-numeric-probes.jsonl').exists())
            self.assertFalse((root / 'on/nested-response-numeric-probes.jsonl').exists())
            self.assertFalse((root / 'on/table-selection-probes.jsonl').exists())
            self.assertFalse((root / 'on/table-key-response-probes.jsonl').exists())
            self.assertFalse((root / 'on/table-structure-child-probes.jsonl').exists())
            self.assertFalse((root / 'on/recursive-response-numeric-probes.jsonl').exists())
            row = json.loads((root / 'on/numeric-probes.jsonl').read_text(encoding='utf8'))
            self.assertEqual(row['recordSha256'], hashlib.sha256(raw).hexdigest())
            self.assertEqual(row['source']['sha256'], hashlib.sha256(original).hexdigest())
            self.assertEqual(len(row['probes']), 6)
            self.assertEqual(row['probes'][2]['result']['candidateValue'], 65535)
            self.assertEqual(row['probes'][-1]['result']['reason'], 'numeric-probe-truncated-field')
            self.assertTrue(all(p['actualVehicleResponse'] is False for p in row['probes']))
            self.assertTrue(all(p['result']['physicalConversionQualified'] is False for p in row['probes']))
            self.assertFalse(result['numericProbeProfile']['executionEnabled'])
            self.assertEqual(db.read_bytes(), original)
            # The response switch creates separate root and nested evidence;
            # numeric-stage-only runs must not silently opt into either.
            from scripts.diagnostics.tests.test_piwis_key_index import page, MAGIC
            key = root / 'fixture.key'
            payload = struct.pack('<IBB', 0, len(original), len(raw))
            key.write_bytes(page(0, [MAGIC, (b'\x01\0\0\0', payload)], 1) + page(255, []))
            with patch('scripts.diagnostics.piwis_database.REQUEST_KERNEL_SHA256', profile):
                response = audit([db], paths, root / 'response', kernel,
                    parse_response_parameters=True, key_indexes=[key],
                    parse_data_layouts=True, response_numeric_probes=True, table_selection_probes=True)
            self.assertEqual((root / 'response/response-numeric-probes.jsonl').read_bytes(), b'')
            self.assertEqual((root / 'response/nested-response-numeric-probes.jsonl').read_bytes(), b'')
            self.assertEqual((root / 'response/table-key-response-probes.jsonl').read_bytes(), b'')
            self.assertEqual((root / 'response/table-structure-child-probes.jsonl').read_bytes(), b'')
            self.assertEqual((root / 'response/recursive-response-numeric-probes.jsonl').read_bytes(), b'')
            self.assertEqual(response['responseNumericProbeProfile']['nestedCounts'], {})
            self.assertEqual(response['responseNumericProbeProfile']['tableKeyCounts'], {})
            self.assertEqual(response['responseNumericProbeProfile']['tableStructureCounts'], {})
            self.assertEqual(response['responseNumericProbeProfile']['recursiveCounts'], {})
            self.assertFalse(response['responseNumericProbeProfile']['executionEnabled'])
            self.assertEqual((root / 'response/table-selection-probes.jsonl').read_bytes(), b'')
            self.assertEqual(response['tableSelectionProbeProfile']['counts'], {})
            self.assertFalse(response['tableSelectionProbeProfile']['tableBindingVerified'])
            self.assertEqual(db.read_bytes(), original)

    def test_unknown_byte_encoding_is_not_guessed(self):
        result = string_table(struct.pack('<III', 1, 0, 15), struct.pack('<I', 1) + b'\xff')
        self.assertIsNone(result[15]['text'])
        self.assertEqual(result[15]['rawHex'], 'ff')

    def test_count_truncation_duplicate_ids_offsets_and_trailing_data_rejected(self):
        idx, data = table([(10, 'a'), (11, 'b')])
        for bad_idx, bad_data in ((idx[:-1], data), (idx, data[:-1]),
                (idx[:12] + struct.pack('<II', 5, 10), data),
                (idx[:12] + struct.pack('<II', 6, 11), data), (idx, data + b'x'),
                (struct.pack('<I', 1_200_001), b'')):
            with self.subTest(index=bad_idx), self.assertRaises(ValueError):
                string_table(bad_idx, bad_data)

    def test_string_expansion_and_invalid_utf16_rejected(self):
        idx = struct.pack('<III', 1, 0, 1)
        for raw, unicode in ((struct.pack('<I', 2**32-1), False), (struct.pack('<I', 1) + b'\x00\xd8', True)):
            with self.subTest(raw=raw), self.assertRaises((ValueError, UnicodeError)):
                string_table(idx, raw, unicode)

    def test_zlib_frames_cover_all_original_bytes_and_cross_chunk_boundaries(self):
        values = [b'abc', bytes(range(256)) * 64, b'last']
        chunks = [zlib.compress(x, 0) for x in values]
        rows = list(records(b''.join(chunks)))
        self.assertEqual([r['data'] for r in rows], values)
        self.assertEqual([r['sourceOffset'] for r in rows], [0, len(chunks[0]), sum(map(len, chunks[:2]))])
        self.assertEqual(sum(r['compressedBytes'] for r in rows), sum(map(len, chunks)))

    def test_zlib_truncation_corruption_trailers_and_bombs_are_rejected(self):
        raw = zlib.compress(b'original')
        for bad in (b'', raw[:-1], raw[:-1] + bytes([raw[-1] ^ 1]), raw + b'trailer'):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                list(records(bad))
        with self.assertRaises(ValueError):
            list(records(zlib.compress(b'a' * 5000), record_limit=128))
        with self.assertRaises(ValueError):
            list(records(zlib.compress(b'a' * 80) * 2, total_limit=100))

    def test_overlapping_pool_ids_keep_all_candidates_without_field_semantics(self):
        pools = {name: string_table(*table([(18, text)])) for name, text in (('A', 'service'), ('U', 'different'))}
        rows = candidates(b'\xff' + struct.pack('<I', 18), pools)
        self.assertEqual([(r['pool'], r['text'], r['recordOffset']) for r in rows], [('A', 'service', 1), ('U', 'different', 1)])

    def test_cli_audit_preserves_sources_and_never_overwrites_existing_output(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paths = []
            originals = {}
            for prefix, unicode in (('A', False), ('U', True)):
                pair = []
                for suffix, raw in zip(('idx', 'data'), table([(10, prefix)], unicode)):
                    path = root / (prefix + '.' + suffix + '.gz')
                    path.write_bytes(gzip.compress(raw))
                    originals[path] = path.read_bytes()
                    pair.append(path)
                paths.append((prefix, *pair, unicode))
            db = root / 'provided.db'
            db.write_bytes(zlib.compress(struct.pack('<I', 10)))
            originals[db] = db.read_bytes()
            result = audit([db], paths, root / 'output')
            self.assertEqual(result['crossPoolIdOverlaps'], 1)
            self.assertEqual(result['databases'][0]['records'], 1)
            self.assertFalse(result['executionEnabled'])
            self.assertFalse(result['liveVerified'])
            self.assertFalse(result['databases'][0]['recordSemanticsVerified'])
            self.assertTrue(result['noDeviceIO'])
            row = json.loads((root / 'output/record-string-candidates.jsonl').read_text().strip())
            self.assertEqual(len(row['candidateStringRefs']), 2)
            self.assertEqual({p: p.read_bytes() for p in originals}, originals)
            with self.assertRaises(FileExistsError):
                audit([db], paths, root / 'output')
