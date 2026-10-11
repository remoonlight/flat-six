"""Retained BCD input helper and catalog; isolated original CPU instructions only.

Never assumes generic decimal-to-BCD behavior, legal vehicle input or a complete
coding block. Source helpers and explicit formatting models are kept separate
from actual Android UI/binding and all head/vehicle execution.
"""
from pathlib import Path
import argparse
import collections
import hashlib
import json
import struct
import time
from . import x431_cpu_reference as base
from . import x431_sprintf_reference as cpu
from .arm_write_monitor import attach_arm_write_monitor
from .x431_input_reference import CpuInputReference
from .x431_integer_sprintf_reference import CpuIntegerSprintfReference, validate_integer_format
from .x431_negate_caller_reference import audit_sources as audit_parent_sources, CpuNegateCallerReference
from .x431_formula import formula_from_record, parse_formula_text
from .coding_input_source import _pic_address
from .x431_callback_source import _slot
from .offline_match import ANCHORS

FORMULAS = frozenset((
    '2562fb2aaff776d8be062688b33e6f346651d401d427a0133ca8efb2119c9e31',
    'ad04c94f394f70eae8e4809fed727d6de4776d60c2699c8e0f98607e86b6051f',
    '83597e97131d0759bc8780f8969ba99c2e3598c21a2da107c66168b631478181'))
OBJECTS = (base.DATA + 0x20000, base.DATA + 0x20010)
GUARDS = dict(cpuEmulationOnly=True, originalBcdHelperExecuted=True,
    isolatedOriginalIntegerFormatting=True, formatterCallbackSubstituted=True,
    actualAndroidLibcSprintfExecuted=False, genericBcdEncodingImplemented=False,
    actualAndroidUiExecuted=False, actualDynamicBindingQualified=False,
    inputCharacterEncodingQualified=False, legalInputDomainQualified=False,
    completeCodingBlockQualified=False, sourceDifferencesAreProductRules=False,
    vehicleVerified=False, codingWriteQualified=False, codingRestoreQualified=False,
    executionEnabled=False, writePayload=None)


def collect_definitions(variants):
    path = Path(variants)
    with path.open('rb') as source:
        base._check(hashlib.file_digest(source, 'sha256').hexdigest() == ANCHORS['variants'],
            'bcd-catalog-source-unreviewed')
    profiles = records = bcd_records = 0; found = {}
    with path.open(encoding='utf8') as source:
        for line in source:
            profile = json.loads(line); profiles += 1
            for record in profile.get('pool_records', {}).get('coding', {}).get('records', []):
                records += 1
                formula, parsed = formula_from_record(record)
                if parsed.get('encoding') != 'BCD-P':
                    continue
                digest = hashlib.sha256(formula.encode()).hexdigest()
                base._check(digest in FORMULAS, 'bcd-catalog-formula-unreviewed')
                row = found.setdefault(digest, dict(formula=formula, parsed=parse_formula_text(formula),
                    records=0, sourceOffsets=set(), profileIds=set(), formulaIds=set()))
                row['records'] += 1; bcd_records += 1
                row['sourceOffsets'].add(record['at']); row['profileIds'].add(profile['profile_id'])
                row['formulaIds'].add(record['formula']['id_hex'])
    base._check(profiles == 291 and records == 59871 and bcd_records == 221 and set(found) == FORMULAS,
        'bcd-catalog-complete-counts-different')
    for row in found.values():
        for name in ('sourceOffsets', 'profileIds', 'formulaIds'):
            row[name] = sorted(row[name])
    return dict(sourceSha256=ANCHORS['variants'], profileCount=profiles, codingRecords=records,
        bcdRecords=bcd_records, definitionCount=3,
        definitions=[dict(sha256=k, **r) for k, r in sorted(found.items())],
        catalogRecordsAreNotInstalledEcus=True, vehicleVerified=False)


def audit_sources(calc, std, deviceid, language, readwrite):
    parent = audit_parent_sources(calc, std, deviceid, language, readwrite)
    elf = base._Elf(calc); strings = []
    for load, add, expected in ((0x2464e, 0x24650, 'Encoding='), (0x2465a, 0x2465c, ','),
            (0x24698, 0x2469a, 'BCD-P'), (0x246c4, 0x246d0, '%02d')):
        address = _pic_address(elf, load, add)
        base._check(elf.cstring(address) == expected, 'bcd-helper-string-unreviewed')
        strings.append(dict(loadVa=load, addVa=add, stringVa=address, value=expected))
    for address, expected in ((0x246aa, '0228'), (0x246c6, '00eb0510'),
            (0x246d4, 'a0f54c72'), (0x246da, 'f9f798ec')):
        base._check(elf.raw(address, len(expected) // 2).hex() == expected, 'bcd-helper-instruction-unreviewed')
    slots = dict(language=_slot(elf, 0x33832, 0x33834, 'GetTextFromTextLib', 0x10b770),
        formatter=_slot(elf, 0x33848, 0x3384c, 'SPRINTF', 0x10ba5c))
    base._check(all(elf.raw(a, 2).hex() == '9847' for a in (0x33846, 0x33856)),
        'bcd-caller-prompt-instruction-unreviewed')
    return dict(parent=parent, helperStrings=strings, helperEntryVa=0x24635,
        formatterCallVa=0x246da, formatterReturnVa=0x246de,
        callerPrompt=dict(languageCallVa=0x33846, formatterCallVa=0x33856,
            idHex='00210365', languageStackOffset=0xb8, formatterStackOffset=0x50,
            destinationBytes=50, slots=slots),
        twoStorageBytesRequired=True, originalReturnIsNotUiAcceptance=True)


def validate_helper(formula, data, negative_control=False):
    base._check(type(negative_control) is bool, 'bcd-helper-control-invalid')
    base._check(type(formula) is str and formula.isascii() and '\0' not in formula,
        'bcd-helper-formula-invalid')
    retained = formula.replace('Encoding=Unsigned,', 'Encoding=BCD-P,') if negative_control else formula
    base._check(hashlib.sha256(retained.encode()).hexdigest() in FORMULAS
        and (not negative_control or formula != retained), 'bcd-helper-exact-formula-required')
    base._check(type(data) is bytes and len(data) <= 64 and b'\0' not in data,
        'bcd-helper-bounded-storage-bytes-required')


class CpuBcdIntegerReference(CpuIntegerSprintfReference):
    def validate_format(self, fmt, argument_bytes, fpscr=0):
        if fmt != b'%02d':
            return validate_integer_format(fmt, argument_bytes, fpscr)
        base._check(type(argument_bytes) is bytes and len(argument_bytes) == 4,
            'bcd-format-word-required')
        value = struct.unpack('<i', argument_bytes)[0]
        base._check(-799 <= value <= 3519 and type(fpscr) is int and 0 <= fpscr <= 0xffffffff
            and not fpscr & 0x00c00000, 'bcd-format-domain-unreviewed')
        return value & 0xffffffff


class CpuBcdHelperReference(CpuInputReference):
    def __init__(self, calc, std, deviceid, language, readwrite):
        self.bcd_source = audit_sources(calc, std, deviceid, language, readwrite)
        self.write_hook = None; self.poisoned = False; self.formats = []
        super().__init__(calc)
        try:
            self.integer = CpuBcdIntegerReference(calc, std, deviceid)
            self.write_hook = attach_arm_write_monitor(self.u, self.write)
        except Exception:
            self.close(); raise

    def write(self, u, access, address, size, value, userdata):
        base._check(type(size) is int and 0 <= size <= 65536 and (
            base.STACK <= address and address + size <= base.STACK + 0x100000
            or OBJECTS[0] <= address and address + size <= OBJECTS[1] + 4
            or any(a <= address and address + size <= a + n for a, n in self.allocations.items())),
            'bcd-helper-write-range-unreviewed')

    def code(self, u, address, size, userdata):
        name = self.hooks.get(address)
        if name == 'sprintf':
            a, b, c = (u.reg_read(reg) for reg in (base.UC_ARM_REG_R0, base.UC_ARM_REG_R1, base.UC_ARM_REG_R2))
            lr = u.reg_read(base.UC_ARM_REG_LR); sp = u.reg_read(base.UC_ARM_REG_SP)
            base._check(lr == 0x246df and a == sp + 0x24 and b == 0xf5f42 and not self.formats,
                'bcd-helper-format-call-unreviewed')
            result = self.integer.format_integer(b'%02d', struct.pack('<I', c), u.reg_read(base.UC_ARM_REG_FPSCR))
            raw = bytes.fromhex(result['outputBytesHex']) + b'\0'
            base._check(len(raw) <= 5, 'bcd-helper-stack-format-capacity-unreviewed')
            self.write(u, None, a, len(raw), None, None); u.mem_write(a, raw)
            self.formats.append(dict(argumentWord=c, result=result))
            for reg, value in zip((base.UC_ARM_REG_R0, base.UC_ARM_REG_R1, base.UC_ARM_REG_R2, base.UC_ARM_REG_R3), result['returnedRegisters']):
                u.reg_write(reg, value)
            u.reg_write(base.UC_ARM_REG_APSR, result['returnedApsr'])
            u.reg_write(base.UC_ARM_REG_FPSCR, result['returnedFpscr']); u.reg_write(base.UC_ARM_REG_PC, lr)
            return
        if name in ('memcpy', 'memmove', '__aeabi_memcpy', '__aeabi_memcpy4', '__aeabi_memcpy8', 'memset'):
            self.write(u, None, u.reg_read(base.UC_ARM_REG_R0), u.reg_read(base.UC_ARM_REG_R2), None, None)
        if name in ('__aeabi_memclr', '__aeabi_memclr4', '__aeabi_memclr8'):
            self.write(u, None, u.reg_read(base.UC_ARM_REG_R0), u.reg_read(base.UC_ARM_REG_R1), None, None)
        if 0x24634 <= address < 0x24748:
            self.trace['originalInstructions'] += 1
            base._check(time.monotonic() < self.deadline, 'bcd-helper-wall-budget-exhausted')
            return
        return super().code(u, address, size, userdata)

    def transform(self, formula, data, negative_control=False):
        validate_helper(formula, data, negative_control)
        base._check(not self.closed and not self.poisoned, 'bcd-helper-prior-outcome-unknown-or-closed')
        for obj, value in zip(OBJECTS, (formula.encode(), data)):
            self.put_string(obj, 0, value)
        u = self.u; sp = base.STACK + 0x80000
        for reg, value in ((base.UC_ARM_REG_R0, OBJECTS[0]), (base.UC_ARM_REG_R1, OBJECTS[1]),
                (base.UC_ARM_REG_SP, sp), (base.UC_ARM_REG_LR, base.STOP | 1)):
            u.reg_write(reg, value)
        self.deadline = time.monotonic() + 5
        try:
            u.emu_start(0x24635, base.STOP, count=300000)
            base._check(u.reg_read(base.UC_ARM_REG_PC) == base.STOP and u.reg_read(base.UC_ARM_REG_SP) == sp,
                'bcd-helper-not-returned-or-stack-not-restored')
            ret = u.reg_read(base.UC_ARM_REG_R0); pointer = self.uint(OBJECTS[1])
            output = bytes(self.u.mem_read(pointer, self.uint(pointer - 12)))
            # Original disposal of the two caller-owned string representations.
            for obj in OBJECTS:
                u.reg_write(base.UC_ARM_REG_R0, self.uint(obj) - 12)
                u.reg_write(base.UC_ARM_REG_R1, base.DATA + 0x20300)
                u.reg_write(base.UC_ARM_REG_LR, base.STOP | 1)
                u.emu_start(0xc6309, base.STOP, count=300000)
                base._check(u.reg_read(base.UC_ARM_REG_PC) == base.STOP and u.reg_read(base.UC_ARM_REG_SP) == sp,
                    'bcd-helper-disposal-not-returned')
            base._check(not self.allocations and ret == 1, 'bcd-helper-lifetime-or-return-unreviewed')
            return dict(returnValue=ret, inputBytesHex=data.hex().upper(), outputBytesHex=output.hex().upper(),
                formatCalls=self.formats.copy(), allocationsRemaining=0, negativeControl=negative_control,
                originalReturnIsNotUiAcceptance=True, **GUARDS)
        finally:
            self.poisoned = True

    def close(self):
        if self.closed:
            return
        errors = []
        if hasattr(self, 'integer'):
            try:
                self.integer.close()
            except Exception as error:
                errors.append(error)
        try:
            cpu.CpuSprintfReference.close(self)
        except Exception as error:
            errors.append(error)
        if errors:
            raise ValueError('bcd-helper-engine-cleanup-failed') from errors[0]


def validate_caller(formula, replies, original, byte=4, bit=0, title=b'field'):
    validate_helper(formula, b'')
    base._check(type(replies) is list and 1 <= len(replies) <= 3, 'bcd-caller-reply-budget-required')
    for reply in replies:
        base._check(type(reply) is dict and set(reply) == {'data', 'accepted'}
            and type(reply['data']) is bytes and len(reply['data']) <= 64 and b'\0' not in reply['data']
            and type(reply['accepted']) is bool, 'bcd-caller-reply-invalid')
    base._check(type(original) is bytes and 512 <= len(original) <= 4096
        and type(byte) is int and 2 <= byte <= len(original) - 512,
        'bcd-caller-source-window-unreviewed')
    base._check(type(bit) is int and bit == 0, 'bcd-caller-bit-offset-unreviewed')
    base._check(type(title) is bytes and len(title) <= 64 and b'\0' not in title
        and b'0x' not in title and b'0X' not in title, 'bcd-caller-title-unreviewed')
    return parse_formula_text(formula)


class CpuBcdCallerReference(CpuNegateCallerReference):
    """Exact retained BCD formulas, original validation/helper/inverse caller."""
    def __init__(self, calc, std, deviceid, language, readwrite):
        self.bcd_source = audit_sources(calc, std, deviceid, language, readwrite)
        self.bcd_formats = []; self.bcd_events = []; self.bcd_prompts = []; self.pending_bcd_prompt = None
        super().__init__(calc, std, deviceid, language, readwrite)
        try:
            self.integer.close()
            self.integer = CpuBcdIntegerReference(calc, std, deviceid)
            for row in self.info_audit['languageSource']['prompts']:
                self.language.prompts[int(row['idHex'], 16)] = row
        except Exception:
            self.close(); raise

    def validate_arguments(self, formula, replies, original, byte, bit, title):
        parsed = validate_caller(formula, replies, original, byte, bit, title)
        self.bcd_bits = parsed['bitLength']
        return parsed

    def code(self, u, address, size, userdata):
        if address in (0x33846, 0x33856):
            a, b, c = (u.reg_read(reg) for reg in (base.UC_ARM_REG_R0, base.UC_ARM_REG_R1, base.UC_ARM_REG_R2))
            sp = u.reg_read(base.UC_ARM_REG_SP)
            if address == 0x33846:
                base._check(self.uint(0x10b770) == 0 and a == 0x00210365 and b == sp + 0xb8
                    and c == 200 and self.pending_bcd_prompt is None, 'bcd-caller-prompt-arguments-unreviewed')
                result = dict(self.language.lookup(a, c), isolatedMinMaxPromptReference=False,
                    isolatedBcdPromptReference=True)
                data = bytes.fromhex(result['payloadHex'])
                self.pending_bcd_prompt = data[:-1]
            else:
                base._check(self.uint(0x10ba5c) == 0 and a == sp + 0x50 and b == sp + 0xb8
                    and self.pending_bcd_prompt is not None and self.cstr(b) == self.pending_bcd_prompt
                    and c == (1 << self.bcd_bits) - 1, 'bcd-caller-prompt-format-unreviewed')
                result = self.integer.format_integer(self.pending_bcd_prompt, struct.pack('<I', c))
                data = bytes.fromhex(result['outputBytesHex']) + b'\0'
                base._check(len(data) <= 50, 'bcd-caller-prompt-capacity-unreviewed')
                self.pending_bcd_prompt = None
            self.write(u, None, b if address == 0x33846 else a, len(data), None, None)
            u.mem_write(b if address == 0x33846 else a, data)
            self.bcd_prompts.append(dict(callVa=address, argumentWord=c, result=result))
            self._return(u, address, result); return
        if address in (0x24810, 0x24814):
            sp = u.reg_read(base.UC_ARM_REG_SP)
            self.bcd_events.append(dict(va=address,
                inputBytesHex=self.cstr(self.uint(sp + 0xac)).hex().upper()))
        if self.hooks.get(address) == 'sprintf' and u.reg_read(base.UC_ARM_REG_LR) == 0x246df:
            a, b, c = (u.reg_read(reg) for reg in (base.UC_ARM_REG_R0, base.UC_ARM_REG_R1, base.UC_ARM_REG_R2))
            sp = u.reg_read(base.UC_ARM_REG_SP)
            base._check(a == sp + 0x24 and b == 0xf5f42 and not self.bcd_formats,
                'bcd-caller-format-call-unreviewed')
            result = self.integer.format_integer(b'%02d', struct.pack('<I', c), u.reg_read(base.UC_ARM_REG_FPSCR))
            raw = bytes.fromhex(result['outputBytesHex']) + b'\0'
            base._check(len(raw) <= 5, 'bcd-caller-stack-format-capacity-unreviewed')
            self.write(u, None, a, len(raw), None, None); u.mem_write(a, raw)
            self.bcd_formats.append(dict(argumentWord=c, result=result))
            for reg, value in zip((base.UC_ARM_REG_R0, base.UC_ARM_REG_R1, base.UC_ARM_REG_R2, base.UC_ARM_REG_R3), result['returnedRegisters']):
                u.reg_write(reg, value)
            u.reg_write(base.UC_ARM_REG_APSR, result['returnedApsr'])
            u.reg_write(base.UC_ARM_REG_FPSCR, result['returnedFpscr']); u.reg_write(base.UC_ARM_REG_PC, 0x246df)
            return
        return super().code(u, address, size, userdata)

    def call(self, formula, replies, original, byte=4, bit=0, title=b'field'):
        result = super().call(formula, replies, original, byte, bit, title)
        base._check(self.pending_bcd_prompt is None, 'bcd-caller-prompt-not-completed')
        return dict(result, **{**GUARDS, 'originalMinMaxCallerChainExecuted':False,
            'originalBcdCallerChainExecuted':True, 'bcdEvents':self.bcd_events.copy(),
            'originalBcdHelperExecuted':bool(self.bcd_events),
            'returnValueIsNotVehicleSuccess':True,
            'bcdFormats':self.bcd_formats.copy(), 'bcdPrompts':self.bcd_prompts.copy()})


def helper_cases():
    for value in range(100):
        yield f'two-decimal-{value:02d}', f'{value:02d}'.encode(), False
    for name, data in (('empty', b''), ('one-zero', b'0'), ('one-nine', b'9'),
            ('three-digits', b'100'), ('four-digits', b'1234'), ('maximum-storage', b'1' * 64),
            ('unsigned-minimum-pair', bytes((1, 1))), ('unsigned-maximum-pair', bytes((255, 255))),
            ('negative-sign', b'-1'), ('positive-sign', b'+1'), ('leading-space', b' 1'),
            ('trailing-space', b'1 '), ('alphabetic-first', b'A0'), ('alphabetic-second', b'0A'),
            ('two-utf8-storage-bytes', b'\xc3\xa9')):
        yield name, data, False
    for data in (b'12', b'100'):
        yield 'synthetic-unsigned-control-' + data.decode(), data, True


def caller_cases(definition):
    bits = definition['parsed']['bitLength']
    for value in range(100):
        data = f'{value:02d}'.encode()
        yield f'two-decimal-{value:02d}', [dict(data=data, accepted=True)]
    for data in ((b'0', b'1', b'100', b'255', b'256', b'1234', b'65535') if bits == 16
            else (b'0', b'1', b'100', b'255')):
        yield 'numeric-storage-' + data.decode(), [dict(data=data, accepted=True)]
    yield 'cancel-empty', [dict(data=b'', accepted=False)]
    yield 'cancel-nonempty', [dict(data=b'12', accepted=False)]
    yield 'overflow-retry', [dict(data=str(1 << bits).encode(), accepted=True), dict(data=b'12', accepted=True)]


def audit_matrix(calc, std, deviceid, language, readwrite, variants, output):
    sources = audit_sources(calc, std, deviceid, language, readwrite)
    catalog = collect_definitions(variants)
    output = Path(output); output.mkdir()
    (output / 'catalog.json').write_text(json.dumps(catalog, ensure_ascii=False, indent=2) + '\n',
        encoding='utf8', newline='\n')
    counts = collections.Counter(); returns = collections.Counter()
    with (output / 'helper-cases.jsonl').open('x', encoding='utf8', newline='\n') as out:
        for definition in catalog['definitions']:
            for name, data, control in helper_cases():
                formula = definition['formula'].replace('Encoding=BCD-P,', 'Encoding=Unsigned,') if control else definition['formula']
                probe = CpuBcdHelperReference(calc, std, deviceid, language, readwrite)
                try:
                    result = probe.transform(formula, data, control)
                    expected = (f'{data[1] + (data[0] << 4) - 0x330:02d}'.encode()
                        if len(data) == 2 and not control else data)
                    base._check(result['outputBytesHex'] == expected.hex().upper()
                        and len(result['formatCalls']) == int(len(data) == 2 and not control),
                        'bcd-helper-independent-comparison-different')
                finally:
                    probe.close()
                base._check(probe.closed and probe.integer.closed, 'bcd-helper-engines-not-disposed')
                counts['helperCases'] += 1; counts['helperEnginesDisposed'] += 2
                counts['helperSyntheticControls'] += int(control)
                out.write(json.dumps(dict(formulaSha256=definition['sha256'], name=name,
                    comparisonBytesHex=expected.hex().upper(), allEnginesDisposed=True, result=result),
                    separators=(',', ':')) + '\n')
    with (output / 'caller-cases.jsonl').open('x', encoding='utf8', newline='\n') as out:
        for definition in catalog['definitions']:
            width = definition['parsed']['bitLength'] // 8
            for index, (name, replies) in enumerate(caller_cases(definition)):
                original = (b'\xa5' * 4096 if index % 2 else bytes(range(256)) * 16)
                byte = (2, 4, 3584)[index % 3]
                probe = CpuBcdCallerReference(calc, std, deviceid, language, readwrite)
                try:
                    result = probe.call(definition['formula'], replies, original, byte)
                    expected = bytearray(original)
                    if replies[-1]['accepted']:
                        data = replies[-1]['data']
                        value = ((data[0] - 0x30) * 16 + data[1] - 0x30 if len(data) == 2 else int(data))
                        expected[byte:byte + width] = value.to_bytes(width, 'big')
                    base._check(result['completed'] and result['afterHex'] == expected.hex().upper(),
                        'bcd-caller-independent-buffer-comparison-different')
                    # The original caller invokes the helper even after UI cancellation;
                    # its return 1 must never be interpreted as input acceptance.
                    base._check(len(result['bcdEvents']) == 2
                        and result['repliesConsumed'] == len(replies), 'bcd-caller-control-flow-different')
                finally:
                    probe.close()
                base._check(probe.closed and all(getattr(probe, k).closed
                    for k in ('hex', 'pair', 'string', 'integer', 'language', 'sprintf')),
                    'bcd-caller-engines-not-disposed')
                counts['callerCases'] += 1
                # Nine reserve engines, seven active engines, replaced language and integer engines.
                counts['callerEnginesDisposed'] += 18
                counts['callerCanceled'] += int(not replies[-1]['accepted'])
                counts['callerRetried'] += int(len(replies) > 1)
                returns[f'{result["returnValue"] & 0xffffffff:08X}'] += 1
                out.write(json.dumps(dict(formulaSha256=definition['sha256'], name=name, byteOffset=byte,
                    originalBytesHex=original.hex().upper(), replies=[dict(dataHex=r['data'].hex().upper(),
                        accepted=r['accepted']) for r in replies], allEnginesDisposed=True,
                    independentBufferComparisonDifferent=False, result=result), separators=(',', ':')) + '\n')
    # Record every loaded project dependency, without importing an engine here.
    import sys
    directory = Path(__file__).resolve().parents[1]
    files = sorted({Path(m.__file__).resolve() for m in tuple(sys.modules.values())
        if getattr(m, '__file__', None) and Path(m.__file__).resolve().is_relative_to(directory)})
    report = dict(kind='x431-original-bcd-input-and-caller-reference', schemaVersion=1,
        sourceAudit=sources, catalogSha256=hashlib.sha256((output / 'catalog.json').read_bytes()).hexdigest(),
        counts=dict(counts), callerReturnValues=dict(sorted(returns.items())),
        helperCasesSha256=hashlib.sha256((output / 'helper-cases.jsonl').read_bytes()).hexdigest(),
        callerCasesSha256=hashlib.sha256((output / 'caller-cases.jsonl').read_bytes()).hexdigest(),
        code=[dict(path=f.relative_to(directory).as_posix(), sha256=hashlib.sha256(f.read_bytes()).hexdigest()) for f in files],
        noGenericThreeOrMoreDigitBcdConversion=True, returnValueIsNotVehicleSuccess=True,
        originalBcdCallerChainExecuted=True, **GUARDS)
    (output / 'result.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n',
        encoding='utf8', newline='\n')
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for key in ('calc', 'std', 'deviceid', 'language', 'readwrite', 'variants', 'output-dir'):
        parser.add_argument('--' + key, type=Path, required=True)
    a = parser.parse_args()
    report = audit_matrix(*(getattr(a, key).read_bytes() for key in ('calc', 'std', 'deviceid', 'language', 'readwrite')),
        a.variants, a.output_dir)
    print(json.dumps(dict(counts=report['counts'], callerReturnValues=report['callerReturnValues'],
        vehicleVerified=False, executionEnabled=False)))


if __name__ == '__main__':
    main()
