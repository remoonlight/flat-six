import copy
import json
from pathlib import Path
import struct
import tempfile
import unittest
import zlib

from scripts.diagnostics.piwis_database import REQUEST_KERNEL_SHA256 as PROFILE, audit, digest, source
from scripts.diagnostics.piwis_ecu_config import (
    ecu_config_mapping_layout, write_ecu_config_mapping_targets,
    config_data_record_reference_prefix, write_ecu_config_data_reference_targets,
    config_record_data_reference_prefix, write_ecu_config_record_reference_targets,
)


POOLS = {'AStringData': {i: {'text': name, 'encoding': 'ascii', 'units': len(name), 'offset': i * 10}
    for i, name in enumerate(('group', '#DataUID', 'DataName', '#RecordUID', 'RecordName', 'VariantName'), 1)},
    'UStringData': {}}


def mapping_raw(value=b'\xff', tail=b'#>\x00'):
    raw = bytearray(struct.pack('<H', 0x21))
    for field in (0x10, 0x20, 0x30, 0x40):
        raw += struct.pack('<5I', 1, 6 if field == 0x30 else 1, 1, 4 if field == 0x40 else 2,
                           5 if field == 0x40 else 3)
        if field == 0x40:
            raw += value
    return bytes(raw) + tail


def fixture(folder, target_name=3):
    root = mapping_raw()
    data = struct.pack('<H6I', 0x180, 1, 1, 1, 0, 0, target_name) + b'unparsed-data-body'
    record = struct.pack('<H6I', 0x184, 1, 1, 1, 0, 0, 5) + b'unparsed-record-body'
    objects, raw, offset = {}, bytearray(), 0
    for number, (object_id, body) in enumerate(((100, root), (2, data), (4, record))):
        compressed = zlib.compress(body)
        objects[object_id] = {'objectId': object_id, 'recordType': int.from_bytes(body[:2], 'little'),
            'sourceOffset': offset, 'compressedBytes': len(compressed), 'decompressedBytes': len(body),
            'recordSha256': digest(body), 'record': number}
        raw += compressed; offset += len(compressed)
    path = folder / 'retained.ec.db'; path.write_bytes(raw); _, src = source(path)
    root_info = objects[100]
    row = {'source': src, 'sourceOffset': 0, 'compressedBytes': root_info['compressedBytes'],
        'recordSha256': root_info['recordSha256'], 'record': 0,
        'layout': ecu_config_mapping_layout(root, POOLS, kernel_sha256=PROFILE)}
    mappings = folder / 'mappings.jsonl'; mappings.write_text(json.dumps(row) + '\n', encoding='utf8')
    return mappings, {src['file']: {'databaseSource': src, 'objects': objects}}, row


DATA_POOLS = copy.deepcopy(POOLS)
for token, text in ((7, 'retained.ec'), (8, 'other.ec'), (9, 'attribute')):
    DATA_POOLS['AStringData'][token] = {'text': text, 'encoding': 'ascii', 'units': len(text), 'offset': token * 10}


def data_reference_raw(key=5, obj=4, container=7, value=b'\xff', attributes=(), empty=False, tail=b'#>\0'):
    common = struct.pack('<H6I', 0x180, 1, 1, 1, 0, 0, 3)
    entry = struct.pack('<III', key, obj, container) + bytes([len(attributes)])
    entry += b''.join(struct.pack('<I', token) for token in attributes) + value
    return common + struct.pack('<H', 0 if empty else 1) + (b'' if empty else entry) + tail


def data_fixture(folder, raw=None, target_name=5):
    data = data_reference_raw() if raw is None else raw
    record = struct.pack('<H6I', 0x184, 1, 1, 1, 0, 0, target_name) + b'unparsed-record-body'
    blob, objects = bytearray(), {}
    for number, (object_id, raw) in enumerate(((2, data), (4, record))):
        encoded = zlib.compress(raw)
        objects[object_id] = {'objectId': object_id, 'recordType': int.from_bytes(raw[:2], 'little'),
            'sourceOffset': len(blob), 'compressedBytes': len(encoded), 'decompressedBytes': len(raw),
            'recordSha256': digest(raw), 'record': number}
        blob += encoded
    database = folder / 'retained.ec.db'; database.write_bytes(blob); _, src = source(database)
    row = {'source': src, 'sourceOffset': 0, 'record': 0, 'compressedBytes': objects[2]['compressedBytes'],
        'recordSha256': objects[2]['recordSha256'],
        'layout': config_data_record_reference_prefix(data, DATA_POOLS, kernel_sha256=PROFILE)}
    prefixes = folder / 'data-prefixes.jsonl'; prefixes.write_text(json.dumps(row) + '\n', encoding='utf8')
    return prefixes, {src['file']: {'databaseSource': src, 'objects': objects}}, row


def record_reference_raw(key=5, obj=4, container=7, config_id=b'\xff', value=b'\xff',
                         audience=0, config_flag=0, config_presence=0, data_flag=0, data_presence=0,
                         attributes=(9,), empty=False, tail=b'#>\0'):
    common = struct.pack('<H6I', 0x184, 1, 1, 1, 0, 0, 3)
    optional_items = bytes([audience]) + config_id + bytes([config_flag, config_presence, data_flag, data_presence])
    entry = struct.pack('<III', key, obj, container) + bytes([len(attributes)])
    entry += b''.join(struct.pack('<I', token) for token in attributes) + value
    return common + optional_items + struct.pack('<H', 0 if empty else 1) + (b'' if empty else entry) + tail


def record_fixture(folder, raw=None, target_name=5):
    record = record_reference_raw() if raw is None else raw
    data_record = struct.pack('<H6I', 0x18a, 1, 1, 1, 0, 0, target_name) + b'unparsed-data-record-body'
    blob, objects = bytearray(), {}
    for number, (object_id, raw) in enumerate(((2, record), (4, data_record))):
        encoded = zlib.compress(raw)
        objects[object_id] = {'objectId': object_id, 'recordType': int.from_bytes(raw[:2], 'little'),
            'sourceOffset': len(blob), 'compressedBytes': len(encoded), 'decompressedBytes': len(raw),
            'recordSha256': digest(raw), 'record': number}
        blob += encoded
    database = folder / 'retained.ec.db'; database.write_bytes(blob); _, src = source(database)
    row = {'source': src, 'sourceOffset': 0, 'record': 0, 'compressedBytes': objects[2]['compressedBytes'],
        'recordSha256': objects[2]['recordSha256'],
        'layout': config_record_data_reference_prefix(record, DATA_POOLS, kernel_sha256=PROFILE)}
    prefixes = folder / 'record-prefixes.jsonl'; prefixes.write_text(json.dumps(row) + '\n', encoding='utf8')
    return prefixes, {src['file']: {'databaseSource': src, 'objects': objects}}, row


class PiwisEcuConfigTests(unittest.TestCase):
    def test_four_fields_pair_order_static_values_and_unread_tail(self):
        result = ecu_config_mapping_layout(mapping_raw(), POOLS, kernel_sha256=PROFILE)
        self.assertTrue(result['fieldLayoutComplete'], result)
        self.assertEqual([m['nativeField'] for m in result['mappings']], [16, 32, 48, 64])
        self.assertEqual(result['totalSerializedPairs'], 4)
        pair = result['mappings'][0]['groups'][0]['pairs'][0]
        self.assertEqual(pair['serializedField1']['text'], '#DataUID')
        self.assertEqual(pair['serializedField2']['text'], 'DataName')
        pair = result['mappings'][3]['groups'][0]['pairs'][0]
        self.assertEqual(pair['expectedTargetRecordType'], 0x184)
        self.assertEqual(pair['value']['nativeType'], 255)
        self.assertFalse(pair['value']['nativeValueDefined'])
        self.assertEqual(result['remainingRawHex'], '233e00')
        self.assertEqual(result['sourceByteOrder'], 'retained-little-endian')
        for field in ('actualVehicleOriginalCoding', 'recordEnvelopeVerified', 'runtimeMappingSelectionVerified',
                      'runtimeStreamByteOrderVerified', 'executionEnabled'):
            self.assertFalse(result[field])

    def test_explicit_typed_values_are_definitions_not_original_car_coding(self):
        for encoded, kind in ((b'\x0b' + struct.pack('<I', 22), 11),
                              (b'\x07' + struct.pack('<i', -2), 7), (b'\x03\x01\x02\x00AB', 3)):
            result = ecu_config_mapping_layout(mapping_raw(encoded), POOLS, kernel_sha256=PROFILE)
            self.assertTrue(result['fieldLayoutComplete'], result)
            pair = result['mappings'][3]['groups'][0]['pairs'][0]
            self.assertEqual(pair['value']['nativeType'], kind)
            self.assertEqual(pair['valueOrigin'], 'static-configuration-mapping-definition')
            self.assertFalse(pair['actualVehicleOriginalCoding'])

    def test_every_body_truncation_blocks_but_tail_is_not_required_for_fields(self):
        raw = mapping_raw(tail=b'')
        for length in range(2, len(raw)):
            result = ecu_config_mapping_layout(raw[:length], POOLS, kernel_sha256=PROFILE)
            self.assertFalse(result['fieldLayoutComplete'], length)
            self.assertLessEqual(result['inspectedOffset'], length)
        self.assertTrue(ecu_config_mapping_layout(raw, POOLS, kernel_sha256=PROFILE)['fieldLayoutComplete'])
        self.assertIsNone(ecu_config_mapping_layout(b'\x80\x01', POOLS, kernel_sha256=PROFILE))

    def test_count_identity_duplicates_unknown_value_and_kernel_block(self):
        for raw, error in ((struct.pack('<HI', 33, 4097), 'ecu-config-group-count-limit'),
                (struct.pack('<HIII', 33, 1, 1, 65537), 'ecu-config-pair-count-limit'),
                (struct.pack('<HIII', 33, 1, 99, 0), 'ecu-config-map-key-identity-unqualified'),
                (struct.pack('<HI', 33, 2) + struct.pack('<II', 1, 0) * 2, 'ecu-config-duplicate-map-key-unqualified'),
                (mapping_raw(b'\xfe'), 'unsupported-compu-value-type-254')):
            result = ecu_config_mapping_layout(raw, POOLS, kernel_sha256=PROFILE)
            self.assertFalse(result['fieldLayoutComplete'])
            self.assertEqual(result['unsupportedReason'], error)
        result = ecu_config_mapping_layout(mapping_raw(), POOLS, kernel_sha256='a'*64)
        self.assertEqual(result['unsupportedReason'], 'ecu-config-kernel-profile-mismatch')

    def test_writer_exact_same_database_targets_names_and_partial_prefixes(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); mappings, indexes, _ = fixture(root)
            output = root / 'targets.jsonl'
            counts = write_ecu_config_mapping_targets(mappings, indexes, POOLS, output, kernel_sha256=PROFILE)
            self.assertEqual(counts['rootRecordsValidated'], 1)
            self.assertEqual(counts['boundPairs'], 4)
            self.assertEqual(counts['uniqueTargetPrefixes'], 2)
            rows = [json.loads(l) for l in output.read_text().splitlines()]
            self.assertEqual(rows[0]['target']['objectId'], 2)
            self.assertEqual(rows[-1]['target']['objectId'], 4)
            for row in rows:
                self.assertTrue(row['binding']['targetShortNameMatched'])
                self.assertFalse(row['targetRecordPrefix']['fieldLayoutComplete'])
                for field in ('runtimeReferenceResolutionVerified', 'runtimeMappingSelectionVerified',
                              'runtimeStreamByteOrderVerified', 'actualVehicleOriginalCoding', 'executionEnabled'):
                    self.assertFalse(row['binding'][field])

    def test_wrong_target_name_or_type_is_recorded_without_success(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); mappings, indexes, _ = fixture(root, target_name=6)
            counts = write_ecu_config_mapping_targets(mappings, indexes, POOLS, root / 'wrong-name.jsonl', kernel_sha256=PROFILE)
            self.assertEqual(counts['blocked:ecu-config-target-short-name-mismatch'], 3)
            index = next(iter(indexes.values())); index['objects'][2]['recordType'] = 0x184
            counts = write_ecu_config_mapping_targets(mappings, indexes, POOLS, root / 'wrong-type.jsonl', kernel_sha256=PROFILE)
            self.assertEqual(counts['blocked:ecu-config-target-record-type-mismatch'], 3)

    def test_forged_root_layout_and_changed_source_are_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); mappings, indexes, row = fixture(root)
            changed = copy.deepcopy(row); changed['layout']['mappings'][0]['groups'][0]['pairs'][0]['serializedField1']['id'] = 4
            mappings.write_text(json.dumps(changed) + '\n', encoding='utf8')
            with self.assertRaisesRegex(ValueError, 'ecu-config-root-layout-identity-mismatch'):
                write_ecu_config_mapping_targets(mappings, indexes, POOLS, root / 'forged.jsonl', kernel_sha256=PROFILE)
            mappings.write_text(json.dumps(row) + '\n', encoding='utf8')
            Path(row['source']['file']).write_bytes(b'changed')
            with self.assertRaisesRegex(ValueError, 'ecu-config-source-changed-during-binding'):
                write_ecu_config_mapping_targets(mappings, indexes, POOLS, root / 'changed.jsonl', kernel_sha256=PROFILE)

    def test_target_frame_boundary_length_or_hash_mismatch_cannot_bind(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); mappings, indexes, _ = fixture(root)
            for number, field in enumerate(('compressedBytes', 'decompressedBytes', 'recordSha256')):
                changed = copy.deepcopy(indexes)
                target = next(iter(changed.values()))['objects'][2]
                target[field] = '0' * 64 if field == 'recordSha256' else target[field] + 1
                output = root / ('invalid-frame-' + str(number) + '.jsonl')
                counts = write_ecu_config_mapping_targets(mappings, changed, POOLS, output, kernel_sha256=PROFILE)
                self.assertEqual(counts['boundPairs'], 1)
                self.assertEqual(counts['blocked:ecu-config-target-record-identity-mismatch'], 3)
                rows = [json.loads(line) for line in output.read_text().splitlines()]
                for row in rows[:-1]:
                    self.assertFalse(row['binding']['ok'])
                    self.assertIsNone(row['targetRecordPrefix'])
                    self.assertFalse(row['binding']['executionEnabled'])

    def test_default_cli_requires_explicit_indexes(self):
        with self.assertRaisesRegex(ValueError, 'ecu-config-mappings-require-key-indexes'):
            audit([], [], Path('unused'), ecu_config_mappings=True)

    def test_data_collection_order_attributes_values_and_empty_prefix(self):
        raw = data_reference_raw(value=b'\x0b' + struct.pack('<I', 20), attributes=(9,))
        result = config_data_record_reference_prefix(raw, DATA_POOLS, kernel_sha256=PROFILE)
        self.assertTrue(result['prefixLayoutComplete'], result)
        self.assertFalse(result['fieldLayoutComplete'])
        entry = result['references'][0]
        self.assertEqual(entry['reference']['attributes'][0]['text'], 'attribute')
        self.assertEqual(entry['value']['nativeType'], 11)
        self.assertEqual(entry['value']['rawHex'], '14000000')
        self.assertEqual(result['remainingRawHex'], '233e00')
        for field in ('executionEnabled', 'actualVehicleOriginalCoding', 'runtimeStreamByteOrderVerified',
                      'mapInsertionSemanticsVerified', 'runtimeReferenceResolutionVerified'):
            self.assertFalse(result[field])
        empty = config_data_record_reference_prefix(data_reference_raw(empty=True), DATA_POOLS, kernel_sha256=PROFILE)
        self.assertTrue(empty['prefixLayoutComplete'])
        self.assertEqual(empty['references'], [])

    def test_data_collection_truncations_count_keys_duplicates_and_unknown_values(self):
        raw = data_reference_raw(tail=b'')
        for length in range(2, len(raw)):
            result = config_data_record_reference_prefix(raw[:length], DATA_POOLS, kernel_sha256=PROFILE)
            self.assertFalse(result['prefixLayoutComplete'], length)
            self.assertLessEqual(result['inspectedOffset'], length)
        duplicate = raw[:26] + struct.pack('<H', 2) + raw[28:] * 2
        for candidate, reason in ((raw[:26] + struct.pack('<H', 4097), 'ecu-config-reference-count-limit'),
                (data_reference_raw(key=0), 'ecu-config-reference-key-identity-unqualified'),
                (data_reference_raw(key=99), 'ecu-config-reference-key-identity-unqualified'),
                (duplicate, 'ecu-config-duplicate-reference-key-unqualified'),
                (data_reference_raw(value=b'\xfe'), 'unsupported-compu-value-type-254')):
            result = config_data_record_reference_prefix(candidate, DATA_POOLS, kernel_sha256=PROFILE)
            self.assertFalse(result['prefixLayoutComplete'])
            self.assertEqual(result['unsupportedReason'], reason)
        self.assertEqual(config_data_record_reference_prefix(raw, DATA_POOLS, kernel_sha256='0'*64)['unsupportedReason'],
                         'ecu-config-kernel-profile-mismatch')

    def test_data_reference_writer_exact_container_object_short_name(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); prefixes, indexes, _ = data_fixture(root)
            output = root / 'data-targets.jsonl'
            counts = write_ecu_config_data_reference_targets(prefixes, indexes, DATA_POOLS, output, kernel_sha256=PROFILE)
            self.assertEqual(counts['dataRecordsValidated'], 1)
            self.assertEqual(counts['references'], 1)
            self.assertEqual(counts['boundReferences'], 1)
            row = json.loads(output.read_text())
            self.assertEqual(row['dataObject']['objectId'], 2)
            self.assertEqual(row['target']['objectId'], 4)
            self.assertTrue(row['binding']['containerFileNameMatched'])
            self.assertTrue(row['binding']['targetShortNameMatched'])
            self.assertFalse(row['targetRecordPrefix']['fieldLayoutComplete'])
            self.assertFalse(row['binding']['executionEnabled'])
            self.assertFalse(row['binding']['runtimeReferenceResolutionVerified'])

    def test_data_reference_writer_container_id_name_and_frame_failures(self):
        cases = ((data_reference_raw(container=8), 5, 'ecu-config-target-container-file-mismatch'),
                 (data_reference_raw(obj=99), 5, 'ecu-config-target-object-id-unqualified'),
                 (data_reference_raw(obj=5), 5, 'ecu-config-target-index-object-missing'),
                 (data_reference_raw(obj=2), 5, 'ecu-config-target-record-type-mismatch'),
                 (data_reference_raw(), 6, 'ecu-config-target-short-name-mismatch'))
        for raw, name, reason in cases:
            with tempfile.TemporaryDirectory() as folder:
                root = Path(folder); prefixes, indexes, _ = data_fixture(root, raw, name)
                counts = write_ecu_config_data_reference_targets(prefixes, indexes, DATA_POOLS,
                    root / 'invalid.jsonl', kernel_sha256=PROFILE)
                self.assertEqual(counts['blocked:' + reason], 1)
                self.assertNotIn('boundReferences', counts)
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); prefixes, indexes, _ = data_fixture(root)
            next(iter(indexes.values()))['objects'][4]['compressedBytes'] += 1
            counts = write_ecu_config_data_reference_targets(prefixes, indexes, DATA_POOLS,
                root / 'invalid-frame.jsonl', kernel_sha256=PROFILE)
            self.assertEqual(counts['blocked:ecu-config-target-record-identity-mismatch'], 1)

    def test_data_reference_forged_prefix_and_changed_source_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); prefixes, indexes, row = data_fixture(root)
            changed = copy.deepcopy(row); changed['layout']['references'][0]['reference']['nativeField08']['id'] = 2
            prefixes.write_text(json.dumps(changed) + '\n', encoding='utf8')
            with self.assertRaisesRegex(ValueError, 'ecu-config-data-prefix-identity-mismatch'):
                write_ecu_config_data_reference_targets(prefixes, indexes, DATA_POOLS, root / 'forged.jsonl', kernel_sha256=PROFILE)
            prefixes.write_text(json.dumps(row) + '\n', encoding='utf8')
            Path(row['source']['file']).write_bytes(b'changed')
            with self.assertRaisesRegex(ValueError, 'ecu-config-source-changed-during-binding'):
                write_ecu_config_data_reference_targets(prefixes, indexes, DATA_POOLS, root / 'changed.jsonl', kernel_sha256=PROFILE)

    def test_blocked_data_prefix_is_counted_without_binding(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); prefixes, indexes, _ = data_fixture(root, data_reference_raw(value=b'\xfe'))
            output = root / 'blocked-prefix.jsonl'
            counts = write_ecu_config_data_reference_targets(prefixes, indexes, DATA_POOLS, output, kernel_sha256=PROFILE)
            self.assertEqual(counts['dataRecordsBlocked'], 1)
            self.assertEqual(output.read_text(), '')

    def test_record_prefix_preserves_static_id_declarations_and_attributes(self):
        raw = record_reference_raw(config_id=b'\x0b' + struct.pack('<I', 22), config_flag=1, data_flag=1)
        result = config_record_data_reference_prefix(raw, DATA_POOLS, kernel_sha256=PROFILE)
        self.assertTrue(result['prefixLayoutComplete'], result)
        self.assertEqual(result['configurationId']['rawHex'], '16000000')
        self.assertEqual(result['configurationIdOrigin'], 'static-configuration-record-definition')
        self.assertEqual(result['hasConfigurationIdFlag'], 1)
        self.assertEqual(result['configurationIdItemPresence'], 0)
        self.assertEqual(result['hasDataIdFlag'], 1)
        self.assertEqual(result['dataIdItemPresence'], 0)
        self.assertEqual(result['references'][0]['expectedTargetRecordType'], 394)
        self.assertEqual(result['references'][0]['reference']['attributes'][0]['text'], 'attribute')
        self.assertEqual(result['remainingRawHex'], '233e00')
        for name in ('fieldLayoutComplete', 'itemDeclarationSemanticsVerified', 'attributeMeaningVerified',
                     'runtimeStreamByteOrderVerified', 'actualVehicleOriginalCoding', 'executionEnabled'):
            self.assertFalse(result[name])

    def test_record_present_optional_item_bodies_stop_without_guessing(self):
        cases = (({'audience': 1}, 'ecu-config-audience-body-unqualified'),
                 ({'config_presence': 1}, 'ecu-config-configuration-id-item-body-unqualified'),
                 ({'data_presence': 1}, 'ecu-config-data-id-item-body-unqualified'),
                 ({'audience': 2}, 'unsupported-object-presence-2'))
        for changes, reason in cases:
            raw = record_reference_raw(**changes)
            result = config_record_data_reference_prefix(raw, DATA_POOLS, kernel_sha256=PROFILE)
            self.assertFalse(result['prefixLayoutComplete'])
            self.assertEqual(result['unsupportedReason'], reason)
            self.assertLess(result['endOffset'], result['inspectedOffset'])
            self.assertEqual(result['remainingRawHex'], raw[result['endOffset']:].hex())
            self.assertEqual(result['references'], [])

    def test_record_prefix_every_truncation_and_kernel_profile(self):
        raw = record_reference_raw(tail=b'')
        for length in range(2, len(raw)):
            result = config_record_data_reference_prefix(raw[:length], DATA_POOLS, kernel_sha256=PROFILE)
            self.assertFalse(result['prefixLayoutComplete'], length)
            self.assertLessEqual(result['inspectedOffset'], length)
        self.assertTrue(config_record_data_reference_prefix(raw, DATA_POOLS, kernel_sha256=PROFILE)['prefixLayoutComplete'])
        self.assertEqual(config_record_data_reference_prefix(raw, DATA_POOLS, kernel_sha256='0'*64)['unsupportedReason'],
                         'ecu-config-kernel-profile-mismatch')
        self.assertIsNone(config_record_data_reference_prefix(b'\x8a\x01', DATA_POOLS, kernel_sha256=PROFILE))

    def test_record_count_duplicate_keys_null_keys_and_unknown_values_block(self):
        raw = record_reference_raw(tail=b'')
        duplicate = raw[:32] + struct.pack('<H', 2) + raw[34:] * 2
        cases = ((raw[:32] + struct.pack('<H', 4097), 'ecu-config-reference-count-limit'),
                 (duplicate, 'ecu-config-duplicate-reference-key-unqualified'),
                 (record_reference_raw(key=0), 'ecu-config-reference-key-identity-unqualified'),
                 (record_reference_raw(config_id=b'\xfe'), 'unsupported-compu-value-type-254'),
                 (record_reference_raw(value=b'\xfe'), 'unsupported-compu-value-type-254'))
        for candidate, reason in cases:
            result = config_record_data_reference_prefix(candidate, DATA_POOLS, kernel_sha256=PROFILE)
            self.assertFalse(result['prefixLayoutComplete'])
            self.assertEqual(result['unsupportedReason'], reason)

    def test_record_writer_exact_source_container_id_short_name_and_attribute(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); prefixes, indexes, _ = record_fixture(root)
            output = root / 'record-targets.jsonl'
            counts = write_ecu_config_record_reference_targets(prefixes, indexes, DATA_POOLS, output, kernel_sha256=PROFILE)
            self.assertEqual(counts['configurationRecordsValidated'], 1)
            self.assertEqual(counts['staticConfigurationIdType:255'], 1)
            self.assertEqual(counts['boundReferences'], 1)
            row = json.loads(output.read_text())
            self.assertEqual(row['configurationRecord']['recordType'], 388)
            self.assertEqual(row['target']['recordType'], 394)
            self.assertEqual(row['entry']['reference']['attributes'][0]['text'], 'attribute')
            self.assertTrue(row['binding']['ok'])
            self.assertFalse(row['binding']['attributeMeaningVerified'])
            self.assertFalse(row['binding']['actualVehicleOriginalCoding'])
            self.assertFalse(row['targetRecordPrefix']['fieldLayoutComplete'])

    def test_record_writer_empty_collection_is_validated_without_targets(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); prefixes, indexes, _ = record_fixture(root, record_reference_raw(empty=True))
            output = root / 'empty-targets.jsonl'
            counts = write_ecu_config_record_reference_targets(prefixes, indexes, DATA_POOLS, output, kernel_sha256=PROFILE)
            self.assertEqual(counts['configurationRecordsValidated'], 1)
            self.assertEqual(counts['collectionSize:0'], 1)
            self.assertEqual(counts['uniqueTargetPrefixes'], 0)
            self.assertEqual(output.read_text(), '')

    def test_record_writer_rejects_wrong_container_id_type_name_and_frame(self):
        cases = ((record_reference_raw(container=8), 5, 'ecu-config-target-container-file-mismatch'),
                 (record_reference_raw(obj=5), 5, 'ecu-config-target-index-object-missing'),
                 (record_reference_raw(obj=2), 5, 'ecu-config-target-record-type-mismatch'),
                 (record_reference_raw(), 6, 'ecu-config-target-short-name-mismatch'))
        for raw, name, reason in cases:
            with tempfile.TemporaryDirectory() as folder:
                root = Path(folder); prefixes, indexes, _ = record_fixture(root, raw, name)
                counts = write_ecu_config_record_reference_targets(prefixes, indexes, DATA_POOLS,
                    root / 'invalid.jsonl', kernel_sha256=PROFILE)
                self.assertEqual(counts['blocked:' + reason], 1)
                self.assertNotIn('boundReferences', counts)
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); prefixes, indexes, _ = record_fixture(root)
            next(iter(indexes.values()))['objects'][4]['recordSha256'] = '0'*64
            counts = write_ecu_config_record_reference_targets(prefixes, indexes, DATA_POOLS,
                root / 'invalid-frame.jsonl', kernel_sha256=PROFILE)
            self.assertEqual(counts['blocked:ecu-config-target-record-identity-mismatch'], 1)

    def test_record_writer_rejects_forged_prefix_or_changed_source(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); prefixes, indexes, row = record_fixture(root)
            changed = copy.deepcopy(row); changed['layout']['references'][0]['reference']['attributes'] = []
            prefixes.write_text(json.dumps(changed) + '\n', encoding='utf8')
            with self.assertRaisesRegex(ValueError, 'ecu-config-record-prefix-identity-mismatch'):
                write_ecu_config_record_reference_targets(prefixes, indexes, DATA_POOLS, root / 'forged.jsonl', kernel_sha256=PROFILE)
            prefixes.write_text(json.dumps(row) + '\n', encoding='utf8')
            Path(row['source']['file']).write_bytes(b'changed')
            with self.assertRaisesRegex(ValueError, 'ecu-config-source-changed-during-binding'):
                write_ecu_config_record_reference_targets(prefixes, indexes, DATA_POOLS, root / 'changed.jsonl', kernel_sha256=PROFILE)

    def test_record_writer_blocked_optional_body_never_binds(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); prefixes, indexes, _ = record_fixture(root, record_reference_raw(config_presence=1))
            output = root / 'blocked.jsonl'
            counts = write_ecu_config_record_reference_targets(prefixes, indexes, DATA_POOLS, output, kernel_sha256=PROFILE)
            self.assertEqual(counts['configurationRecordsBlocked'], 1)
            self.assertEqual(output.read_text(), '')


if __name__ == '__main__':
    unittest.main()
