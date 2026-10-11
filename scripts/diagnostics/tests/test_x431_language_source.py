import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from scripts.diagnostics import x431_language_source as source


class LanguageSourceTests(unittest.TestCase):
    def test_import_does_not_load_native_library_or_cpu_engine(self):
        result = subprocess.run([sys.executable, '-B', '-c',
            "import sys; from scripts.diagnostics import x431_language_source; "
            "assert 'unicorn' not in sys.modules; assert 'ctypes' not in sys.modules"],
            capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_all_source_identities_precede_elf_and_language_parsing(self):
        valid = [b'calc', b'std', b'language']
        hashes = {k:hashlib.sha256(v).hexdigest() for k,v in zip(source.HASHES, valid)}
        for index, key in enumerate(source.HASHES):
            for bad in (None, b'', bytearray(valid[index]), valid[index]+b'changed'):
                args = valid.copy(); args[index] = bad
                with self.subTest(key=key, bad=bad), patch.object(source, 'HASHES', hashes), \
                        patch.object(source, 'input_source_audit') as calc, \
                        patch.object(source, '_Elf') as elf, \
                        patch.object(source, 'parse_header') as language, \
                        self.assertRaisesRegex(ValueError, 'language-source-'+key+'-unreviewed'):
                    source.audit_language_sources(*args)
                calc.assert_not_called(); elf.assert_not_called(); language.assert_not_called()

    def test_complete_plain_bytes_preserve_nul_and_source_length(self):
        for size, bypass in ((1, 'strlen-outside-22-through-544'),
                (22, 'strlen-outside-22-through-544'),
                (23, 'indirect-prefix-mismatch'), (200, 'indirect-prefix-mismatch')):
            body = b'x'*(size-1)+b'\0'
            actual = source._plain_prompt_contract(body)
            self.assertEqual(actual['sourceExpectedReturn'], len(body))
            self.assertEqual(actual['strlenBytes'], len(body)-1)
            self.assertEqual(actual['indirectLookupBypass'], bypass)
            self.assertFalse(actual['sourceExpectedReturnExecuted'])
            self.assertFalse(actual['truncationNeeded'])

    def test_truncated_unterminated_embedded_nul_and_foreign_capacity_rejected(self):
        for body, capacity in ((None,200),(bytearray(b'x\0'),200),(b'',200),
                (b'x',200),(b'x\0x\0',200),(b'x'*200+b'\0',200),
                (b'x\0',199),(b'x\0',True)):
            with self.subTest(body=body,capacity=capacity), \
                    self.assertRaisesRegex(ValueError, 'complete-prompt-required'):
                source._plain_prompt_contract(body,capacity)

    def test_indirect_prefix_stops_instead_of_claiming_unchanged_text(self):
        with self.assertRaisesRegex(ValueError, 'indirect-prompt-unreviewed'):
            source._plain_prompt_contract(b'SubIndexNum='+b'x'*10+b'\0')

    def _state(self):
        keys = [bytes.fromhex(k) for k in ('00000001','00000003','00000007')]
        ctx = bytearray(0x200)
        return dict(ctx=ctx,keylen=4,index_off=0,count=3,
            gag=b'\0'*5+b''.join(k+b'\0'*4 for k in keys)), keys

    def test_binary_search_uses_stored_key_and_complete_record_bytes(self):
        state, keys = self._state()
        with patch.object(source, 'decode_record', side_effect=lambda st,i:(keys[i],b'raw\0')) as decode:
            for i, key in enumerate(keys):
                self.assertEqual(source._lookup(state,int.from_bytes(key,'big')), (i,key,b'raw\0'))
            self.assertEqual(decode.call_count,3)
            with self.assertRaisesRegex(ValueError,'prompt-id-missing'):
                source._lookup(state,2)
            self.assertEqual(decode.call_count,3)

    def test_decoded_record_key_mismatch_stops(self):
        state, _ = self._state()
        with patch.object(source, 'decode_record', return_value=(b'xxxx',b'raw\0')), \
                self.assertRaisesRegex(ValueError,'record-key-mismatch'):
            source._lookup(state,3)

    def test_cli_preserves_existing_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory); args=[]
            for key in source.HASHES:
                path=root/key; path.write_bytes(key.encode()); args += ['--'+key,str(path)]
            output=root/'report.json'
            with patch.object(source,'audit_language_sources',return_value=source.GUARDS.copy()) as audit:
                source.main(args+['--output',str(output)])
                audit.assert_called_once_with(b'calc',b'std',b'language')
                before=output.read_bytes()
                self.assertEqual(json.loads(before),source.GUARDS)
                with self.assertRaises(FileExistsError):
                    source.main(args+['--output',str(output)])
                self.assertEqual(output.read_bytes(),before)


if __name__ == '__main__':
    unittest.main()
