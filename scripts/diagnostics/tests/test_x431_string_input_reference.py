import collections
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

from scripts.diagnostics import x431_input_info_source as source
from scripts.diagnostics import x431_string_input_reference as reference


class StringInputReferenceTests(unittest.TestCase):
    def test_import_never_loads_engine_or_host_libraries(self):
        r = subprocess.run([sys.executable, '-c',
            "import sys; from scripts.diagnostics import x431_string_input_reference; "
            "assert 'unicorn' not in sys.modules; assert 'ctypes' not in sys.modules"],
            capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_each_original_identity_precedes_elf_parsing(self):
        blobs = [b'calc', b'std']
        hashes = {k: hashlib.sha256(b).hexdigest() for k, b in zip(source.HASHES, blobs)}
        for index, key in enumerate(source.HASHES):
            for bad in (None, bytearray(blobs[index]), blobs[index] + b'changed'):
                args = blobs.copy(); args[index] = bad
                with self.subTest(key=key, bad=bad), patch.object(source, 'HASHES', hashes), \
                        patch.object(source, '_Elf') as elf:
                    with self.assertRaisesRegex(ValueError, 'source-' + key + '-unreviewed'):
                        source.audit_input_info_sources(*args)
                    elf.assert_not_called()

    def test_runtime_rejects_unknown_original_before_engine_import(self):
        with self.assertRaisesRegex(ValueError, 'source-calc-unreviewed'):
            reference.CpuStringInputReference(b'calc', b'std')

    def test_array_object_cannot_be_confused_with_pointer_object(self):
        class Elf:
            relocations = {123: dict(type=21, symbol=dict(name='pSwapDataBuffer',
                va=0xe660a, bytes=0x5000, type=1, section=23))}
        original = Elf.relocations[123]
        with patch.object(source, '_pic_address', return_value=123):
            self.assertEqual(source._array_slot(Elf(), 1, 2)['bytes'], 0x5000)
            for change in ({'bytes':4}, {'va':0xe660b}, {'section':0}, {'type':2}):
                Elf.relocations[123] = original | {'symbol': original['symbol'] | change}
                with self.subTest(change=change), self.assertRaisesRegex(ValueError, 'buffer-unreviewed'):
                    source._array_slot(Elf(), 1, 2)

    def test_evidence_output_is_append_only(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for key in source.HASHES:
                (root/key).write_bytes(key.encode())
            args = ['--calc', str(root/'calc'), '--std', str(root/'std'), '--output', str(root/'report')]
            with patch.object(source, 'audit_input_info_sources', return_value=source.GUARDS):
                source.main(args)
                before = (root/'report').read_bytes()
                self.assertEqual(json.loads(before), source.GUARDS)
                self.assertNotIn(b'\r\n', before)
                with self.assertRaises(FileExistsError):
                    source.main(args)
                self.assertEqual((root/'report').read_bytes(), before)

    def validate(self, **changes):
        args = dict(title=b'field', conditions=b'input', reply=b'41', capacity=4096,
            accepted=True, initial=None, destination_present=True, sequence=0)
        args.update(changes)
        reference.validate_input(**args)

    def test_raw_non_ascii_is_preserved_without_encoding_qualification(self):
        self.validate(reply='测试'.encode('utf8'))
        self.assertFalse(reference.GUARDS['inputCharacterEncodingQualified'])
        self.assertFalse(reference.GUARDS['actualInputInfoReturnQualified'])

    def test_embedded_nul_and_implicit_text_conversion_stop(self):
        for name in ('title', 'conditions', 'initial', 'reply'):
            for value in (b'A\0B', 'AB', bytearray(b'AB')):
                with self.subTest(name=name, value=value), self.assertRaisesRegex(ValueError, 'invalid'):
                    self.validate(**{name:value})

    def test_reply_does_not_silently_truncate_to_capacity(self):
        with self.assertRaisesRegex(ValueError, 'reply-exceeds-capacity'):
            self.validate(reply=b'AB', capacity=1)
        self.validate(reply=b'AB', capacity=2)

    def test_large_capacity_stops_before_source_clamp_can_overrun_global_buffer(self):
        for capacity in (True, -2, 4097, 0x5000, 0x5001):
            with self.subTest(capacity=capacity), self.assertRaisesRegex(ValueError, 'capacity-unreviewed'):
                self.validate(capacity=capacity)

    def test_boolean_and_sequence_contracts_are_explicit(self):
        for name, values in (('accepted', (0, 1, None)), ('destination_present', (0, 1)),
                             ('sequence', (True, -1, 256, '0'))):
            for value in values:
                with self.subTest(name=name, value=value), self.assertRaisesRegex(ValueError, 'invalid'):
                    self.validate(**{name:value})

    def test_closed_reference_cannot_be_reused(self):
        p = reference.CpuStringInputReference.__new__(reference.CpuStringInputReference)
        p.closed = True; p.poisoned = False; p.active = False
        with self.assertRaisesRegex(ValueError, 'closed'):
            p.input_bytes(b'field', b'input')

    def test_original_and_foundation_writes_share_sentinel_boundaries(self):
        p = reference.CpuStringInputReference.__new__(reference.CpuStringInputReference)
        p.active = True; p.buffer_extent = 4102; p.output_extent = 4097
        p.u = Mock()
        for address, size in ((reference.OUTPUT+4096, 2), (reference.BUFFER+4101, 2),
                              (reference.TITLE, 1), (reference.EXCHANGE_POINTER, 4), (0, 1)):
            with self.subTest(address=address):
                with self.assertRaisesRegex(ValueError, 'write-range'):
                    p.write(None, None, address, size, None, None)
                with self.assertRaisesRegex(ValueError, 'foundation-write'):
                    p._put(address, b'A' * size)
        p.u.mem_write.assert_not_called()

    def test_unknown_code_stops_before_any_import_or_ui_model(self):
        p = reference.CpuStringInputReference.__new__(reference.CpuStringInputReference)
        p.active = True; p.hooks = {}; p.r = None; p.allowed = ((0x26fb4, 0x26fca),)
        with self.assertRaisesRegex(ValueError, 'code-range'):
            p.code(None, 0x32200, 2, None)

    def test_notify_replacement_requires_reviewed_caller_return_address(self):
        p = reference.CpuStringInputReference.__new__(reference.CpuStringInputReference)
        p.active = True; p.hooks = {0x41248:'virtual-ui-notify'}; p.trace = collections.Counter()
        p.expected_request = b'123456'; p.requests = []
        p.r = Mock(UC_ARM_REG_R0=0, UC_ARM_REG_R1=1, UC_ARM_REG_LR=14, UC_ARM_REG_PC=15)
        u = Mock(); u.reg_read.side_effect = lambda reg: {0:reference.BUFFER, 1:6, 14:0}[reg]
        with self.assertRaisesRegex(ValueError, 'notify-call'):
            p.code(u, 0x41248, 2, None)
        u.mem_write.assert_not_called(); u.reg_write.assert_not_called()


if __name__ == '__main__':
    unittest.main()
