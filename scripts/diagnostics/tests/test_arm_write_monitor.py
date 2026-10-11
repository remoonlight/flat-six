import subprocess
import sys
import types
import unittest
from scripts.diagnostics.arm_write_monitor import ArmWriteMonitor, IT_MASK, remove_arm_write_monitor
from scripts.diagnostics.arm_write_monitor_audit import expected_stores, predicate


class FakeCpu:
    _arch = 1
    def __init__(self):
        self.regs = {1: 0xa00001f3, 2: 0x1000}
        self.hooks = {}; self.deleted = []; self.written = []
        self.fail_add = self.fail_delete = None
    def reg_read(self, reg):
        return self.regs[reg]
    def reg_write(self, reg, value):
        self.written.append((reg, value)); self.regs[reg] = value
    def hook_add(self, kind, callback):
        if kind == self.fail_add:
            raise RuntimeError('add')
        key = len(self.hooks) + 10; self.hooks[key] = (kind, callback); return key
    def hook_del(self, key):
        self.deleted.append(key)
        if key == self.fail_delete:
            raise RuntimeError('delete')
        del self.hooks[key]


class ArmWriteMonitorTests(unittest.TestCase):
    def setUp(self):
        self.engine = types.SimpleNamespace(__version__='2.1.4', UC_ARCH_ARM=1,
            UC_HOOK_CODE=4, UC_HOOK_MEM_WRITE=2048, UC_MEM_WRITE=17)
        self.r = types.SimpleNamespace(UC_ARM_REG_CPSR=1, UC_ARM_REG_PC=2)
        self.u = FakeCpu(); self.events = []
    def make(self, callback=None):
        return ArmWriteMonitor(self.u, self.engine, self.r,
            callback or (lambda *args: self.events.append(args)))
    def test_cold_import_does_not_import_engine_or_transport(self):
        result = subprocess.run([sys.executable, '-c', "import sys; from scripts.diagnostics import arm_write_monitor_audit; assert not {'unicorn','ctypes','serial'} & sys.modules.keys()"], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
    def test_rejects_unreviewed_engine_and_architecture_before_hooks(self):
        self.engine.__version__ = '2.1.5'
        with self.assertRaisesRegex(ValueError, 'engine-unreviewed'): self.make()
        self.engine.__version__ = '2.1.4'; self.u._arch = 2
        with self.assertRaisesRegex(ValueError, 'architecture-unreviewed'): self.make()
        self.assertEqual(self.u.hooks, {})
    def test_no_instruction_snapshot_never_reaches_guard(self):
        monitor = self.make()
        with self.assertRaisesRegex(ValueError, 'unobserved-instruction'):
            monitor._write(self.u, 17, 0x4000, 4, 42, None)
        self.assertEqual(self.events, []); self.assertEqual(self.u.written, [])
        monitor.close()
    def test_only_it_shadow_restored_before_range_guard(self):
        initial = 0xa00001f3 | 0x02000c00
        self.u.regs[1] = initial
        monitor = self.make(lambda *args: self.events.append(self.u.regs.copy()))
        monitor._snapshot(self.u, 0x1000, 2, None)
        self.u.regs[1] = 0x600001f3 | 0x06001c00
        monitor._write(self.u, 17, 0x4000, 4, 42, None)
        expected = (0x600001f3 & ~IT_MASK) | (initial & IT_MASK)
        self.assertEqual(self.events, [{1: expected, 2: 0x1000}])
        self.assertEqual(self.u.written, [(1, expected)])
        self.assertEqual(monitor.restoration_count, 1); monitor.close()
    def test_no_corruption_does_not_write_registers(self):
        monitor = self.make(); monitor._snapshot(self.u, 0x1000, 2, None)
        monitor._write(self.u, 17, 0x4000, 4, 42, None)
        self.assertEqual(len(self.events), 1); self.assertEqual(self.u.written, [])
        monitor.close()
    def test_instruction_and_access_mismatch_are_rejected(self):
        monitor = self.make(); monitor._snapshot(self.u, 0x1000, 2, None)
        for pc, access in ((0x1002, 17), (0x1000, 16)):
            self.u.regs[2] = pc
            with self.assertRaisesRegex(ValueError, 'unobserved-instruction'):
                monitor._write(self.u, access, 0x4000, 4, 42, None)
        self.assertEqual(self.events, []); monitor.close()
    def test_each_instruction_updates_snapshot_and_multiple_writes_share_it(self):
        monitor = self.make()
        for pc, it in ((0x1000, 0x02000c00), (0x1002, 0)):
            self.u.regs.update({1: 0xa00001f3 | it, 2: pc})
            monitor._snapshot(self.u, pc, 2, None)
            for address in (0x4000, 0x4004):
                self.u.regs[1] = 0xa00001f3 | 0x06001c00
                monitor._write(self.u, 17, address, 4, 42, None)
                self.assertEqual(self.u.regs[1] & IT_MASK, it)
        self.assertEqual(monitor.write_count, 4); monitor.close()
    def test_caller_range_rejection_is_preserved(self):
        def guard(*args): raise ValueError('range-rejected')
        monitor = self.make(guard); monitor._snapshot(self.u, 0x1000, 2, None)
        self.u.regs[1] |= IT_MASK
        with self.assertRaisesRegex(ValueError, 'range-rejected'):
            monitor._write(self.u, 17, 0xdead, 4, 42, None)
        self.assertEqual(self.u.regs[1] & IT_MASK, 0); monitor.close()
    def test_partial_install_removes_code_hook(self):
        self.u.fail_add = 2048
        with self.assertRaisesRegex(RuntimeError, 'add'): self.make()
        self.assertEqual(self.u.deleted, [10]); self.assertEqual(self.u.hooks, {})
    def test_close_attempts_both_hooks_once_even_on_failure(self):
        monitor = self.make(); self.u.fail_delete = 11
        with self.assertRaisesRegex(ValueError, 'hook-cleanup-failed'): monitor.close()
        monitor.close()
        self.assertEqual(self.u.deleted, [11, 10]); self.assertTrue(monitor.closed)
    def test_closed_or_wrong_owner_monitor_cannot_run(self):
        monitor = self.make()
        with self.assertRaisesRegex(ValueError, 'owner-mismatch'):
            remove_arm_write_monitor(FakeCpu(), monitor)
        monitor.close()
        with self.assertRaisesRegex(ValueError, 'closed-or-owner-mismatch'):
            monitor._snapshot(self.u, 0x1000, 2, None)
    def test_truth_conditions_and_fixed_expected_store_examples(self):
        self.assertTrue(predicate(0, 4)); self.assertFalse(predicate(1, 4))
        self.assertTrue(predicate(12, 0)); self.assertFalse(predicate(12, 4))
        value = (0x23456789).to_bytes(4, 'little'); untouched = b'\xa5' * 4
        self.assertEqual(expected_stores(0, 8, 4), value * 5)
        self.assertEqual(expected_stores(0, 8, 0), untouched + value * 4)
        self.assertEqual(expected_stores(0, 1, 0), untouched * 4 + value)
        self.assertEqual(expected_stores(0, 15, 0), untouched + value * 4)


if __name__ == '__main__':
    unittest.main()
