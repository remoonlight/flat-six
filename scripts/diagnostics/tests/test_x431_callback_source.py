import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from scripts.diagnostics import x431_callback_source as source


class CallbackSourceTests(unittest.TestCase):
    def test_import_does_not_load_private_engine_or_native_libraries(self):
        result = subprocess.run([sys.executable, '-c',
            "import sys; from scripts.diagnostics import x431_callback_source; "
            "assert 'unicorn' not in sys.modules; assert 'ctypes' not in sys.modules"],
            capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_each_identity_checked_before_any_elf_is_parsed(self):
        valid = [b'calc', b'std', b'deviceid']
        hashes = {k:hashlib.sha256(v).hexdigest() for k,v in zip(source.HASHES, valid)}
        for index, key in enumerate(source.HASHES):
            for bad in (None, b'', bytearray(valid[index]), valid[index] + b'changed'):
                args = valid.copy(); args[index] = bad
                with self.subTest(key=key, bad=bad), patch.object(source, 'HASHES', hashes), \
                        patch.object(source, '_Elf') as elf, \
                        self.assertRaisesRegex(ValueError, 'callback-source-' + key + '-unreviewed'):
                    source.audit_callback_sources(*args)
                elf.assert_not_called()

    def test_slot_rejects_foreign_symbol_width_kind_and_relocation(self):
        class Elf:
            relocations = {123:dict(type=21, symbol=dict(name='SPRINTF', va=456,
                bytes=4, type=1, section=22))}
        with patch.object(source, '_pic_address', return_value=123):
            actual = source._slot(Elf(), 1, 2, 'SPRINTF', 456)
            self.assertEqual((actual['gotVa'], actual['objectVa']), (123, 456))
            original = Elf.relocations[123]
            for change in ({'name':'sprintf'}, {'va':457}, {'bytes':8}, {'type':2}, {'section':0}):
                Elf.relocations[123] = dict(type=21, symbol=original['symbol'] | change)
                with self.subTest(change=change), self.assertRaisesRegex(ValueError, 'slot-unreviewed'):
                    source._slot(Elf(), 1, 2, 'SPRINTF', 456)
            Elf.relocations[123] = original | {'type':22}
            with self.assertRaisesRegex(ValueError, 'slot-unreviewed'):
                source._slot(Elf(), 1, 2, 'SPRINTF', 456)

    def test_imported_stdio_object_is_not_a_defined_callback_slot(self):
        class Elf:
            relocations = {123:dict(type=21, symbol=dict(name='__sF', va=0,
                bytes=0, type=1, section=0))}
        with patch.object(source, '_pic_address', return_value=123):
            self.assertEqual(source._slot(Elf(), 1, 2, '__sF', 0, defined=False)['symbol'], '__sF')
            with self.assertRaisesRegex(ValueError, 'slot-unreviewed'):
                source._slot(Elf(), 1, 2, '__sF', 0)

    def test_binding_checks_source_name_and_destination_store(self):
        class Elf:
            name = 'SPRINTF'; instruction = bytes.fromhex('09680860')
            def cstring(self, _): return self.name
            def raw(self, at, count):
                self_read.append((at,count)); return self.instruction
        args = (Elf(), 'SPRINTF', 1, 2, 3, 4, 5, 6, 7, -9)
        self_read = []
        with patch.object(source, '_pic_address', return_value=123), \
                patch.object(source, '_slot', return_value={'gotVa':456, 'objectVa':7}):
            self.assertEqual(source._binding(*args)['missingSymbolReturn'], -9)
            self.assertEqual(self_read, [(4,4)])
            for name, instruction in (('sprintf', Elf.instruction), ('SPRINTF', b'\0'*4)):
                args[0].name, args[0].instruction = name, instruction
                with self.assertRaisesRegex(ValueError, 'binding-unreviewed'):
                    source._binding(*args)

    def test_report_storage_never_overwrites_existing_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); inputs = []
            for key in source.HASHES:
                path = root/key; path.write_bytes(key.encode()); inputs += ['--'+key,str(path)]
            output = root/'report.json'
            with patch.object(source, 'audit_callback_sources', return_value=source.GUARDS.copy()) as audit:
                source.main(inputs+['--output', str(output)])
                audit.assert_called_once_with(b'calc', b'std', b'deviceid')
                before = output.read_bytes()
                self.assertEqual(json.loads(before), source.GUARDS)
                with self.assertRaises(FileExistsError):
                    source.main(inputs+['--output', str(output)])
                self.assertEqual(output.read_bytes(), before)

    def test_definition_preserves_full_device_loader_order_and_distinct_origins(self):
        self.assertEqual([r[0] for r in source.DEVICE_BINDINGS],
            ['GetSysFunCfg','FreeCfgMemory','fseekEx','freadEx','fopenEx','fcloseEx','SPRINTF','GetLanCode'])
        self.assertEqual([r[-1] for r in source.DEVICE_BINDINGS], list(range(-3,-11,-1)))
        self.assertEqual(len(source.REGIONS), 10)
        self.assertEqual({r[0] for r in source.REGIONS}, {'calc','std','deviceid'})
        self.assertEqual({k:v for k,v in source.GUARDS.items() if v not in (False,None)}, {})


if __name__ == '__main__':
    unittest.main()
