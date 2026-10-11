"""ARM32 write guards preserving the pinned engine's pre-callback IT shadow.

Unicorn 2.1.4 cputlb.c calls cpu_restore_state before a memory callback. ARM
restore_state_to_opc also replaces condexec_bits, which the current translated
block normally keeps zero and tracks statically. Keep that runtime shadow as
observed by a preceding code hook; never synthesize an architectural IT state,
change NZCV, or relax the caller's write guard. Explicit CPU research only.
"""
IT_MASK = 0x0600fc00


def _check(condition, reason):
    if not condition:
        raise ValueError(reason)


class ArmWriteMonitor:
    def __init__(self, u, engine, registers, callback):
        _check(engine.__version__ == '2.1.4', 'arm-write-monitor-engine-unreviewed')
        _check(u._arch == engine.UC_ARCH_ARM, 'arm-write-monitor-architecture-unreviewed')
        self.u, self.engine, self.r, self.callback = u, engine, registers, callback
        self.code_hook = self.write_hook = None
        self.pc = self.saved_it = None
        self.closed = False
        self.write_count = self.restoration_count = 0
        try:
            self.code_hook = u.hook_add(engine.UC_HOOK_CODE, self._snapshot)
            self.write_hook = u.hook_add(engine.UC_HOOK_MEM_WRITE, self._write)
        except Exception:
            self.close()
            raise

    def _snapshot(self, u, address, size, userdata):
        _check(not self.closed and u is self.u, 'arm-write-monitor-closed-or-owner-mismatch')
        self.pc = address
        self.saved_it = u.reg_read(self.r.UC_ARM_REG_CPSR) & IT_MASK

    def _write(self, u, access, address, size, value, userdata):
        _check(not self.closed and u is self.u and self.saved_it is not None
            and access == self.engine.UC_MEM_WRITE
            and u.reg_read(self.r.UC_ARM_REG_PC) == self.pc,
            'arm-write-monitor-unobserved-instruction')
        cpsr = u.reg_read(self.r.UC_ARM_REG_CPSR)
        if cpsr & IT_MASK != self.saved_it:
            # Only the engine's internal IT shadow was replaced by its sync.
            # Preserve the current flags, processor mode, Thumb state and PC.
            u.reg_write(self.r.UC_ARM_REG_CPSR, (cpsr & ~IT_MASK) | self.saved_it)
            self.restoration_count += 1
        self.write_count += 1
        return self.callback(u, access, address, size, value, userdata)

    def close(self):
        if self.closed:
            return
        errors = []
        for hook in (self.write_hook, self.code_hook):
            if hook is not None:
                try:
                    self.u.hook_del(hook)
                except Exception as error:
                    errors.append(error)
        self.closed = True
        self.code_hook = self.write_hook = None
        self.pc = self.saved_it = None
        if errors:
            raise ValueError('arm-write-monitor-hook-cleanup-failed') from errors[0]


def attach_arm_write_monitor(u, callback):
    # Remains absent from cold/default imports and normal vehicle transports.
    import unicorn as engine
    from unicorn import arm_const
    return ArmWriteMonitor(u, engine, arm_const, callback)


def remove_arm_write_monitor(u, hook):
    if isinstance(hook, ArmWriteMonitor):
        _check(hook.u is u, 'arm-write-monitor-owner-mismatch')
        hook.close()
    else:
        u.hook_del(hook)
