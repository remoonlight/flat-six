"""Explicit finite ARM IT/write-monitor qualification; no ELF or vehicle I/O."""
from pathlib import Path
import argparse
import hashlib
import json
import struct
import weakref
from .arm_write_monitor import ArmWriteMonitor, _check


def predicate(condition, flags):
    n, z, c, v = (bool(flags & b) for b in (8, 4, 2, 1))
    return (z, not z, c, not c, n, not n, v, not v,
        c and not z, not c or z, n == v, n != v,
        not z and n == v, z or n != v)[condition]


def expected_stores(condition, mask, flags):
    output = bytearray(b'\xa5' * 20)
    state = condition << 4 | mask
    for index in range(4):
        if state == 0 or predicate(state >> 4, flags):
            output[index * 4:index * 4 + 4] = (0x23456789).to_bytes(4, 'little')
        if state:
            state = 0 if state & 7 == 0 else (state & 0xe0) | ((state << 1) & 0x1f)
    output[16:20] = (0x23456789).to_bytes(4, 'little')
    return bytes(output)


def run_case(engine, r, condition, mask, flags, mode, cross_page):
    _check(mode in ('off', 'raw', 'preserved'), 'arm-monitor-audit-mode-invalid')
    u = engine.Uc(engine.UC_ARCH_ARM, engine.UC_MODE_THUMB)
    u.ctl_set_cpu_model(r.UC_CPU_ARM_CORTEX_A9)
    hooks = []; monitor = None; trace = []; writes = []
    try:
        u.mem_map(0x1000, 0x2000, 5)
        u.mem_map(0x4000, 0x1000, 3)
        u.mem_map(0x5000, 0x1000, 5)
        start = 0x1ffc if cross_page else 0x1000
        # IT; four distinct STRs; CMP r2,r2; BNE over final STR; BX lr.
        insns = [0xbf00 | condition << 4 | mask] + [0x6008 | i << 6 for i in range(4)]
        insns += [0x4292, 0xd100, 0x6108, 0x4770]
        u.mem_write(start, struct.pack('<' + 'H' * len(insns), *insns))
        u.mem_write(0x4000, b'\xa5' * 20)
        for reg, value in ((r.UC_ARM_REG_R0, 0x23456789), (r.UC_ARM_REG_R1, 0x4000),
                (r.UC_ARM_REG_R2, 0), (r.UC_ARM_REG_LR, 0x5001), (r.UC_ARM_REG_APSR, flags << 28)):
            u.reg_write(reg, value)
        def code(u, address, size, userdata):
            trace.append(address)
        def write(u, access, address, size, value, userdata):
            _check(0x4000 <= address and address + size <= 0x4014,
                'arm-monitor-audit-write-out-of-range')
            writes.append([address, size, value])
        hooks.append(u.hook_add(engine.UC_HOOK_CODE, code))
        if mode == 'preserved':
            monitor = ArmWriteMonitor(u, engine, r, write)
        elif mode == 'raw':
            hooks.append(u.hook_add(engine.UC_HOOK_MEM_WRITE, write))
        u.emu_start(start | 1, 0x5000, count=100)
        return dict(memoryHex=bytes(u.mem_read(0x4000, 20)).hex(),
            registers=[u.reg_read(x) for x in (r.UC_ARM_REG_R0, r.UC_ARM_REG_R1,
                r.UC_ARM_REG_R2, r.UC_ARM_REG_CPSR, r.UC_ARM_REG_PC)], instructionTrace=trace), writes
    finally:
        try:
            if monitor is not None:
                monitor.close()
            for hook in hooks:
                u.hook_del(hook)
        finally:
            finalizer = getattr(u, '_Uc__finalizer')
            _check(isinstance(finalizer, weakref.finalize) and finalizer.alive,
                'arm-monitor-audit-finalizer-invalid')
            finalizer(); u._uch = None
            _check(not finalizer.alive, 'arm-monitor-audit-engine-not-disposed')


def audit_matrix(output):
    import unicorn as engine
    from unicorn import arm_const as r
    _check(engine.__version__ == '2.1.4', 'arm-monitor-audit-engine-unreviewed')
    output.mkdir()
    rows = []; raw_differences = 0
    for cross in (False, True):
        for condition in range(14):
            for mask in range(1, 16):
                for flags in range(16):
                    expected = expected_stores(condition, mask, flags)
                    plain, _ = run_case(engine, r, condition, mask, flags, 'off', cross)
                    preserved, writes = run_case(engine, r, condition, mask, flags, 'preserved', cross)
                    raw, _ = run_case(engine, r, condition, mask, flags, 'raw', cross)
                    _check(plain['memoryHex'] == expected.hex(), 'arm-monitor-audit-baseline-disagrees')
                    _check(preserved == plain, 'arm-monitor-audit-preserved-state-disagrees')
                    expected_writes = [[0x4000 + i * 4, 4, 0x23456789] for i in range(5)
                        if expected[i * 4:(i + 1) * 4] != b'\xa5' * 4]
                    _check(writes == expected_writes, 'arm-monitor-audit-missed-or-extra-write')
                    raw_differences += raw != plain
                    rows.append(dict(condition=condition, mask=mask, flags=flags, crossPage=cross,
                        expectedMemoryHex=expected.hex(), baseline=plain, preserved=preserved,
                        raw=raw, monitoredWrites=writes, threeEnginesDisposed=True))
    folder = Path(__file__).resolve().parent
    report = dict(kind='arm32-preserved-write-monitor-audit', schemaVersion=1,
        engineVersion='2.1.4', cpuModel='Cortex-A9', caseCount=len(rows),
        rawHookDifferences=raw_differences, preservedDifferences=0, enginesDisposed=len(rows) * 3,
        syntheticItMatrixQualified=True, qualificationScope='14-conditions-15-masks-16-nzcv-2-page-layouts-word-stores',
        allArmInstructionsQualified=False, sourceCallerQualified=False, vehicleVerified=False,
        code=[dict(name=n, sha256=hashlib.sha256((folder / n).read_bytes()).hexdigest())
            for n in ('arm_write_monitor.py', 'arm_write_monitor_audit.py')], cases=rows)
    (output / 'result.json').write_text(json.dumps(report, indent=2) + '\n', encoding='utf8', newline='\n')
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir', type=Path, required=True)
    result = audit_matrix(parser.parse_args().output_dir)
    print(json.dumps({k: v for k, v in result.items() if k not in ('cases', 'code')}))


if __name__ == '__main__':
    main()
