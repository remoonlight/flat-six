"""Synthetic JVM fixtures: no PIWIS binaries, Java runtime or device required."""
import hashlib
import json
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from scripts.diagnostics.piwis_java import (
    Reader, constant_pool, inspect_class, inspect_jar, instructions, manifest_fields, program_probes,
)


def u2(value):
    return struct.pack('>H', value)


def u4(value):
    return struct.pack('>I', value)


def utf(value):
    raw = value.encode('utf8')
    return b'\x01' + u2(len(raw)) + raw


def fixture(name='de/dsa/Test', overload=False, handler=None):
    # 1 class name, 2 this class, 3 Object name, 4 Object class, 5 method name,
    # 6 ()V, 7 Code, 8 NameAndType, 9 Object methodref, 10 (I)V, 11 field,
    # 12 integer descriptor, 13 ConstantValue, 14 integer constant.
    entries = [utf(name), b'\x07' + u2(1), utf('java/lang/Object'), b'\x07' + u2(3),
               utf('<init>'), utf('()V'), utf('Code'), b'\x0c' + u2(5) + u2(6),
               b'\x0a' + u2(4) + u2(8), utf('(I)V'), utf('TEST'), utf('I'),
               utf('ConstantValue'), b'\x03' + struct.pack('>i', -123)]
    code = b'\x2a\xb7\x00\x09\xb1'
    exception_table = u2(0) if handler is None else u2(1) + b''.join(u2(i) for i in handler)
    payload = u2(1) + u2(1) + u4(len(code)) + code + exception_table + u2(0)
    method = u2(1) + u2(5) + u2(6) + u2(1) + u2(7) + u4(len(payload)) + payload
    if overload:
        method += u2(0x101) + u2(5) + u2(10) + u2(0)  # Native fixture has no Code.
    field = u2(0x19) + u2(11) + u2(12) + u2(1) + u2(13) + u4(2) + u2(14)
    return (b'\xca\xfe\xba\xbe' + u2(0) + u2(55) + u2(len(entries) + 1) + b''.join(entries)
            + u2(0x21) + u2(2) + u2(4) + u2(0) + u2(1) + field
            + u2(2 if overload else 1) + method + u2(0))


def call_pool(interface=False):
    return [None, (1, 'de/dsa/Missing'), (7, 1), (1, 'run'), (1, '()V'),
            (12, (3, 4)), (11 if interface else 10, (2, 5))]


class PiwisJavaTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='piwis-java-')
        self.addCleanup(self.temp.cleanup)
        self.jar = Path(self.temp.name) / 'test.jar'

    def archive(self, members=None):
        with zipfile.ZipFile(self.jar, 'w') as jar:
            jar.writestr('META-INF/MANIFEST.MF', 'Manifest-Version: 1.0\r\nBundle-SymbolicName: TEST\r\n\r\n')
            for name, raw in (members or {'de/dsa/Test.class': fixture()}).items():
                jar.writestr(name, raw)

    def test_class_method_descriptor_source_offsets_and_hashes(self):
        raw = fixture(overload=True)
        result = inspect_class(raw)
        self.assertEqual(result['name'], 'de/dsa/Test')
        self.assertEqual(result['major'], 55)
        self.assertEqual(result['fields'][0]['constant'], -123)
        self.assertEqual([m['descriptor'] for m in result['methods']], ['()V', '(I)V'])
        method = result['methods'][0]
        for item in method['instructions']:
            self.assertEqual(raw[item['classOffset']], item['opcode'])
            self.assertEqual(item['classOffset'], method['codeOffset'] + item['pc'])
        self.assertEqual(method['instructions'][1]['call'], {'owner': 'java/lang/Object', 'name': '<init>', 'descriptor': '()V'})
        self.assertEqual(method['codeSha256'], hashlib.sha256(b'\x2a\xb7\x00\x09\xb1').hexdigest())
        self.assertNotIn('instructions', result['methods'][1])

    def test_truncated_bad_magic_trailing_bytes_and_pool_size(self):
        for raw in (b'bad!', fixture()[:-1], fixture() + b'\0', b'\xca\xfe\xba\xbe' + u2(0) + u2(55) + u2(0)):
            with self.subTest(raw_length=len(raw)), self.assertRaises(ValueError):
                inspect_class(raw)

    def test_modified_utf8_null_and_surrogate_pair(self):
        raw = b'a\xc0\x80\xed\xa0\xbd\xed\xb8\x80'
        pool = constant_pool(Reader(u2(2) + b'\x01' + u2(len(raw)) + raw))
        self.assertEqual(pool[1], (1, 'a\0\U0001f600'))

    def test_exception_handler_boundaries(self):
        valid = inspect_class(fixture(handler=(0, 5, 4, 4)))
        self.assertEqual(valid['methods'][0]['exceptionHandlers'][0]['catch'], 'java/lang/Object')
        for handler in ((0, 2, 4, 4), (0, 5, 2, 4), (4, 1, 4, 4), (0, 6, 4, 4)):
            with self.subTest(handler=handler), self.assertRaisesRegex(ValueError, 'exception-handler'):
                inspect_class(fixture(handler=handler))

    def test_wide_constant_slots_and_nonfinite_json(self):
        pool = constant_pool(Reader(u2(5) + b'\x05' + struct.pack('>q', -7) + b'\x06' + struct.pack('>d', float('nan'))))
        self.assertEqual(pool[1], (5, -7))
        self.assertIsNone(pool[2])
        self.assertEqual(pool[3], (6, 'NaN'))
        json.dumps(pool, allow_nan=False)
        with self.assertRaisesRegex(ValueError, 'wide-constant-slot'):
            constant_pool(Reader(u2(2) + b'\x05' + struct.pack('>q', 1)))

    def test_calls_constants_and_operand_boundaries(self):
        result = instructions(b'\x10\xff\x11\xff\x7f\xb8\x00\x06\xb1', call_pool(), 200)
        self.assertEqual([i['pc'] for i in result], [0, 2, 5, 8])
        self.assertEqual([result[0]['number'], result[1]['number']], [-1, -129])
        self.assertEqual(result[2]['classOffset'], 205)
        self.assertEqual(result[2]['call']['name'], 'run')

    def test_interface_dynamic_invocation_and_wrong_pool_types(self):
        self.assertEqual(instructions(b'\xb9\x00\x06\x01\x00\xb1', call_pool(True))[0]['call']['owner'], 'de/dsa/Missing')
        pool = call_pool() + [(18, (2, 5))]
        self.assertEqual(instructions(b'\xba\x00\x07\x00\x00\xb1', pool)[0]['dynamicCall']['bootstrapIndex'], 2)
        for code, constants in ((b'\xb9\x00\x06\x00\x00', call_pool(True)),
                                (b'\xb9\x00\x06\x01\x01', call_pool(True)),
                                (b'\xb6\x00\x06', call_pool(True)),
                                (b'\xba\x00\x07\x00\x01', pool)):
            with self.subTest(code=code), self.assertRaises(ValueError):
                instructions(code, constants)

    def test_branch_target_must_be_instruction_start(self):
        self.assertEqual(instructions(b'\xa7\x00\x03\xb1', [None])[0]['branchTarget'], 3)
        for code in (b'\xa7\x00\x01\xb1', b'\xa7\x00\x04\xb1', b'\xa7\xff\xff\xb1'):
            with self.subTest(code=code), self.assertRaisesRegex(ValueError, 'branch-target'):
                instructions(code, [None])

    def test_table_and_lookup_switch_padding_alignment(self):
        table = b'\xaa\0\0\0' + struct.pack('>iiii', 20, 4, 4, 20) + b'\xb1'
        self.assertEqual(instructions(table, [None])[0]['switch']['cases'], [{'key': 4, 'target': 20}])
        lookup = b'\x00\xab\0\0' + struct.pack('>iiii', 19, 1, -2, 19) + b'\xb1'
        self.assertEqual(instructions(lookup, [None])[1]['switch']['defaultTarget'], 20)
        for code in (b'\xaa\x01\0\0' + table[4:], b'\xaa\0\0\0' + struct.pack('>iii', 16, 5, 4),
                     b'\xab\0\0\0' + struct.pack('>ii', 12, -1)):
            with self.subTest(code=code), self.assertRaises(ValueError):
                instructions(code, [None])

    def test_wide_increment_and_reserved_or_truncated_instructions(self):
        result = instructions(b'\xc4\x84\x01\x00\xff\xff\xc4\x19\x01\x00\xb1', [None])
        self.assertEqual([i['pc'] for i in result], [0, 6, 10])
        self.assertEqual(result[0]['widenedOpcode'], 132)
        for code in (b'\xc4\x00\0\0', b'\xca', b'\xff', b'\xb8\x00', b'\xc4\x84\0\0\0'):
            with self.subTest(code=code), self.assertRaises(ValueError):
                instructions(code, [None])

    def test_manifest_folded_main_section_and_duplicate_headers(self):
        self.assertEqual(manifest_fields(b'Bundle-Name: TEST\r\r\nRequire-Bundle: one,\r\n two\r\n\r\nName: x\r\nBundle-Name: other\r\n'),
                         {'Bundle-Name': 'TEST', 'Require-Bundle': 'one,two'})
        with self.assertRaisesRegex(ValueError, 'duplicate-manifest'):
            manifest_fields(b'A: one\nA: two\n')

    def test_jar_source_and_missing_symbolic_body_are_explicit(self):
        self.archive({'de/dsa/Test.class': fixture(overload=True),
            'plugin.xml': b'<plugin><extension point="TEST"><provider class="de.dsa.Test"/></extension></plugin>'})
        result = inspect_jar(self.jar)
        self.assertEqual(result['source']['sha256'], hashlib.sha256(self.jar.read_bytes()).hexdigest())
        self.assertEqual(result['summary']['methodsWithCode'], 1)
        self.assertFalse(result['symbolicCalls'][0]['ownerPresent'])
        self.assertFalse(result['symbolicCalls'][0]['exactMethodPresent'])
        self.assertFalse(result['symbolicCalls'][0]['exactMethodHasCode'])
        probes = program_probes(result)
        self.assertTrue(probes['entrypoints'][0]['bodyPresent'])
        self.assertFalse(probes['executionEnabled'])
        self.assertFalse(probes['targetExecuted'])
        self.assertTrue(result['noDeviceIO'])
        self.assertEqual(probes['serviceCallSites'], [])
        self.assertFalse(result['liveVerified'])

    def test_class_name_mismatch_and_unsafe_jar_paths(self):
        for name, raw in (('other.class', fixture()), ('../escape', b'x'), ('C:/escape', b'x')):
            self.archive({name: raw})
            with self.subTest(name=name), self.assertRaises(ValueError):
                inspect_jar(self.jar)

    def test_jar_without_manifest_keeps_missing_metadata_explicit(self):
        with zipfile.ZipFile(self.jar, 'w') as archive:
            archive.writestr('de/dsa/Test.class', fixture())
        result = inspect_jar(self.jar)
        self.assertEqual(result['summary']['classes'], 1)
        self.assertFalse(result['manifestPresent'])
        self.assertEqual(result['manifest'], {})
        self.assertFalse(result['targetExecuted'])

    def test_source_change_during_inspection_rejected(self):
        self.archive()
        original = Path.read_bytes
        count = 0
        def changed(path):
            nonlocal count
            raw = original(path)
            if path == self.jar:
                count += 1
                if count > 1:
                    return raw + b'changed'
            return raw
        with patch.object(Path, 'read_bytes', changed), self.assertRaisesRegex(ValueError, 'changed-during'):
            inspect_jar(self.jar)

    def test_vendor_probes_distinguish_scaling_ids_from_pids(self):
        name = 'de/dsa/pidt/obd/scaling/ScalingDefinition0C'
        self.archive({name + '.class': fixture(name)})
        probes = program_probes(inspect_jar(self.jar))
        row = probes['scalingDefinitions'][0]
        self.assertEqual(row['identifierKind'], 'scaling-id-not-pid')
        self.assertFalse(row['ecuBindingVerified'])
        self.assertEqual(row['classSha256'], hashlib.sha256(fixture(name)).hexdigest())

    def test_oversized_metadata_rejected_before_crc_or_decompression(self):
        self.archive({'large.txt': b'x' * (1024 * 1024 + 1)})
        with (patch.object(zipfile.ZipFile, 'testzip', side_effect=AssertionError('must not decompress')),
              self.assertRaisesRegex(ValueError, 'jar-member-size-limit')):
            inspect_jar(self.jar)


if __name__ == '__main__':
    unittest.main()
