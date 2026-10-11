import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, Mock

from scripts.diagnostics import x431_command_reference as reference


class CommandReferenceTests(unittest.TestCase):
    def test_import_does_not_load_engine_or_native_loader(self):
        result=subprocess.run([sys.executable,'-B','-c',
            "import sys; from scripts.diagnostics import x431_command_reference; "
            "assert 'unicorn' not in sys.modules; assert 'ctypes' not in sys.modules"],capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stderr)

    def test_source_drift_stops_before_engine_construction(self):
        with self.assertRaisesRegex(ValueError,'language-source-calc-unreviewed'):
            reference.CpuCommandReference(b'changed',b'std',b'language')

    def test_poisoned_closed_and_live_prior_file_cannot_be_reused(self):
        for closed,poisoned,opened,allocations in [(True,False,False,{}),(False,True,False,{}),
                (False,False,True,{}),(False,False,False,{10:4})]:
            instance=reference.CpuCommandReference.__new__(reference.CpuCommandReference)
            instance.closed=closed;instance.poisoned=poisoned;instance.opened=opened;instance.allocations=allocations
            with self.assertRaisesRegex(ValueError,'command-prior-state'):
                instance.command(1,'zero')

    def test_strlen_stops_at_known_boundary_without_zero_padding(self):
        instance=reference.CpuCommandReference.__new__(reference.CpuCommandReference)
        instance.u=Mock();instance.strlen_observations=[]
        instance.u.mem_read.return_value=b'\xa5'*514
        with self.assertRaisesRegex(ValueError,'command-strlen-outside-observed-buffer'):
            instance._cstring(reference.cpu.DATA)
        self.assertIsNone(instance.strlen_observations[0]['terminatorOffset'])
        instance.u.mem_read.assert_called_once_with(reference.cpu.DATA,514)
        instance.u.mem_write.assert_not_called()

    def test_strlen_rejects_foreign_pointer_before_read(self):
        instance=reference.CpuCommandReference.__new__(reference.CpuCommandReference);instance.u=Mock()
        for address in (0,reference.cpu.DATA-1,reference.cpu.DATA+514):
            with self.assertRaisesRegex(ValueError,'command-strlen-address'):
                instance._cstring(address)
        instance.u.mem_read.assert_not_called()

    def test_failed_or_existing_matrix_never_overwrites_evidence(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder);(path/'result.json').write_text('original')
            with self.assertRaisesRegex(ValueError,'fresh-output-required'):
                reference.audit_matrix(b'',b'',b'',path)
            self.assertEqual((path/'result.json').read_text(),'original')
            for budget in (True,0,1801,float('inf'),float('nan')):
                with self.assertRaisesRegex(ValueError,'total-budget-invalid'):
                    reference.audit_matrix(b'',b'',b'',path/'new',budget_s=budget)
            self.assertFalse((path/'new').exists())

    def test_write_guard_confines_output_and_allocations(self):
        instance=reference.CpuCommandReference.__new__(reference.CpuCommandReference);instance.allocations={reference.HEAP:4}
        for address,size in [(reference.cpu.DATA,514),(reference.cpu.DATA+513,1),(reference.HEAP,4)]:
            self.assertTrue(instance._writable(address,size))
        for address,size in [(reference.cpu.DATA,515),(reference.cpu.DATA-1,1),(reference.cpu.DATA+514,1),(reference.HEAP,5)]:
            self.assertFalse(instance._writable(address,size))

    def test_all_actual_operation_qualifications_remain_false(self):
        for key in ('headOpened','vehicleIO','vehicleVerified','liveVerified','executionEnabled','actualHeapBufferQualified',
                'actualDynamicLookupBindingQualified','actualCommandLookupRuntimeQualified'):
            self.assertIs(reference.GUARDS[key],False)
        self.assertIs(reference.GUARDS['deviceWrites'],False)
        self.assertIs(reference.GUARDS['vendorCodeExecuted'],True)
        self.assertIs(reference.GUARDS['cpuEmulationOnly'],True)
        self.assertIsNone(reference.GUARDS['writePayload'])
