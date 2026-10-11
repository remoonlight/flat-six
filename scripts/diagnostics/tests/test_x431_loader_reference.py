import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from scripts.diagnostics import x431_loader_reference as reference


def observation():
    return dict(pStdDllPresent=False, pStdSearchIDHandlePresent=False,
                symbolSlots={name: False for name in reference.SYMBOL_NAMES},
                events=[dict(operation='open-search', firstDlopenSucceeded=False,
                             fallbackDlopenSucceeded=False, symbolLookupResults=[], observedReturn=-1)])


class LoaderReferenceTests(unittest.TestCase):
    def test_import_does_not_load_engine_or_host_loader(self):
        result = subprocess.run([sys.executable, '-B', '-c',
            "import sys; from scripts.diagnostics import x431_loader_reference; "
            "assert 'unicorn' not in sys.modules; assert 'ctypes' not in sys.modules"],
            capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_changed_source_stops_before_engine_construction(self):
        with self.assertRaisesRegex(ValueError, 'native-binding-file-source-unreviewed'):
            reference.CpuLoaderReference(b'changed', b'std')

    def test_unobserved_initial_slots_are_not_zero_filled(self):
        state = observation()
        state['symbolSlots'][reference.SYMBOL_NAMES[-1]] = None
        with self.assertRaisesRegex(ValueError, 'unknown-initial-slot'):
            reference.validate_observation(state)
        self.assertIsNone(state['symbolSlots'][reference.SYMBOL_NAMES[-1]])

    def test_closed_and_poisoned_engines_cannot_be_reused(self):
        for closed, poisoned in [(True, False), (False, True)]:
            engine = reference.CpuLoaderReference.__new__(reference.CpuLoaderReference)
            engine.closed, engine.poisoned = closed, poisoned
            with self.assertRaisesRegex(ValueError, 'prior-state'):
                engine.sequence(observation())

    def test_unreviewed_logger_return_rejected_before_cpu_access(self):
        engine = reference.CpuLoaderReference.__new__(reference.CpuLoaderReference)
        for result in (True, -1, 1, 0x100000000):
            with self.assertRaisesRegex(ValueError, 'log-return-unreviewed'):
                engine.sequence(observation(), logger_return=result)

    def test_write_guard_only_allows_stack_and_whole_named_objects(self):
        engine = reference.CpuLoaderReference.__new__(reference.CpuLoaderReference)
        engine.slots = {'object': 0x40000}
        self.assertTrue(engine._writable(0x40000, 4))
        self.assertTrue(engine._writable(reference.STACK, 8))
        for address, size in [(0x40000, 1), (0x40001, 4), (0x40000, 8),
                              (reference.STACK - 1, 4), (reference.STACK + 0xfffff, 4)]:
            self.assertFalse(engine._writable(address, size))

    def test_logger_model_does_not_read_unknown_pointers(self):
        engine = reference.CpuLoaderReference.__new__(reference.CpuLoaderReference)
        engine.deadline = time.monotonic() + 1
        engine.hooks = {0x10: 'retained-file-log-model'}
        engine.r = SimpleNamespace(UC_ARM_REG_R0=0, UC_ARM_REG_R1=1, UC_ARM_REG_R2=2,
                                   UC_ARM_REG_R3=3, UC_ARM_REG_LR=14, UC_ARM_REG_PC=15)
        engine.logger_return = 0xa5a5a5a5
        engine.events = []
        cpu = Mock()
        cpu.reg_read.side_effect = [0xffffffff, 0xfffffffe, 0xfffffffd, 0xfffffffc, 0x99]
        engine.code(cpu, 0x10, 2, None)
        cpu.mem_read.assert_not_called()
        cpu.mem_write.assert_not_called()
        self.assertEqual(engine.events[0]['args'][0], 0xffffffff)
        self.assertEqual(cpu.reg_write.call_args_list[0].args, (0, 0xa5a5a5a5))

    def test_unknown_import_stops_without_host_fallback(self):
        engine = reference.CpuLoaderReference.__new__(reference.CpuLoaderReference)
        engine.deadline = time.monotonic() + 1
        engine.hooks = {0x10: 'unreviewed'}
        engine.r = SimpleNamespace(UC_ARM_REG_R0=0, UC_ARM_REG_R1=1, UC_ARM_REG_R2=2, UC_ARM_REG_R3=3)
        cpu = Mock()
        cpu.reg_read.return_value = 0
        with self.assertRaisesRegex(ValueError, 'import-unreviewed'):
            engine.code(cpu, 0x10, 2, None)
        cpu.reg_write.assert_not_called()

    def test_existing_output_and_invalid_budget_preserve_evidence(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)
            (path / 'result.json').write_text('original')
            with self.assertRaisesRegex(ValueError, 'fresh-output-required'):
                reference.audit_matrix(b'', b'', path)
            for budget in (True, 0, 301, float('nan'), float('inf')):
                with self.assertRaisesRegex(ValueError, 'total-budget-invalid'):
                    reference.audit_matrix(b'', b'', path / 'new', budget_s=budget)
            self.assertEqual((path / 'result.json').read_text(), 'original')
            self.assertFalse((path / 'new').exists())

    def test_cpu_execution_does_not_promote_device_or_binding_qualification(self):
        for key in ('hostElfLoaded', 'hostDynamicLoaderCalled', 'nativeLoggingExecuted',
                    'actualDynamicLookupBindingQualified', 'headOpened', 'vehicleIO',
                    'vehicleVerified', 'executionEnabled', 'deviceWrites'):
            self.assertIs(reference.GUARDS[key], False)
        self.assertIs(reference.GUARDS['vendorCodeExecuted'], True)
        self.assertIsNone(reference.GUARDS['writePayload'])
