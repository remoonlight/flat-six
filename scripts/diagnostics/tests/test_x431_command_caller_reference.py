import hashlib
import json
import subprocess
import sys
import tempfile
import time
import unittest
import zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from scripts.diagnostics import x431_command_caller_reference as reference


class CommandCallerReferenceTests(unittest.TestCase):
    def test_import_does_not_load_engine_or_host_loader(self):
        result = subprocess.run([sys.executable, '-B', '-c',
            "import sys; from scripts.diagnostics import x431_command_caller_reference; "
            "assert 'unicorn' not in sys.modules; assert 'ctypes' not in sys.modules"],
            capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_changed_file_is_rejected_before_elf_or_engine(self):
        with self.assertRaisesRegex(ValueError, 'file-source-unreviewed'):
            reference.CpuFileCallerReference(b'changed', b'', b'', b'')

    def test_tails_are_explicit_and_never_padded_from_unknown_bytes(self):
        self.assertEqual(reference.scratch_bytes('sentinel'), b'\xa5' * 514)
        self.assertEqual(reference.scratch_bytes('sentinel-nul'), b'\xa5' * 513 + b'\0')
        self.assertEqual(reference.scratch_bytes('zero'), bytes(514))
        with self.assertRaisesRegex(ValueError, 'tail-unreviewed'):
            reference.scratch_bytes('unknown')

    def test_direct_lookup_rejects_incomplete_observed_storage(self):
        engine = reference.CpuDirectCommandReference.__new__(reference.CpuDirectCommandReference)
        for storage in (b'', bytes(513), bytes(515), bytearray(514)):
            with self.assertRaisesRegex(ValueError, 'direct-input-invalid'):
                engine.search(1, storage)
        for command_id in (True, -1, 0x100000000):
            with self.assertRaisesRegex(ValueError, 'direct-input-invalid'):
                engine.search(command_id, bytes(514))

    def test_direct_closed_or_poisoned_reference_cannot_be_reused(self):
        for closed, poisoned in [(True, False), (False, True)]:
            engine = reference.CpuDirectCommandReference.__new__(reference.CpuDirectCommandReference)
            engine.closed, engine.poisoned = closed, poisoned
            with self.assertRaisesRegex(ValueError, 'direct-prior-state'):
                engine.search(1, bytes(514))

    def test_caller_rejects_unknown_ids_profiles_or_prior_state_before_cpu_access(self):
        engine = reference.CpuFileCallerReference.__new__(reference.CpuFileCallerReference)
        for command_id, profile in [(True, 'complete'), (-1, 'complete'), (1, 'unknown')]:
            with self.assertRaisesRegex(ValueError, 'input-invalid'):
                engine.run(command_id, 'zero', profile)
        engine.closed, engine.poisoned = True, False
        with self.assertRaisesRegex(ValueError, 'prior-state'):
            engine.run(1, 'zero', 'complete')

    def test_named_storage_write_guards_stop_overflows_and_use_after_free(self):
        engine = reference.CpuFileCallerReference.__new__(reference.CpuFileCallerReference)
        engine.slots = {'object': 0x40000}
        engine.allocation_live = False
        self.assertTrue(engine._writable(reference.DATA, 512))
        self.assertFalse(engine._writable(reference.DATA, 513))
        self.assertFalse(engine._writable(reference.SCRATCH, 514))
        engine.allocation_live = True
        self.assertTrue(engine._writable(reference.SCRATCH, 514))
        self.assertFalse(engine._writable(reference.SCRATCH, 515))
        self.assertFalse(engine._writable(reference.SCRATCH + 513, 2))

    def test_null_or_stale_slots_stop_before_any_pointer_read_or_fallback(self):
        engine = reference.CpuFileCallerReference.__new__(reference.CpuFileCallerReference)
        cpu = Mock()
        for address, error in [(0, 'null-function-slot'), (0x53000020, 'stale-function-slot')]:
            with self.assertRaisesRegex(ValueError, error):
                engine.code(cpu, address, 2, None)
        cpu.mem_read.assert_not_called()
        cpu.reg_read.assert_not_called()
        cpu.reg_write.assert_not_called()

    def test_foreign_context_pointer_stops_without_reading_it(self):
        engine = reference.CpuFileCallerReference.__new__(reference.CpuFileCallerReference)
        engine.hooks = {0x10: 'source-open-model'}
        engine.deadline = time.monotonic() + 1
        engine.r = SimpleNamespace(UC_ARM_REG_R0=0, UC_ARM_REG_R1=1, UC_ARM_REG_R2=2, UC_ARM_REG_R3=3)
        engine.command_name, engine.context_open, engine.caller_events = 0x40000, False, []
        cpu = Mock()
        cpu.reg_read.side_effect = [0xffffffff, 0, 0, 0]
        with self.assertRaisesRegex(ValueError, 'context-open-unreviewed'):
            engine.code(cpu, 0x10, 2, None)
        cpu.mem_read.assert_not_called()
        cpu.reg_write.assert_not_called()

    def test_existing_evidence_and_invalid_budget_are_preserved(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)
            (path / 'result.json').write_text('original')
            with self.assertRaisesRegex(ValueError, 'fresh-output-required'):
                reference.audit_matrix(b'', b'', b'', b'', path/'none.zip', path)
            for budget in (True, 0, 901, float('nan'), float('inf')):
                with self.assertRaisesRegex(ValueError, 'total-budget-invalid'):
                    reference.audit_matrix(b'', b'', b'', b'', path/'none.zip', path/'new', budget_s=budget)
            self.assertEqual((path/'result.json').read_text(), 'original')
            self.assertFalse((path/'new').exists())

    def test_source_catalog_payload_drift_is_rejected_before_selection(self):
        with tempfile.TemporaryDirectory() as folder:
            p = Path(folder)/'sources.zip'
            prefix = '.local/diagnostics/x431-source-references/test/'
            manifest = {'files': [{'name': 'profiles/a.json', 'bytes': 2, 'sha256': hashlib.sha256(b'{}').hexdigest()}]}
            with zipfile.ZipFile(p, 'w') as z:
                z.writestr(prefix+'manifest.json', json.dumps(manifest))
                z.writestr(prefix+'profiles/a.json', b'changed')
            with patch.object(reference, 'decode_command_catalog', return_value={'records': []}), \
                 patch('scripts.diagnostics.source_catalog._code_status', return_value={
                     'generatorCodeMatchesCurrent': True, 'generatorCodeDrift': []}):
                with self.assertRaisesRegex(ValueError, 'source-payload-drift'):
                    reference.selector_scope(p, b'')

    def test_std_close_failure_still_closes_file_engine(self):
        engine = reference.CpuFileCallerReference.__new__(reference.CpuFileCallerReference)
        engine.std_reference = Mock(closed=False)
        engine.std_reference.close.side_effect = ValueError('std-failure')
        with patch.object(reference.CpuLoaderReference, 'close', autospec=True) as outer:
            with self.assertRaisesRegex(ValueError, 'engine-cleanup-failed:1'):
                engine.close()
            outer.assert_called_once_with(engine)
        engine.std_reference.close.assert_called_once()

    def test_file_close_failure_keeps_completed_std_close(self):
        engine = reference.CpuFileCallerReference.__new__(reference.CpuFileCallerReference)
        engine.std_reference = Mock(closed=False)
        with patch.object(reference.CpuLoaderReference, 'close', side_effect=ValueError('file-failure')):
            with self.assertRaisesRegex(ValueError, 'engine-cleanup-failed:1'):
                engine.close()
        engine.std_reference.close.assert_called_once()

    def test_both_close_failures_are_reported_after_both_attempts(self):
        engine = reference.CpuFileCallerReference.__new__(reference.CpuFileCallerReference)
        engine.std_reference = Mock(closed=False)
        engine.std_reference.close.side_effect = ValueError('std-failure')
        with patch.object(reference.CpuLoaderReference, 'close', side_effect=ValueError('file-failure')) as outer:
            with self.assertRaisesRegex(ValueError, 'engine-cleanup-failed:2'):
                engine.close()
            outer.assert_called_once()
        engine.std_reference.close.assert_called_once()

    def test_already_closed_std_is_not_closed_twice(self):
        engine = reference.CpuFileCallerReference.__new__(reference.CpuFileCallerReference)
        engine.std_reference = Mock(closed=True)
        with patch.object(reference.CpuLoaderReference, 'close', autospec=True) as outer:
            engine.close()
            outer.assert_called_once_with(engine)
        engine.std_reference.close.assert_not_called()

    def test_actual_heap_binding_and_vehicle_qualification_stay_false(self):
        for key in ('headOpened', 'vehicleIO', 'vehicleVerified', 'executionEnabled', 'deviceWrites',
                    'actualDynamicLookupBindingQualified', 'actualHeapBufferQualified',
                    'actualCommandLookupRuntimeQualified', 'singleAndroidProcessBindingQualified',
                    'nativeFileOpenExecuted', 'nativeFileCloseExecuted'):
            self.assertIs(reference.GUARDS[key], False)
        self.assertIs(reference.GUARDS['separateCpuLookupBridgeModeled'], True)
        self.assertIsNone(reference.GUARDS['writePayload'])
