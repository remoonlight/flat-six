"""Bounded stream fixtures; never construct serialized Java classes or load target bytecode."""
import hashlib
import json
import struct
import unittest

from scripts.diagnostics.piwis_compilation import FIELDS, RESULT_CLASS, RESULT_UID, inspect_compilation
from scripts.diagnostics.tests.test_piwis_java import fixture, u2, u4


def utf(value):
    raw = value.encode('utf8')
    return u2(len(raw)) + raw


def text(value):
    return b'\x74' + utf(value)


def stream(oid='CTc_TEST', main='ctspackage.Test', key='ctspackage.Test', code=None, source=None):
    raw = b'\xac\xed\0\x05\x73\x72' + utf(RESULT_CLASS) + struct.pack('>q', RESULT_UID) + b'\x02' + u2(5)
    for n, (kind, name, descriptor) in enumerate(FIELDS):
        raw += kind.encode() + utf(name)
        raw += b'\x71\x00\x7e\x00\x03' if n > 2 else text(descriptor)
    raw += b'\x78\x70\x73\x72' + utf('java.util.HashMap') + struct.pack('>q', 362498820763181265)
    raw += b'\x03\0\x02F' + utf('loadFactor') + b'I' + utf('threshold') + b'\x78\x70'
    raw += struct.pack('>fI', 0.75, 12) + b'\x77\x08' + u4(16) + u4(1)
    raw += text(key) + b'\x75\x72' + utf('[B') + struct.pack('>q', -5984413125824719648) + b'\x02\0\0\x78\x70'
    code = fixture('ctspackage/Test') if code is None else code
    raw += u4(len(code)) + code + b'\x78\x70' + text(oid) + text(main)
    return raw + (b'\x70' if source is None else text(source))


class PiwisCompilationTests(unittest.TestCase):
    def test_supported_layout_maps_entity_and_exact_class_bytes(self):
        raw = stream()
        result = inspect_compilation(raw)
        self.assertEqual(result['entityOID'], 'CTc_TEST')
        self.assertEqual(result['fullyQualifiedClassName'], 'ctspackage.Test')
        self.assertEqual(result['sourceSha256'], hashlib.sha256(raw).hexdigest())
        row = result['classes'][0]
        self.assertEqual(raw[row['classOffsetInObject']:row['classOffsetInObject'] + row['classBytes']], fixture('ctspackage/Test'))
        self.assertEqual(row['class']['name'], 'ctspackage/Test')
        self.assertIsNone(result['sourceString'])
        self.assertFalse(result['targetExecuted'])
        self.assertFalse(result['executionEnabled'])
        self.assertTrue(result['noDeviceIO'])
        json.dumps(result, allow_nan=False)

    def test_optional_source_is_only_text(self):
        result = inspect_compilation(stream(source='literal source: never execute'))
        self.assertEqual(result['sourceString'], 'literal source: never execute')
        self.assertFalse(result['liveVerified'])

    def test_non_cts_identity_requires_explicit_verified_kind(self):
        for kind, prefix in (('Script', 'Scc'), ('BuildingBlock', 'BBC'), ('Screen', 'Scr')):
            raw = stream(oid=prefix + '_TEST')
            with self.subTest(kind=kind):
                with self.assertRaises(ValueError):
                    inspect_compilation(raw)
                result = inspect_compilation(raw, kind)
                self.assertEqual(result['entityKind'], kind)
                self.assertFalse(result['executionEnabled'])
        with self.assertRaises(ValueError):
            inspect_compilation(stream(), 'arbitrary-kind')

    def test_screen_missing_entity_identity_is_preserved_not_invented(self):
        raw = stream().replace(text('CTc_TEST'), b'\x70')
        with self.assertRaises(ValueError):
            inspect_compilation(raw)
        result = inspect_compilation(raw, 'Screen')
        self.assertIsNone(result['entityOID'])
        self.assertFalse(result['entityIdentityPresent'])
        self.assertFalse(result['liveVerified'])

    def test_invalid_magic_truncation_and_trailing_payload(self):
        for raw in (stream()[1:], stream()[:-1], stream() + b'\x79', b'\xac\xed\0\x04'):
            with self.subTest(length=len(raw)), self.assertRaises(ValueError):
                inspect_compilation(raw)

    def test_entity_and_main_class_must_match_verified_shape(self):
        for raw in (stream(oid='unrelated'), stream(main='ctspackage.Other'), stream(key='ctspackage.Other')):
            with self.subTest(length=len(raw)), self.assertRaises(ValueError):
                inspect_compilation(raw)

    def test_embedded_class_is_validated_without_loading(self):
        with self.assertRaisesRegex(ValueError, 'class-magic'):
            inspect_compilation(stream(code=b'not a class'))

    def test_unrecognized_descriptor_schema_and_serial_version(self):
        for raw in (stream().replace(b'entityOID', b'entityZZZ', 1),
                    stream().replace(RESULT_CLASS.encode(), b'X' * len(RESULT_CLASS), 1),
                    stream().replace(struct.pack('>q', RESULT_UID), b'\0' * 8, 1)):
            with self.subTest(length=len(raw)), self.assertRaisesRegex(ValueError, 'object-layout'):
                inspect_compilation(raw)

    def test_invalid_backreference_is_not_resolved(self):
        for replacement in (b'\x71\x00\x7e\x00\xff', b'\x71\x00\x7e\x00\x00'):
            raw = stream().replace(b'\x71\x00\x7e\x00\x03', replacement, 1)
            with self.subTest(replacement=replacement), self.assertRaisesRegex(ValueError, 'stream-reference'):
                inspect_compilation(raw)

    def test_custom_datatype_object_and_source_object_are_rejected(self):
        raw = stream()
        end = raw.index(b'\xca\xfe\xba\xbe') + len(fixture('ctspackage/Test'))
        mutated = raw[:end + 1] + b'\x73' + raw[end + 2:]
        with self.assertRaisesRegex(ValueError, 'stream-token'):
            inspect_compilation(mutated)
        with self.assertRaisesRegex(ValueError, 'compilation-string'):
            inspect_compilation(raw[:-1] + b'\x73')

    def test_class_size_and_map_size_cannot_expand_unbounded(self):
        raw = stream()
        for mutated in (raw.replace(u4(len(fixture('ctspackage/Test'))) + b'\xca\xfe\xba\xbe',
                                    u4(0xffffffff) + b'\xca\xfe\xba\xbe', 1),
                        raw.replace(b'\x77\x08' + u4(16) + u4(1), b'\x77\x08' + u4(16) + u4(33), 1)):
            with self.subTest(length=len(mutated)), self.assertRaises(ValueError):
                inspect_compilation(mutated)

    def test_non_byte_array_payload_is_rejected(self):
        with self.assertRaisesRegex(ValueError, 'byte-array'):
            inspect_compilation(stream().replace(utf('[B'), utf('[I'), 1))


if __name__ == '__main__':
    unittest.main()
