"""Isolated original STD InputStringEx with an explicit virtual UI reply.

Executes the export, input helper and positive exchange-return branch. The
Android Notify function is replaced at its exact entry, not executed. Context,
strlen and strcpy are bounded models. No actual UI/head/vehicle communication.
"""
from pathlib import Path
from .arm_write_monitor import attach_arm_write_monitor
import argparse
import collections
import hashlib
import json
import struct
import time

from . import x431_sprintf_reference as cpu
from .x431_input_info_source import audit_input_info_sources

BUFFER, BUFFER_BYTES, SEQUENCE, EXCHANGE_POINTER = 0xe660a, 0x5000, 0xced58, 0xa606c
TITLE, CONDITIONS, INITIAL, OUTPUT = (cpu.DATA + n for n in (0x20000, 0x21000, 0x22000, 0x30000))
GUARDS = dict(cpuEmulationOnly=True, isolatedStringInputReference=True,
    stdInputInstructionsExecuted=True, virtualUiReplyOnly=True,
    stdContextInitializationModeled=True, libcStringOperationsModeled=True,
    actualAndroidNotifyExecuted=False, actualDynamicBindingQualified=False,
    runtimeCallbacksBound=False, androidInputWorkflowVerified=False,
    inputCharacterEncodingQualified=False, legalInputDomainQualified=False,
    actualInputInfoReturnQualified=False, capacityEdgeSafetyQualified=False,
    hostElfLoaded=False, vehicleVerified=False, codingWriteQualified=False,
    codingRestoreQualified=False, executionEnabled=False, writePayload=None)


def validate_input(title, conditions, reply, capacity, accepted, initial, destination_present, sequence):
    for name, value in (('title', title), ('conditions', conditions), ('initial', initial)):
        cpu._check(value is None or type(value) is bytes and len(value) <= 512 and b'\0' not in value,
            'string-input-reference-' + name + '-invalid')
    cpu._check(type(reply) is bytes and len(reply) <= 4096 and b'\0' not in reply,
        'string-input-reference-reply-invalid')
    cpu._check(type(capacity) is int and -1 <= capacity <= 4096,
        'string-input-reference-capacity-unreviewed')
    cpu._check(len(reply) <= max(0, capacity), 'string-input-reference-reply-exceeds-capacity')
    cpu._check(type(accepted) is bool and type(destination_present) is bool,
        'string-input-reference-state-invalid')
    cpu._check(type(sequence) is int and 0 <= sequence <= 255,
        'string-input-reference-sequence-invalid')


class CpuStringInputReference(cpu.CpuSprintfReference):
    def __init__(self, calc, std):
        self.source_audit = audit_input_info_sources(calc, std)
        try:
            import unicorn as engine
            from unicorn import arm_const
        except ImportError as error:
            raise ValueError('string-input-reference-private-unicorn-2.1.4-required') from error
        cpu._check(engine.__version__ == '2.1.4', 'string-input-reference-engine-unreviewed')
        self.engine, self.r = engine, arm_const
        self.u = engine.Uc(engine.UC_ARCH_ARM, engine.UC_MODE_THUMB)
        self.closed = False; self.poisoned = False; self.code_hook = self.write_hook = None
        self.active = False; self.trace = collections.Counter()
        self.allowed = ((0x26fb4, 0x26fca), (0x26fe0, 0x270f8), (0x321b0, 0x321e0))
        try:
            self._map(std)
            # Actual Notify has native callback/JNI branches. Neither is run.
            self.hooks[0x41248] = 'virtual-ui-notify'
            self.code_hook = self.u.hook_add(engine.UC_HOOK_CODE, self.code)
            self.write_hook = attach_arm_write_monitor(self.u, self.write)
        except Exception:
            self.close(); raise

    def _writable(self, address, size):
        return self.active and type(size) is int and 0 <= size <= BUFFER_BYTES and (
            cpu.STACK <= address and address + size <= cpu.STACK + 0x100000
            or BUFFER <= address and address + size <= BUFFER + self.buffer_extent
            or OUTPUT <= address and address + size <= OUTPUT + self.output_extent
            or address == SEQUENCE and size == 1)

    def write(self, u, access, address, size, value, userdata):
        cpu._check(self._writable(address, size), 'string-input-reference-write-range-unreviewed')

    def _put(self, address, data):
        cpu._check(self._writable(address, len(data)), 'string-input-reference-foundation-write-unreviewed')
        self.u.mem_write(address, data)

    def _cstring(self, address):
        bounds = ((TITLE, 513), (CONDITIONS, 513), (INITIAL, 513),
            (BUFFER, self.buffer_extent), (OUTPUT, self.output_extent),
            (cpu.STACK, 0x100000))
        matches = [(a, n) for a, n in bounds if a <= address < a + n]
        cpu._check(len(matches) == 1, 'string-input-reference-string-address-unreviewed')
        a, n = matches[0]
        raw = bytes(self.u.mem_read(address, min(a + n - address, 4097)))
        end = raw.find(b'\0')
        cpu._check(end >= 0, 'string-input-reference-string-unterminated')
        return raw[:end]

    def code(self, u, address, size, userdata):
        if address == cpu.STOP:
            return
        cpu._check(self.active, 'string-input-reference-no-active-call')
        name = self.hooks.get(address); r = self.r
        if name is None:
            cpu._check(any(a <= address < b for a, b in self.allowed)
                or 0x1e000 <= address < 0x1ec00, 'string-input-reference-code-range-unreviewed')
            self.trace['originalInstructions'] += 1
            if address in (0x26fb4, 0x26fe0, 0x321b0):
                self.entries.append(address)
            cpu._check(time.monotonic() < self.deadline, 'string-input-reference-wall-budget-exhausted')
            return
        self.trace[name] += 1
        a, b = (u.reg_read(reg) for reg in (r.UC_ARM_REG_R0, r.UC_ARM_REG_R1))
        result = 0
        if name == 'virtual-ui-notify':
            cpu._check(u.reg_read(r.UC_ARM_REG_LR) == 0x321c9 and a == BUFFER
                and b == len(self.expected_request) and not self.requests,
                'string-input-reference-notify-call-unreviewed')
            request = bytes(u.mem_read(a, b))
            cpu._check(request == self.expected_request, 'string-input-reference-ui-request-mismatch')
            self.requests.append(request.hex())
            response = bytearray(5) + self.reply + b'\0'
            response[:2] = len(response).to_bytes(2, 'big')
            response[2:5] = bytes((self.sequence, 0x10, int(self.accepted)))
            self._put(BUFFER, bytes(response))
            self.response = bytes(response)
            result = len(response)  # Positive exchange result, including cancel.
        elif name == 'strlen':
            result = len(self._cstring(a))
        elif name == 'strcpy':
            self._put(a, self._cstring(b) + b'\0'); result = a
        else:
            raise ValueError('string-input-reference-import-unreviewed:' + name)
        u.reg_write(r.UC_ARM_REG_R0, result)
        u.reg_write(r.UC_ARM_REG_PC, u.reg_read(r.UC_ARM_REG_LR))

    def input_bytes(self, title, conditions, reply=b'', *, capacity=4096, accepted=True,
                    initial=None, destination_present=True, sequence=0):
        validate_input(title, conditions, reply, capacity, accepted, initial, destination_present, sequence)
        cpu._check(not self.closed and not self.poisoned and not self.active,
            'string-input-reference-prior-outcome-unknown-or-closed')
        u, r = self.u, self.r; sp = cpu.STACK + 0x80000
        valid = title is not None and conditions is not None and destination_present and capacity > 0 and not initial
        self.reply, self.accepted, self.sequence = reply, accepted, sequence
        payload = (conditions or b'') + b'\0' + (title or b'') + b'\0' + (initial or b'') + b'\0'
        self.expected_request = (len(payload) + 4).to_bytes(2, 'big') + bytes((sequence, 0x10)) + payload
        self.output_extent = max(1, capacity + 1)
        self.buffer_extent = max(len(self.expected_request), max(0, capacity) + 6)
        before = bytes([0xa5]) * (self.output_extent + 32)
        buffer_before = bytes([0x5a]) * (self.buffer_extent + 32)
        self.trace.clear(); self.entries = []; self.requests = []; self.response = b''
        # Setup writes are explicit models, outside the original execution.
        for at, value in ((TITLE, title or b''), (CONDITIONS, conditions or b''), (INITIAL, initial or b'')):
            u.mem_write(at, value + b'\0')
        u.mem_write(OUTPUT, before); u.mem_write(BUFFER, buffer_before)
        u.mem_write(SEQUENCE, bytes((sequence,)))
        u.mem_write(EXCHANGE_POINTER, struct.pack('<I', BUFFER))
        u.mem_write(sp, struct.pack('<I', capacity & 0xffffffff))
        saved_regs = (r.UC_ARM_REG_R4, r.UC_ARM_REG_R5, r.UC_ARM_REG_R6, r.UC_ARM_REG_R7,
            r.UC_ARM_REG_R8, r.UC_ARM_REG_R9, r.UC_ARM_REG_R10, r.UC_ARM_REG_R11)
        saved = [u.reg_read(reg) for reg in saved_regs]
        for reg, value in ((r.UC_ARM_REG_R0, TITLE if title is not None else 0),
                (r.UC_ARM_REG_R1, CONDITIONS if conditions is not None else 0),
                (r.UC_ARM_REG_R2, INITIAL if initial is not None else 0),
                (r.UC_ARM_REG_R3, OUTPUT if destination_present else 0),
                (r.UC_ARM_REG_SP, sp), (r.UC_ARM_REG_LR, cpu.STOP | 1), (r.UC_ARM_REG_FPSCR, 0)):
            u.reg_write(reg, value)
        self.active = True; self.deadline = time.monotonic() + 2
        try:
            u.emu_start(0x26fb5, cpu.STOP, count=100_000)
            cpu._check(u.reg_read(r.UC_ARM_REG_PC) == cpu.STOP and u.reg_read(r.UC_ARM_REG_SP) == sp
                and [u.reg_read(reg) for reg in saved_regs] == saved,
                'string-input-reference-not-returned-or-registers-not-restored')
            value = u.reg_read(r.UC_ARM_REG_R0)
            value = value - 0x100000000 if value & 0x80000000 else value
            expected_return = len(reply) if valid and accepted else -1 if valid else -50
            written = reply + b'\0' if valid and accepted else b''
            raw = bytes(u.mem_read(OUTPUT, len(before)))
            cpu._check(value == expected_return and raw == written + before[len(written):]
                and len(self.requests) == int(valid), 'string-input-reference-output-invariant-failed')
            cpu._check(bytes(u.mem_read(BUFFER + self.buffer_extent, 32)) == buffer_before[-32:]
                and bytes(u.mem_read(SEQUENCE, 1)) == bytes(((sequence + int(valid)) & 255,)),
                'string-input-reference-buffer-or-sequence-invariant-failed')
            cpu._check(self.entries == [0x26fb4, 0x26fe0] + ([0x321b0] if valid else []),
                'string-input-reference-original-chain-not-observed')
            return dict(returnValue=value, inputBytes=len(reply), outputBytesHex=written.hex(),
                outputAllocatedBytes=self.output_extent, outputStorageSha256=hashlib.sha256(raw).hexdigest(),
                outputAndBufferSentinelsUnchanged=True, originalFunctionEntries=self.entries.copy(),
                uiRequestsHex=self.requests.copy(), virtualUiResponseHex=self.response.hex(),
                sequenceBefore=sequence, sequenceAfter=(sequence + int(valid)) & 255,
                trace=dict(self.trace), **GUARDS)
        except Exception:
            self.poisoned = True; raise
        finally:
            self.active = False


def audit_matrix(calc, std, output):
    output.mkdir()
    p = CpuStringInputReference(calc, std); rows = []
    cases = [
        ('empty', dict(reply=b'')), ('ascii', dict(reply=b'ABCD')),
        ('raw-utf8-bytes', dict(reply='测试'.encode('utf8'))),
        ('capacity-one', dict(reply=b'F', capacity=1)),
        ('caller-capacity-minus-one', dict(reply=b'F' * 4095)),
        ('capacity-plus-nul-probe', dict(reply=b'F' * 4096)),
        ('sequence-wrap', dict(reply=b'01', sequence=255)),
        ('cancel-empty', dict(accepted=False)), ('cancel-nonempty', dict(reply=b'AB', accepted=False)),
        ('null-title', dict(title=None)), ('null-conditions', dict(conditions=None)),
        ('null-destination', dict(destination_present=False)),
        ('zero-capacity', dict(capacity=0)), ('negative-capacity', dict(capacity=-1)),
        ('nonempty-initial', dict(initial=b'old')), ('empty-initial', dict(initial=b'')),
    ]
    try:
        for name, args in cases:
            kwargs = dict(title=b'field', conditions=b'input')
            kwargs.update(args)
            rows.append(dict(name=name, result=p.input_bytes(**kwargs)))
        source = p.source_audit
    finally:
        p.close()
    report = dict(kind='x431-original-string-input-reference', schemaVersion=1, sourceAudit=source,
        codeSha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(), cases=rows,
        caseCount=len(rows), engineVersion='2.1.4', engineDisposed=True,
        capacityProbeUsesExtraDestinationNulByte=True,
        actualCalcInputInfoExecuted=False, actualMinMaxCallerChainExecuted=False, **GUARDS)
    with (output / 'result.json').open('x', encoding='utf8', newline='\n') as out:
        out.write(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    return report


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    for key in ('calc', 'std', 'output-dir'):
        p.add_argument('--' + key, type=Path, required=True)
    a = p.parse_args(argv)
    report = audit_matrix(a.calc.read_bytes(), a.std.read_bytes(), a.output_dir)
    print(json.dumps(dict(cases=report['caseCount'], engineDisposed=report['engineDisposed'], **GUARDS)))


if __name__ == '__main__':
    main()
