"""Retained complete MinMax inverse caller under explicit isolated models.

This records source differences, cancellation and rejected virtual writes.
It never produces an executable vehicle write or qualifies Android encoding.
Only six retained formulas, raw replies, byte-aligned bounded buffers apply.
"""
from pathlib import Path
import argparse
import collections
import hashlib
import json
import struct
import sys
import time

from . import x431_cpu_reference as base
from .arm_write_monitor import attach_arm_write_monitor
from . import x431_sprintf_reference as cpu
from .x431_input_reference import CpuInputReference
from .x431_calc_input_info_reference import CpuCalcInputInfoReference, validate_info, validate_census, GUARDS as INFO_GUARDS
from .x431_minmax_prompt_reference import audit_minmax_sources
from .x431_text_field_reference import source_audit as audit_text_sources, FORMAT_SITES
from .x431_hex_sprintf_reference import CpuHexSprintfReference
from .coding_input_source import audit_coding_input_sources, _pic_address

FORMULAS = frozenset((
    '9681dca0c559e24ccab1d45d0731f0161f246e3c48fbe6e046b44069eeb974da',
    '3ffc9c781a2ed196370fb35511f2ecbeb5e499021e3a171faa44111d6f9946ca',
    'a253ce9ff8ed2beebc4187419e8bf75bfb640b54940e35d6cf91026de4196a3c',
    '0a3ee93e9ef0b5baa4df9c8d9399880816003ef326e408c86a2fb36b705f0640',
    '01d6fefd81cd2baa6317f49a96b66d913279d276f8d9d392ccb526628b6d52f0',
    '2997593599cf2016375417faddbae4394fca74b56b1dea4cc57dab2eaea68962'))
EXTRA_REGIONS = (
    ('OpenSearchIDDLL', 0xa8e58, 672, 'd77ecc5dd826107ef9439558d9356c72b012a185975d6cbc040a0feb1309a0ba'),
    ('OpenLanguageDLL', 0xaa6a0, 84, '9c0c7c50bb516258673b4b7d655a757793aa41064de9377c13031219e4d0e8b7'),
    ('local-string-prefix-helper', 0x25440, 132, '63143a892e098fcb979e743335228383c045926fcd97fc8891372ab1c61fe4e2'))
LOG, LENGTH, TITLE, FORMULA = (base.DATA + n for n in (0x10000, 0x12000, 0x21000, 0x22000))
GUARDS = {**INFO_GUARDS, 'originalMinMaxCallerChainExecuted': False,
    'originalCalcInputInfoInstructionsExecuted': False, 'loaderSuccessModeled': False,
    'allCodingInputPathsExecuted': False, 'rawCallerBufferOnly': True,
    'sourceCompleteCleanupQualified': False, 'sourceDifferencesAreProductRules': False,
    'completeCodingBlockQualified': False, 'cancellationIsVehicleSuccess': False}


class CpuReserveInstrumentationReference(CpuInputReference):
    """Small architectural counterexample, with an explicit optional no-op hook."""
    def __init__(self, calc, memory_hook):
        self.store_observed = False; self.write_hook = None
        super().__init__(calc)
        try:
            if memory_hook == 'preserved':
                self.write_hook = attach_arm_write_monitor(self.u, lambda *args: None)
            elif memory_hook:
                self.write_hook = self.u.hook_add(base.UC_HOOK_MEM_WRITE, lambda *args: None)
        except Exception:
            self.close(); raise

    def code(self, u, address, size, userdata):
        if address == 0xc5a0e:
            self.store_observed = True
        return super().code(u, address, size, userdata)

    def reserve(self, capacity):
        base._check(type(capacity) is int and capacity in (1, 2, 8), 'reserve-instrumentation-domain-unreviewed')
        obj = base.DATA + 0x20000; sp = base.STACK + 0x80000; u = self.u
        self.put_string(obj, 0, b'')
        for reg, value in ((base.UC_ARM_REG_R0, obj), (base.UC_ARM_REG_R1, capacity),
                (base.UC_ARM_REG_SP, sp), (base.UC_ARM_REG_LR, base.STOP | 1)):
            u.reg_write(reg, value)
        self.deadline = time.monotonic() + 3
        try:
            u.emu_start(0xc5981, base.STOP, count=10000)
            base._check(u.reg_read(base.UC_ARM_REG_PC) == base.STOP and u.reg_read(base.UC_ARM_REG_SP) == sp,
                'reserve-instrumentation-not-returned')
            pointer = self.uint(obj)
            valid = pointer - 12 in self.allocations and self.uint(pointer - 8) >= capacity
            return dict(capacity=capacity, objectPointer=pointer, emptyPointer=0x10ce1c,
                pointerHasOwnedCapacity=valid, storeInstructionObserved=self.store_observed,
                allocationsRemaining=len(self.allocations))
        finally:
            self.poisoned = True

    def close(self):
        cpu.CpuSprintfReference.close(self)


def audit_instrumentation(calc):
    rows = []; raw_differences = preserved_differences = 0
    for capacity in (1, 2, 8):
        group = []
        for mode in ('off', 'raw', 'preserved'):
            probe = CpuReserveInstrumentationReference(calc, False if mode == 'off' else True if mode == 'raw' else mode)
            try:
                result = probe.reserve(capacity)
            finally:
                probe.close()
            base._check(probe.closed, 'reserve-instrumentation-engine-not-disposed')
            group.append(dict(monitorMode=mode, engineDisposed=True, result=result))
        keys = ('objectPointer', 'pointerHasOwnedCapacity', 'storeInstructionObserved')
        raw_differences += any(group[0]['result'][k] != group[1]['result'][k] for k in keys)
        preserved_differences += any(group[0]['result'][k] != group[2]['result'][k] for k in keys)
        rows.extend(group)
    qualified = preserved_differences == 0 and all(r['result']['pointerHasOwnedCapacity']
        and r['result']['storeInstructionObserved'] for r in rows if r['monitorMode'] != 'raw')
    return dict(kind='original-cxx-reserve-instrumentation-counterexample', cases=rows, caseCount=9,
        hookDependentPairs=raw_differences, preservedHookDependentPairs=preserved_differences,
        rawMemoryWriteHookArchitecturallyQualified=raw_differences == 0,
        memoryWriteHookArchitecturallyQualified=qualified,
        qualificationScope='preserved-monitor-original-reserve-capacities-1-2-8',
        allNineEnginesDisposed=all(r['engineDisposed'] for r in rows), engineVersion='2.1.4',
        originalReserveEntryVa=0xc5981, pointerStoreVa=0xc5a0e,
        noOpHookChangesAreNotVendorBehavior=True, actualAndroidExecutionQualified=False)


def audit_sources(calc, std, deviceid, language, readwrite):
    # Verify all five originals before any optional execution engine is imported.
    info = audit_minmax_sources(calc, std, language)
    text = audit_text_sources(calc, std, deviceid)
    coding = audit_coding_input_sources(calc, readwrite)
    elf = base._Elf(calc)
    regions = []
    for name, start, size, sha in EXTRA_REGIONS:
        base._check(hashlib.sha256(elf.raw(start, size)).hexdigest() == sha, 'negate-extra-source-unreviewed')
        if name != 'local-string-prefix-helper':
            symbols = [s for s in elf.symbols if s['name'] == name and s['section'] and s['type'] == 2]
            base._check(len(symbols) == 1 and symbols[0]['va'] == start | 1
                and symbols[0]['bytes'] == size, 'negate-loader-symbol-unreviewed')
        regions.append(dict(name=name, startVa=start, bytes=size, sha256=sha))
    strings = []
    for load, add, expected in ((0x2479e, 0x247a2, 'IDENTICAL:'),
            (0x249ae, 0x249b6, '%d'), (0x2f2b0, 0x2f2b4, '0')):
        address = _pic_address(elf, load, add)
        base._check(elf.cstring(address) == expected, 'negate-caller-string-unreviewed')
        strings.append(dict(loadVa=load, addVa=add, stringVa=address, value=expected))
    return dict(inputInfo=info, inverse=text, coding=coding, extraRegions=regions, strings=strings,
        argumentLayout=dict(registers=['title-cstr', 'text-log-cstr', 'formula-cstr', 'raw-buffer'],
            stack=['incoming-length', 'byte-offset', 'bit-offset-also-input-flag', 'length-text-cstr'],
            readwriteCallVa=0xfe272, readwriteStackSetupVa=0xfe262, incomingLengthIsInverseR3=False))


def validate_call(formula, replies, original, byte=4, bit=0, title=b'field'):
    parsed = validate_info(formula, replies, title, bit)
    base._check(hashlib.sha256(formula.encode()).hexdigest() in FORMULAS, 'negate-exact-source-formula-required')
    base._check(type(original) is bytes and 512 <= len(original) <= 4096
        and type(byte) is int and 2 <= byte <= len(original) - 512,
        'negate-source-window-or-header-unreviewed')
    base._check(type(bit) is int and bit == 0, 'negate-bit-offset-unreviewed')
    return parsed


class RejectedVirtualWrite(ValueError):
    def __init__(self, detail):
        self.detail = detail
        super().__init__('negate-virtual-write-range-unreviewed')


class CpuNegateCallerReference(CpuCalcInputInfoReference):
    def __init__(self, calc, std, deviceid, language, readwrite):
        self.negate_audit = audit_sources(calc, std, deviceid, language, readwrite)
        instrumentation = audit_instrumentation(calc)
        base._check(instrumentation['memoryWriteHookArchitecturallyQualified'],
            'negate-memory-write-instrumentation-unqualified')
        self.active_bytes = 0
        self.chain_events = []; self.hex_events = []; self.caller_formats = []; self.branch_events = []; self.prefix_events = []
        super().__init__(calc, std, deviceid, language)
        try:
            self.hex = CpuHexSprintfReference(calc, std, deviceid)
            for _, start, _, _ in EXTRA_REGIONS[:2]:
                self.hooks[start] = 'reviewed-show-loader-model'
        except Exception:
            self.close(); raise

    def write(self, u, access, address, size, value, userdata):
        base._check(type(size) is int and 0 <= size <= 65536, 'negate-write-size-unreviewed')
        if (base.DATA <= address and address + size <= base.DATA + self.active_bytes
                or LOG <= address and address + size <= LOG + 4096
                or LENGTH <= address and address + size <= LENGTH + 32):
            return
        try:
            super().write(u, access, address, size, value, userdata)
        except ValueError as error:
            raise RejectedVirtualWrite(dict(address=address, size=size, value=value,
                instructionVa=self.u.reg_read(base.UC_ARM_REG_PC))) from error

    def code(self, u, address, size, userdata):
        name = self.hooks.get(address)
        if address in (0x25440, 0x2548c):
            base._check(len(self.prefix_events) < 2, 'negate-prefix-observation-budget')
            if address == 0x25440:
                prefix = u.reg_read(base.UC_ARM_REG_R1)
                source = u.reg_read(base.UC_ARM_REG_R2)
                self.prefix_events.append(dict(va=address, prefixBytesHex=self.cstr(prefix).hex().upper(),
                    inputBytesHex=self.cstr(self.uint(source)).hex().upper()))
            else:
                obj = u.reg_read(base.UC_ARM_REG_R4)
                self.prefix_events.append(dict(va=address, outputBytesHex=self.cstr(self.uint(obj)).hex().upper()))
        if address in (0x247e2, 0x247e6, 0x24810, 0x2483e, 0x24842):
            base._check(len(self.chain_events) < 8, 'negate-chain-observation-budget')
            registers = [u.reg_read(r) for r in
                (base.UC_ARM_REG_R0, base.UC_ARM_REG_R1, base.UC_ARM_REG_R2, base.UC_ARM_REG_R3)]
            sp = u.reg_read(base.UC_ARM_REG_SP)
            event = dict(va=address, registers=registers, stackWords=[self.uint(sp + i * 4) for i in range(3)])
            if address == 0x247e6:
                event['inputInfoReturn'] = signed(registers[0])
                event['inputBytesHex'] = self.cstr(self.uint(sp + 0xac)).hex().upper()
            if address == 0x2483e:
                event['inverseInputInfoResultR3'] = signed(registers[3])
                event['inputBytesHex'] = self.cstr(self.uint(registers[1])).hex().upper()
                base._check(registers[2] == base.DATA and event['stackWords'][:2] == [self.byte, 0],
                    'negate-inverse-argument-propagation-unreviewed')
            if address == 0x24842:
                event['inverseReturn'] = signed(registers[0])
            self.chain_events.append(event)
        # Record actual length, terminating-byte and mask calculations; do not supply them.
        if address in (0x2f29a, 0x2f344, 0x2f7ea, 0x2fcf4, 0x2fd8a):
            base._check(len(self.branch_events) < 8192, 'negate-branch-observation-budget')
            registers = [u.reg_read(r) for r in
                (base.UC_ARM_REG_R0, base.UC_ARM_REG_R1, base.UC_ARM_REG_R2, base.UC_ARM_REG_R3)]
            event = dict(va=address, registers=registers)
            if address == 0x2f29a:
                event['rawLengthBeforeHalving'] = signed(u.reg_read(base.UC_ARM_REG_R6))
            if address == 0x2f344:
                event['scannedRawByte'] = bytes(u.mem_read(registers[1] + u.reg_read(base.UC_ARM_REG_R5), 1)).hex().upper()
            self.branch_events.append(event)
        if name == 'sprintf':
            a, b, c = [u.reg_read(r) for r in (base.UC_ARM_REG_R0, base.UC_ARM_REG_R1, base.UC_ARM_REG_R2)]
            lr = u.reg_read(base.UC_ARM_REG_LR); sp = u.reg_read(base.UC_ARM_REG_SP)
            if lr & ~1 == 0x249bc:
                base._check(a == LENGTH and b == 0xf5db9 and not self.caller_formats,
                    'negate-caller-length-format-unreviewed')
                result = self.integer.format_integer(b'%d', struct.pack('<I', c), u.reg_read(base.UC_ARM_REG_FPSCR))
                self.caller_formats.append(dict(returnVa=lr & ~1, argumentWord=c, result=result))
            else:
                base._check(b == 0xf61f8 and lr & ~1 in {r[2] + 4 for r in FORMAT_SITES}
                    and sp <= a and a + 50 <= sp + 0x1150 and len(self.hex_events) < 8192,
                    'negate-hex-format-callsite-unreviewed')
                result = self.hex.format_byte(self.cstr(b), c, u.reg_read(base.UC_ARM_REG_FPSCR))
                self.hex_events.append(dict(returnVa=lr & ~1, argumentByte=c, result=result))
            data = bytes.fromhex(result['outputBytesHex']) + b'\0'
            self.write(u, None, a, len(data), None, None); u.mem_write(a, data)
            for reg, value in zip((base.UC_ARM_REG_R0, base.UC_ARM_REG_R1, base.UC_ARM_REG_R2, base.UC_ARM_REG_R3),
                    result['returnedRegisters']):
                u.reg_write(reg, value)
            u.reg_write(base.UC_ARM_REG_APSR, result['returnedApsr'])
            u.reg_write(base.UC_ARM_REG_FPSCR, result['returnedFpscr'])
            u.reg_write(base.UC_ARM_REG_PC, lr); return
        if name == 'strcat':
            a, b = [u.reg_read(r) for r in (base.UC_ARM_REG_R0, base.UC_ARM_REG_R1)]
            lr = u.reg_read(base.UC_ARM_REG_LR)
            base._check(a == LOG and lr & ~1 == 0x24866, 'negate-log-append-callsite-unreviewed')
            data = self.cstr(a) + self.cstr(b) + b'\0'
            self.write(u, None, a, len(data), None, None); u.mem_write(a, data)
            self.trace['modeledBoundedStrcat'] += 1
            u.reg_write(base.UC_ARM_REG_R0, a); u.reg_write(base.UC_ARM_REG_PC, lr); return
        if 0x24634 <= address < 0x25350 or 0x25440 <= address < 0x254c4:
            self.last.append(address); self.trace['originalInstructions'] += 1
            base._check(time.monotonic() < self.deadline, 'negate-caller-wall-budget-exhausted')
            return
        return super().code(u, address, size, userdata)

    def validate_arguments(self, formula, replies, original, byte, bit, title):
        return validate_call(formula, replies, original, byte, bit, title)

    def execution_limits(self):
        return 20, 8_000_000

    def call(self, formula, replies, original, byte=4, bit=0, title=b'field'):
        self.validate_arguments(formula, replies, original, byte, bit, title)
        base._check(not self.closed and not self.poisoned, 'negate-prior-outcome-unknown-or-closed')
        seconds,instructions=self.execution_limits()
        base._check(type(seconds) in (int,float) and 0<seconds<=60
            and type(instructions) is int and 0<instructions<=8_000_000,
            'negate-execution-budget-unreviewed')
        self.replies = replies; self.byte = byte; self.active_bytes = len(original)
        u = self.u; sp = base.STACK + 0x80000
        u.mem_write(base.DATA, original + b'\xa7' * 32)
        u.mem_write(LOG, bytes(4096) + b'\xa7' * 32)
        u.mem_write(LENGTH, bytes(32) + b'\xa7' * 32)
        u.mem_write(TITLE, title + b'\0'); u.mem_write(FORMULA, formula.encode() + b'\0')
        u.mem_write(sp, struct.pack('<IIII', len(original), byte, bit, LENGTH))
        saved_regs = (base.UC_ARM_REG_R4, base.UC_ARM_REG_R5, base.UC_ARM_REG_R6, base.UC_ARM_REG_R7,
            base.UC_ARM_REG_R8, base.UC_ARM_REG_R9, base.UC_ARM_REG_R10, base.UC_ARM_REG_R11)
        saved = [u.reg_read(r) for r in saved_regs]
        for reg, value in ((base.UC_ARM_REG_R0, TITLE), (base.UC_ARM_REG_R1, LOG),
                (base.UC_ARM_REG_R2, FORMULA), (base.UC_ARM_REG_R3, base.DATA),
                (base.UC_ARM_REG_SP, sp), (base.UC_ARM_REG_LR, base.STOP | 1)):
            u.reg_write(reg, value)
        self.deadline = time.monotonic() + seconds
        boundary = None
        try:
            try:
                u.emu_start(0x24761, base.STOP, count=instructions)
            except RejectedVirtualWrite as error:
                boundary = error.detail
            if boundary is None:
                base._check(u.reg_read(base.UC_ARM_REG_PC) == base.STOP and u.reg_read(base.UC_ARM_REG_SP) == sp
                    and [u.reg_read(r) for r in saved_regs] == saved and self.pending_pair is None
                    and self.response_index == len(replies), 'negate-caller-return-or-lifetime-unreviewed')
                base._check(self.allocations == {self.source_input_buffer:4096},
                    'negate-caller-unexpected-remaining-allocation')
            for a, n in ((base.DATA, len(original)), (LOG, 4096), (LENGTH, 32)):
                base._check(bytes(u.mem_read(a + n, 32)) == b'\xa7' * 32, 'negate-buffer-sentinel-changed')
            return dict(completed=boundary is None, rejectedVirtualWrite=boundary,
                returnValue=signed(u.reg_read(base.UC_ARM_REG_R0)) if boundary is None else None,
                afterHex=bytes(u.mem_read(base.DATA, len(original))).hex().upper() if boundary is None else None,
                textLogBytesHex=self.cstr(LOG).hex().upper() if boundary is None else None,
                lengthTextBytesHex=self.cstr(LENGTH).hex().upper() if boundary is None else None,
                chainEvents=self.chain_events.copy(), branchEvents=self.branch_events.copy(), prefixEvents=self.prefix_events.copy(),
                validationEvents=self.validation_events.copy(), uiEvents=self.ui_events.copy(),
                hexEvents=self.hex_events.copy(), callerFormats=self.caller_formats.copy(),
                numericConversions=self.numeric_events.copy(), repliesConsumed=self.response_index,
                sourceInputBufferRemainingBytes=4096 if boundary is None else None,
                callerStringsDisposed=boundary is None, virtualAllocationsRemaining=len(self.allocations),
                reuseAllowed=False, trace=dict(self.trace), **{**GUARDS,
                    'originalMinMaxCallerChainExecuted':True,
                    'originalCalcInputInfoInstructionsExecuted':True, 'loaderSuccessModeled':True})
        finally:
            self.poisoned = True

    def close(self):
        errors = []
        if hasattr(self, 'hex'):
            try:
                self.hex.close()
            except Exception as error:
                errors.append(error)
        try:
            super().close()
        except Exception as error:
            errors.append(error)
        if errors:
            raise ValueError('negate-caller-engine-cleanup-failed') from errors[0]


def signed(value):
    return value - (1 << 32) if value >> 31 else value


def matrix_cases(census):
    for definition in validate_census(census):
        formula = definition['formula']; parsed = definition['parsed']
        target = b'4142434445464748' if parsed['baseDataType'] == 'A_BYTEFIELD' else b'ABCDEFGH'
        odd = parsed['baseDataType'] == 'A_BYTEFIELD' and parsed['minLength'] == 0
        retry = b'A' if parsed['baseDataType'] == 'A_BYTEFIELD' else b'Z' * (parsed['maxLength'] + 1)
        for name, original, replies in (
                ('valid-zero', bytes(4096), [dict(data=target, accepted=True)]),
                ('valid-a5', b'\xa5' * 4096, [dict(data=target, accepted=True)]),
                ('valid-pattern', bytes(range(256)) * 16, [dict(data=target, accepted=True)]),
                ('cancel', b'\xa5' * 4096, [dict(data=b'', accepted=False)]),
                ('cancel-nonempty-reply', b'\xa5' * 4096, [dict(data=target, accepted=False)]),
                ('odd-source-boundary' if odd else 'retry', b'\xa5' * 4096,
                    [dict(data=b'A', accepted=True)] if odd else
                    [dict(data=retry, accepted=True), dict(data=target, accepted=True)])):
            yield definition, name, original, replies


def compare_observation(definition, original, result):
    """Independent raw-byte comparison for this matrix, never an editing rule."""
    events = result['chainEvents']
    info = next(e for e in events if e['va'] == 0x247e6)
    inverse = next(e for e in events if e['va'] == 0x2483e)
    base._check(info['inputInfoReturn'] == inverse['inverseInputInfoResultR3'],
        'negate-actual-input-return-not-propagated')
    if not result['completed']:
        return dict(comparisonCompleted=False, canceled=info['inputInfoReturn'] == -1)
    returned = next(e['inverseReturn'] for e in events if e['va'] == 0x24842)
    after = bytes.fromhex(result['afterHex'])
    if info['inputInfoReturn'] == -1:
        base._check(after == original and result['textLogBytesHex'] == ''
            and result['lengthTextBytesHex'] == '31' and returned == 1, 'negate-cancel-source-semantics-changed')
        return dict(comparisonCompleted=True, canceled=True, bufferUnchanged=True,
            outerReturnIsNotInputAcceptance=True, reportedLength=1)
    data = bytes.fromhex(inverse['inputBytesHex']); parsed = definition['parsed']
    base._check(data in (b'4142434445464748', b'ABCDEFGH', b'A') and len(original) == 4096,
        'negate-independent-matrix-domain-unreviewed')
    normalized = b'0' + data if parsed['baseDataType'] == 'A_BYTEFIELD' and len(data) % 2 else data
    if normalized != data:
        base._check(result['prefixEvents'] == [
            dict(va=0x25440, prefixBytesHex='30', inputBytesHex=data.hex().upper()),
            dict(va=0x2548c, outputBytesHex=normalized.hex().upper())], 'negate-odd-prefix-observation-different')
    else:
        base._check(not result['prefixEvents'], 'negate-unexpected-prefix-helper')
    direct = bytes.fromhex(normalized.decode()) if parsed['baseDataType'] == 'A_BYTEFIELD' else data
    count = len(normalized) // 2
    if parsed['termination'] == 'ZERO':
        count = next((i for i in range(count) if original[4 + i] == 0), count)
    payload = direct[:count]
    expected = bytearray(original)
    expected[1] = (4 + count - 2) & 255
    for i, value in enumerate(payload):
        expected[4 + i] |= value
    base._check(after == bytes(expected) and returned == count
        and bytes.fromhex(result['lengthTextBytesHex']) == str(count).encode(),
        'negate-independent-source-observation-different')
    naive_patch = original[:4] + direct + original[4 + len(direct):]
    return dict(comparisonCompleted=True, canceled=False, independentSourceObservationMatched=True,
        sourcePayloadBytes=count, rawInputBytes=len(data), sourceLeadingZeroPrefixObserved=normalized != data, reportedLength=returned,
        completeDirectReplacementMatched=after == naive_patch,
        changedOffsets=[i for i, (a, b) in enumerate(zip(original, after)) if a != b],
        lengthHeaderByteOffset=1, lengthHeaderByte=after[1],
        sourceComparisonIsNotVehicleEncodingRule=True)


def audit_matrix(calc, std, deviceid, language, readwrite, census, output):
    audit = audit_sources(calc, std, deviceid, language, readwrite)
    validate_census(census)
    instrumentation = audit_instrumentation(calc)
    output.mkdir(); rows = []; counts = collections.Counter(instrumentationCases=9,
        hookDependentPairs=instrumentation['hookDependentPairs'], cases=0)
    qualified = instrumentation['memoryWriteHookArchitecturallyQualified']
    cases = matrix_cases(census) if qualified else ()
    for definition, name, original, replies in cases:
        probe = CpuNegateCallerReference(calc, std, deviceid, language, readwrite)
        try:
            result = probe.call(definition['formula'], replies, original)
        finally:
            probe.close()
        base._check(probe.closed and all(getattr(probe, k).closed for k in
            ('hex', 'pair', 'string', 'integer', 'language', 'sprintf')), 'negate-seven-engines-not-disposed')
        counts['cases'] += 1; counts[name] += 1
        counts['completed' if result['completed'] else 'rejectedVirtualWrite'] += 1
        counts['uiRepliesConsumed'] += result['repliesConsumed']
        comparison = compare_observation(definition, original, result)
        base._check(result['completed'] and result['returnValue'] == 0, 'negate-caller-did-not-return')
        if comparison.get('canceled'):
            counts['cancellationObserved'] += 1
        else:
            counts['independentSourceObservationMatched'] += 1
            counts['leadingZeroPrefixObserved'] += comparison['sourceLeadingZeroPrefixObserved']
            counts['completeDirectReplacementMatched' if comparison['completeDirectReplacementMatched']
                else 'completeDirectReplacementDifferent'] += 1
        rows.append(dict(case=name, formula=definition['formula'], sourceRecords=definition['records'],
            originalSha256=hashlib.sha256(original).hexdigest(), sevenEnginesDisposed=True,
            comparison=comparison, result=result))
    folder = Path(__file__).resolve().parent
    names = sorted({Path(module.__file__).name for module in tuple(sys.modules.values())
        if getattr(module, '__file__', None) and Path(module.__file__).resolve().parent == folder
        and Path(module.__file__).suffix == '.py'} | {Path(__file__).name})
    report = dict(kind='x431-complete-minmax-inverse-caller-reference', schemaVersion=1,
        sourceAudit=audit, counts=dict(counts), cases=rows, instrumentation=instrumentation,
        code=[dict(name=n, sha256=hashlib.sha256(Path(__file__).with_name(n).read_bytes()).hexdigest()) for n in names],
        **GUARDS)
    report.update(memoryWriteHookArchitecturallyQualified=qualified, callerMatrixExecutionStopped=not qualified,
        originalMinMaxCallerChainExecuted=qualified and len(rows) > 0, referenceQualified=qualified,
        originalCalcInputInfoInstructionsExecuted=qualified and len(rows) > 0,
        loaderSuccessModeled=qualified and len(rows) > 0,
        sourceBehaviorProvenByHistoricalHookedMatrix=False)
    (output / 'result.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf8', newline='\n')
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('calc', 'std', 'deviceid', 'language', 'readwrite', 'census', 'output-dir'):
        parser.add_argument('--' + name, type=Path, required=True)
    a = parser.parse_args()
    report = audit_matrix(a.calc.read_bytes(), a.std.read_bytes(), a.deviceid.read_bytes(), a.language.read_bytes(),
        a.readwrite.read_bytes(), json.loads(a.census.read_text(encoding='utf8')), a.output_dir)
    print(json.dumps({k: report[k] for k in ('counts', 'referenceQualified', 'callerMatrixExecutionStopped',
        'originalMinMaxCallerChainExecuted', 'originalCalcInputInfoInstructionsExecuted',
        'memoryWriteHookArchitecturallyQualified', 'vehicleVerified', 'executionEnabled')}))


if __name__ == '__main__':
    main()
