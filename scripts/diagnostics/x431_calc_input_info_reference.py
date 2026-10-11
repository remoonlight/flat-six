"""Complete retained CALC MinMax input callers with isolated UI callouts.

Original condition generation, title lookup, validation, retries and return
propagation execute. Loaders remain explicit no-binding models; STD input uses
a virtual reply. This is neither Android UI qualification nor a vehicle editor.
"""
from pathlib import Path
from .arm_write_monitor import attach_arm_write_monitor
import argparse
import collections
import hashlib
import json
import struct
import time

from . import x431_numeric_input_reference as previous
from . import x431_input_reference as original
from . import x431_cpu_reference as base
from . import x431_sprintf_reference as cpu
from .x431_minmax_prompt_reference import (audit_minmax_sources, CpuMinMaxLanguageReference,
    CpuMinMaxSprintfReference, REGIONS, PAIRS)
from .x431_string_input_reference import CpuStringInputReference
from .x431_formula import parse_formula_text

GUARDS = dict(cpuEmulationOnly=True, originalCalcInputInfoInstructionsExecuted=True,
    isolatedCallbackSubstitution=True, virtualUiReplyOnly=True, loaderSuccessModeled=True,
    actualDynamicBindingQualified=False, runtimeCallbacksBound=False,
    actualAndroidNotifyExecuted=False, androidInputWorkflowVerified=False,
    actualInputInfoReturnQualified=False, inputCharacterEncodingQualified=False,
    legalInputDomainQualified=False, actualLibcVersionMatched=False,
    hostElfLoaded=False, vehicleVerified=False, codingWriteQualified=False,
    codingRestoreQualified=False, executionEnabled=False, writePayload=None)
CENSUS_CANONICAL_SHA256 = '4e5e89219d452309c0fa84e486645c8da08ec19b1cf6f82290e8185624a166af'


def validate_census(census):
    digest = hashlib.sha256(json.dumps(census, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    base._check(digest == CENSUS_CANONICAL_SHA256, 'calc-input-info-census-unreviewed')
    return [r for r in census['definitions'] if r['parsed'].get('lengthInfo') == 'MinMax']


def validate_info(formula, replies, title, flag):
    base._check(type(formula) is str and formula.isascii() and 1 <= len(formula) <= 1000
        and '\0' not in formula, 'calc-input-info-formula-invalid')
    parsed = parse_formula_text(formula)
    base._check(parsed.get('ok') and parsed.get('kind') == 'IDENTICAL' and parsed.get('lengthInfo') == 'MinMax'
        and parsed.get('baseDataType') in ('A_BYTEFIELD', 'A_ASCIISTRING'), 'calc-input-info-family-unreviewed')
    base._check(type(replies) is list and 1 <= len(replies) <= 3, 'calc-input-info-reply-budget-required')
    for reply in replies:
        base._check(type(reply) is dict and set(reply) == {'data', 'accepted'}
            and type(reply['data']) is bytes and len(reply['data']) <= 4095 and b'\0' not in reply['data']
            and type(reply['accepted']) is bool, 'calc-input-info-reply-invalid-or-nul-capacity-unqualified')
    base._check(type(title) is bytes and len(title) <= 64 and b'\0' not in title
        and b'0x' not in title and b'0X' not in title, 'calc-input-info-title-invalid')
    base._check(type(flag) is int and flag == 0, 'calc-input-info-flag-unreviewed')
    return parsed


class CpuCalcInputInfoReference(previous.CpuNumericInputReference):
    def __init__(self, calc, std, deviceid, language_blob):
        self.info_audit = audit_minmax_sources(calc, std, language_blob)
        super().__init__(calc, std, deviceid, language_blob)
        self.write_hook = None; self.source_input_buffer = None
        self.info_regions = [(r['startVa'], r['startVa'] + r['bytes'])
            for r in self.info_audit['inputInfoSource']['functionRegions'] if r['library'] == 'calc'
            and r['symbol'] in ('_Z21GetIDENTICALInputInfoSsRSsSsi', '_Z19GetUserInutCondiStrSsSsRSsSsi')]
        self.info_regions += [(a, a + n) for _, a, n, _ in REGIONS]
        self.pairs = {r[0]:r for r in PAIRS}; self.formats = {r[3]:r for r in PAIRS}
        self.info_events = []; self.ui_events = []; self.validation_events = []; self.entries = []
        self.pending_pair = None; self.response_index = 0
        try:
            self.language.close()
            self.language = CpuMinMaxLanguageReference(calc, std, language_blob)
            self.pair = CpuMinMaxSprintfReference(calc, std, deviceid)
            self.string = CpuStringInputReference(calc, std)
            self.hooks[0xa90f8] = 'reviewed-show-loader-model'
            self.hooks[0xaa988] = 'reviewed-device-loader-model'
            self.write_hook = attach_arm_write_monitor(self.u, self.write)
        except Exception:
            self.close(); raise

    def cstr(self, address):
        result = bytearray()
        for offset in range(4096):
            value = self.u.mem_read(address + offset, 1)[0]
            if not value:
                return bytes(result)
            result.append(value)
        raise ValueError('calc-input-info-string-unbounded')

    def write(self, u, access, address, size, value, userdata):
        base._check(type(size) is int and 0 <= size <= 65536 and (
            base.STACK <= address and address + size <= base.STACK + 0x100000
            or base.DATA + 0x20000 <= address and address + size <= base.DATA + 0x20304
            or any(a <= address and address + size <= a + n for a, n in self.allocations.items())),
            'calc-input-info-write-range-unreviewed')

    def code(self, u, address, size, userdata):
        if address in (0x36900, 0x369e0, 0x32e4c, 0x38a4c, 0x33fd8):
            self.entries.append(address)
        name = self.hooks.get(address)
        if name in ('memcpy', 'memmove', '__aeabi_memcpy', '__aeabi_memcpy4', '__aeabi_memcpy8', 'memset'):
            a, c = (u.reg_read(r) for r in (base.UC_ARM_REG_R0, base.UC_ARM_REG_R2))
            self.write(u, None, a, c, None, None)
        if name in ('__aeabi_memclr', '__aeabi_memclr4', '__aeabi_memclr8'):
            a, b = (u.reg_read(r) for r in (base.UC_ARM_REG_R0, base.UC_ARM_REG_R1))
            self.write(u, None, a, b, None, None)
        if name in ('reviewed-show-loader-model', 'reviewed-device-loader-model'):
            self.trace[name] += 1
            u.reg_write(base.UC_ARM_REG_R0, 0); u.reg_write(base.UC_ARM_REG_PC, u.reg_read(base.UC_ARM_REG_LR))
            return
        if address == 0x36ae8:
            pointer = u.reg_read(base.UC_ARM_REG_R0)
            base._check(self.source_input_buffer is None and self.allocations.get(pointer) == 4096,
                'calc-input-info-original-allocation-unreviewed')
            self.source_input_buffer = pointer
        if address in (0x36a6c, 0x36b82):
            self.validation_events.append(dict(returnVa=address, rawReturn=u.reg_read(base.UC_ARM_REG_R0)))
        if address in self.pairs or address == 0x38aa4:
            a, b, c = (u.reg_read(r) for r in (base.UC_ARM_REG_R0, base.UC_ARM_REG_R1, base.UC_ARM_REG_R2))
            base._check(self.uint(0x10b770) == 0 and self.pending_pair is None,
                'calc-input-info-language-slot-or-pair-unreviewed')
            if address in self.pairs:
                row = self.pairs[address]; sp = u.reg_read(base.UC_ARM_REG_SP)
                base._check(a == row[6] and b == sp + row[7] and c == 200,
                    'calc-input-info-language-arguments-unreviewed')
                self.pending_pair = row
            else:
                base._check(c == 1024 and self.allocations.get(b) == 1024,
                    'calc-input-info-caption-arguments-unreviewed')
            result = self.language.lookup(a, c); data = bytes.fromhex(result['payloadHex'])
            self.write(u, None, b, len(data), None, None); u.mem_write(b, data)
            self.info_events.append(dict(kind='language', callVa=address, result=result))
            self._return(u, address, result); return
        if address in self.formats:
            row = self.formats[address]; sp = u.reg_read(base.UC_ARM_REG_SP)
            a, b, c, d = (u.reg_read(r) for r in (base.UC_ARM_REG_R0, base.UC_ARM_REG_R1, base.UC_ARM_REG_R2, base.UC_ARM_REG_R3))
            base._check(self.uint(0x10ba5c) == 0 and self.pending_pair == row
                and a == sp + row[8] and b == sp + row[7], 'calc-input-info-format-pair-unreviewed')
            expected = bytes.fromhex(self.language.prompts[row[6]]['payloadHex'])[:-1]
            base._check(self.cstr(b) == expected, 'calc-input-info-prompt-changed')
            result = self.pair.format_pair(expected, struct.pack('<II', c, d), u.reg_read(base.UC_ARM_REG_FPSCR))
            data = bytes.fromhex(result['outputBytesHex']) + b'\0'
            base._check(len(data) <= row[9], 'calc-input-info-format-destination-unqualified')
            self.write(u, None, a, len(data), None, None); u.mem_write(a, data)
            self.info_events.append(dict(kind='format', callVa=address, words=[c, d], result=result))
            self.pending_pair = None; self._return(u, address, result); return
        if address == 0x36b50:
            a, b, c, d = (u.reg_read(r) for r in (base.UC_ARM_REG_R0, base.UC_ARM_REG_R1, base.UC_ARM_REG_R2, base.UC_ARM_REG_R3))
            base._check(self.uint(0x10b82c) == 0 and c == 0 and self.uint(u.reg_read(base.UC_ARM_REG_SP)) == 4096
                and d == self.source_input_buffer and self.allocations.get(d) == 4096
                and self.response_index < len(self.replies), 'calc-input-info-ui-call-or-reply-budget-unreviewed')
            reply = self.replies[self.response_index]; self.response_index += 1
            result = self.string.input_bytes(self.cstr(a), self.cstr(b), reply['data'], accepted=reply['accepted'])
            output = bytes.fromhex(result['outputBytesHex'])
            self.write(u, None, d, len(output), None, None)
            if output:
                u.mem_write(d, output)
            r = self.string.r; child = self.string.u
            result = dict(result, returnedRegisters=[child.reg_read(reg) for reg in
                (r.UC_ARM_REG_R0, r.UC_ARM_REG_R1, r.UC_ARM_REG_R2, r.UC_ARM_REG_R3)],
                returnedApsr=child.reg_read(r.UC_ARM_REG_APSR), returnedFpscr=child.reg_read(r.UC_ARM_REG_FPSCR))
            self.ui_events.append(dict(callVa=address, replyIndex=self.response_index - 1, result=result))
            self._return(u, address, result); return
        if any(a <= address < b for a, b in self.info_regions):
            self.last.append(address); self.trace['originalInstructions'] += 1
            base._check(time.monotonic() < self.deadline, 'calc-input-info-wall-budget-exhausted')
            return
        return super().code(u, address, size, userdata)

    def input_info(self, formula, replies, title=b'field', flag=0):
        validate_info(formula, replies, title, flag)
        base._check(not self.closed and not self.poisoned, 'calc-input-info-prior-outcome-unknown-or-closed')
        self.replies = replies; self.trace.clear(); self.last.clear()
        self.next_alloc = base.HEAP; self.allocations.clear()
        objects = (base.DATA + 0x20000, base.DATA + 0x20010, base.DATA + 0x20020)
        for obj, data in zip(objects, (title, b'', formula.encode())):
            self.put_string(obj, 0, data)
        u = self.u; sp = base.STACK + 0x80000
        saved_regs = (base.UC_ARM_REG_R4, base.UC_ARM_REG_R5, base.UC_ARM_REG_R6, base.UC_ARM_REG_R7,
            base.UC_ARM_REG_R8, base.UC_ARM_REG_R9, base.UC_ARM_REG_R10, base.UC_ARM_REG_R11)
        saved = [u.reg_read(reg) for reg in saved_regs]
        for reg, value in ((base.UC_ARM_REG_R0, objects[0]), (base.UC_ARM_REG_R1, objects[1]),
                (base.UC_ARM_REG_R2, objects[2]), (base.UC_ARM_REG_R3, flag),
                (base.UC_ARM_REG_SP, sp), (base.UC_ARM_REG_LR, base.STOP | 1)):
            u.reg_write(reg, value)
        self.deadline = time.monotonic() + 15
        try:
            u.emu_start(0x36901, base.STOP, count=3_000_000)
            base._check(u.reg_read(base.UC_ARM_REG_PC) == base.STOP and u.reg_read(base.UC_ARM_REG_SP) == sp
                and [u.reg_read(reg) for reg in saved_regs] == saved and self.pending_pair is None
                and self.response_index == len(replies), dict(reason='calc-input-info-return-or-lifetime-unreviewed',
                    pc=u.reg_read(base.UC_ARM_REG_PC), sp=u.reg_read(base.UC_ARM_REG_SP), expectedSp=sp,
                    savedRegisters=saved, actualRegisters=[u.reg_read(reg) for reg in saved_regs],
                    repliesConsumed=self.response_index, repliesProvided=len(replies), pendingPair=self.pending_pair))
            value = u.reg_read(base.UC_ARM_REG_R0)
            if value >> 31:
                value -= 1 << 32
            output = self.cstr(self.uint(objects[1]))
            for obj in objects:
                u.reg_write(base.UC_ARM_REG_R0, self.uint(obj) - 12)
                u.reg_write(base.UC_ARM_REG_R1, base.DATA + 0x20300)
                u.reg_write(base.UC_ARM_REG_LR, base.STOP | 1)
                u.emu_start(0xc6309, base.STOP, count=300_000)
                base._check(u.reg_read(base.UC_ARM_REG_PC) == base.STOP and u.reg_read(base.UC_ARM_REG_SP) == sp,
                    'calc-input-info-caller-dispose-unreviewed')
            base._check(self.allocations == {self.source_input_buffer:4096},
                'calc-input-info-unexpected-remaining-allocation')
            return dict(returnValue=value, outputBytesHex=output.hex(), originalFunctionEntries=self.entries.copy(),
                validationEvents=self.validation_events.copy(), callbackEvents=self.info_events.copy(),
                uiEvents=self.ui_events.copy(), repliesConsumed=self.response_index,
                callerOwnedStringsDisposed=True, sourceInputBufferRemainingBytes=4096,
                sourceCompleteCleanupQualified=False, reuseAllowed=False,
                trace=dict(self.trace), numericConversions=self.numeric_events.copy(), **GUARDS)
        finally:
            # The original raw input allocation remains; never reuse this engine.
            self.poisoned = True

    def close(self):
        if self.closed:
            return
        errors = []
        for key in ('pair', 'string', 'integer', 'language', 'sprintf'):
            if hasattr(self, key):
                try:
                    getattr(self, key).close()
                except Exception as error:
                    errors.append(error)
        try:
            cpu.CpuSprintfReference.close(self)
        except Exception as error:
            errors.append(error)
        if errors:
            raise ValueError('calc-input-info-engine-cleanup-failed') from errors[0]


def audit_matrix(calc, std, deviceid, language_blob, census, output):
    definitions = validate_census(census)
    output.mkdir(); rows = []
    base._check(len(definitions) == 6 and len({r['formula'] for r in definitions}) == 6,
        'calc-input-info-six-source-definitions-required')
    for definition in definitions:
        formula = definition['formula']; parsed = parse_formula_text(formula)
        target = b'4142434445464748' if parsed['baseDataType'] == 'A_BYTEFIELD' else b'ABCDEFGH'
        if parsed['baseDataType'] == 'A_BYTEFIELD' and parsed['minLength'] == 0:
            extra = ('odd-length-accepted-source', [dict(data=b'A', accepted=True)])
        else:
            extra = ('retry', [dict(data=b'A' if parsed['baseDataType'] == 'A_BYTEFIELD'
                else b'Z' * (parsed['maxLength'] + 1), accepted=True), dict(data=target, accepted=True)])
        for name, replies in (('valid', [dict(data=target, accepted=True)]), extra,
                ('cancel', [dict(data=b'', accepted=False)])):
            p = CpuCalcInputInfoReference(calc, std, deviceid, language_blob)
            try:
                result = p.input_info(formula, replies)
                expected = b'' if name == 'cancel' else b'A' if name == 'odd-length-accepted-source' else target
                base._check(result['returnValue'] == (-1 if name == 'cancel' else len(expected))
                    and bytes.fromhex(result['outputBytesHex']) == expected,
                    'calc-input-info-result-comparison-failed')
            finally:
                p.close()
            base._check(p.closed and all(getattr(p, key).closed for key in ('pair', 'string', 'integer', 'language', 'sprintf')),
                'calc-input-info-six-engines-not-disposed')
            rows.append(dict(formula=formula, case=name, result=result, sixEnginesDisposed=True))
    report = dict(kind='x431-original-calc-minmax-input-info', schemaVersion=1, cases=rows,
        caseCount=len(rows), definitionCount=6, censusDeclaredSourceSha256=census['sourceSha256'],
        sourceDefinitionsRehashedHere=False, censusCanonicalHashVerified=True,
        censusCanonicalSha256=CENSUS_CANONICAL_SHA256, originalInverseCallerExecuted=False,
        completeOriginalEditingChainQualified=False, codeSha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(), **GUARDS)
    with (output / 'result.json').open('x', encoding='utf8', newline='\n') as out:
        out.write(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    return report


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    for key in ('calc', 'std', 'deviceid', 'language', 'census', 'output-dir'):
        p.add_argument('--' + key, type=Path, required=True)
    a = p.parse_args(argv)
    r = audit_matrix(a.calc.read_bytes(), a.std.read_bytes(), a.deviceid.read_bytes(), a.language.read_bytes(),
        json.loads(a.census.read_text(encoding='utf8')), a.output_dir)
    print(json.dumps(dict(cases=r['caseCount'], definitionCount=r['definitionCount'], **GUARDS)))


if __name__ == '__main__':
    main()
