"""Original MinMax prompts, two-word formatting and input-caption lookup.

Only five exact retained records, two exact formats and capacities 200/1024.
STD lookup/decryption and DEVICEID formatting execute in isolated engines;
language file context, memory operations and virtual stdio remain models.
"""
from pathlib import Path
import argparse
import hashlib
import json
import struct
import time

from . import x431_language_reference as language
from . import x431_integer_sprintf_reference as integer
from . import x431_sprintf_reference as cpu
from .x431_input_info_source import audit_input_info_sources
from .x431_language_source import audit_language_sources, _lookup, _plain_prompt_contract
from .x431_callback_source import _slot
from .x431_native_binding import _Elf

PROMPTS = (
    (0xff30001c, 1579, '77eb5acdbeedcba9001ae61a44b8241b3c1eaaff56f504247b3905a7b010d149'),
    (0xff30003e, 1546, 'dcb5d97405c0c510e44acc7a2394b9ab96c908ff9a9f14a5810c0cc3eec71c86'),
    (0xff60055c, 482, '9c2a6b15ffda4fb5ef148fc3d5a14c30245394b9956c0412315a9dcf07619b5e'),
    (0x003002fd, 188331, '84fd897c99133c0ff627af5e6bba8a0265eb99bbd1a41919733ac9d1506a6309'),
    (0xff80003d, 2409, 'f33d87090d9a95f08b8d5d306ebb3755fd48607814e5027601b16dd7a6d9b48f'),
)
REGIONS = (
    ('_Z11GetCalcInfoSsRSsi', 0x32e4c, 3192,
     'abe403df15a55744f266d48c5ad3990ce1461f802ec41e5a16603825cfd897a8'),
    ('_Z9GetIDTextRSs', 0x38a4c, 132,
     '5506e7858e7441c6fe713a3b37c6041b44538a13bc0d7e88ec6f02aea56572bf'),
)
PAIRS = (
    (0x3315a, 0x33146, 0x3314a, 0x3316e, 0x3315c, 0x33164, 0xff30001c, 0xb8, 0x50, 100),
    (0x331ac, 0x33196, 0x3319a, 0x331c4, 0x331ae, 0x331b6, 0xff30003e, 0x50, 0xb8, 100),
    (0x343d2, 0x343be, 0x343c2, 0x343e6, 0x343d4, 0x343d8, 0xff30001c, 0x108, 0xa0, 50),
    (0x344ee, 0x344d8, 0x344dc, 0x34504, 0x344f0, 0x344f6, 0xff30003e, 0xa0, 0x108, 100),
)
GUARDS = dict(cpuEmulationOnly=True, isolatedMinMaxPromptReference=True,
    hostElfLoaded=False, hostStdioCalled=False, virtualStdioCapture=True,
    actualDynamicBindingQualified=False, runtimeCallbacksBound=False,
    actualLanguageFileStateQualified=False, inputCharacterEncodingQualified=False,
    androidInputWorkflowVerified=False, actualInputInfoReturnQualified=False,
    legalInputDomainQualified=False, vehicleVerified=False, codingWriteQualified=False,
    codingRestoreQualified=False, executionEnabled=False, writePayload=None)


def audit_minmax_sources(calc, std, language_blob):
    source = audit_input_info_sources(calc, std)
    text = audit_language_sources(calc, std, language_blob)
    elf = _Elf(calc); regions = []
    for name, start, size, sha in REGIONS:
        symbols = [s for s in elf.symbols if s['name'] == name and s['type'] == 2 and s['section']]
        cpu._check(len(symbols) == 1 and symbols[0]['va'] == start | 1 and symbols[0]['bytes'] == size
            and hashlib.sha256(elf.raw(start, size)).hexdigest() == sha, 'minmax-prompt-function-unreviewed')
        regions.append(dict(symbol=name, startVa=start, bytes=size, sha256=sha))
    section = text['textSection']
    state = language.parse_text_gag(language_blob[section['offset']:section['offset'] + section['bytes']])
    prompts = []
    for id_u32, index, sha in PROMPTS:
        actual_index, key, body = _lookup(state, id_u32)
        cpu._check(actual_index == index and hashlib.sha256(body).hexdigest() == sha,
            'minmax-prompt-record-unreviewed')
        contract = _plain_prompt_contract(body)
        prompts.append(dict(idHex=f'{id_u32:08X}', recordIndex=index, storageKeyHex=key.hex(),
            payloadHex=body.hex(), sha256=sha, contract=contract))
    pairs = []
    for a, al, aa, b, bl, ba, id_u32, lang_offset, fmt_offset, extent in PAIRS:
        for site in (a, b):
            instruction = struct.unpack('<H', elf.raw(site, 2))[0]
            cpu._check(instruction & 0xff87 == 0x4780, 'minmax-prompt-call-instruction-unreviewed')
        pairs.append(dict(languageCallVa=a, sprintfCallVa=b, idHex=f'{id_u32:08X}',
            languageStackOffset=lang_offset, formatStackOffset=fmt_offset, destinationBytes=extent,
            languageSlot=_slot(elf, al, aa, 'GetTextFromTextLib', 0x10b770),
            sprintfSlot=_slot(elf, bl, ba, 'SPRINTF', 0x10ba5c)))
    caption = _slot(elf, 0x38a96, 0x38a9c, 'GetTextFromTextLib', 0x10b770)
    cpu._check(elf.raw(0x38aa4, 2).hex() == '9847', 'minmax-prompt-caption-call-unreviewed')
    return dict(kind='x431-minmax-prompt-source', schemaVersion=1, inputInfoSource=source,
        languageSource=text, additionalFunctionRegions=regions, prompts=prompts,
        callbackPairs=pairs, captionCallback=dict(callVa=0x38aa4, capacity=1024, **caption),
        originalInstructionsExecuted=False, **{k:v for k,v in GUARDS.items() if k != 'cpuEmulationOnly'})


class CpuMinMaxLanguageReference(language.CpuLanguageReference):
    def __init__(self, calc, std, language_blob):
        audit = audit_minmax_sources(calc, std, language_blob)
        super().__init__(calc, std, language_blob)
        self.minmax_audit = audit
        self.prompts = {int(r['idHex'], 16):r for r in audit['prompts']}
        self.capacity = 200

    def _writable(self, address, size):
        return type(size) is int and 0 <= size <= 1024 and (
            cpu.STACK <= address and address + size <= cpu.STACK + 0x100000
            or cpu.DATA <= address and address + size <= cpu.DATA + self.capacity
            or any(a <= address and address + size <= a + n for a, n in self.allocations.items()))

    def lookup(self, id_u32, capacity=200):
        cpu._check(type(id_u32) is int and id_u32 in self.prompts
            and type(capacity) is int and capacity in (200, 1024),
            'minmax-language-id-or-capacity-unreviewed')
        cpu._check(not self.closed and not self.poisoned and not self.opened and not self.allocations,
            'minmax-language-prior-outcome-unknown-or-closed')
        self.capacity = capacity
        u, r = self.u, self.r; sp = cpu.STACK + 0x80000
        before = bytes([0xa5]) * (capacity + 32)
        context = bytearray(self.state['ctx'])
        struct.pack_into('<IIf', context, 0, language.FILE_TOKEN, self.state['index_off'], self.state['ver'])
        struct.pack_into('<IIII', context, 0x110, self.state['nsec'], language.SEC,
            self.section['offset'], self.section['bytes'])
        u.mem_write(language.CTX, bytes(context)); u.mem_write(language.SEC, self.state['sec'])
        u.mem_write(cpu.DATA, before)
        self.cursor = self.section['offset']; self.next_alloc = language.HEAP
        self.trace.clear(); self.reads.clear(); self.seen.clear()
        saved_regs = (r.UC_ARM_REG_R4, r.UC_ARM_REG_R5, r.UC_ARM_REG_R6, r.UC_ARM_REG_R7,
            r.UC_ARM_REG_R8, r.UC_ARM_REG_R9, r.UC_ARM_REG_R10, r.UC_ARM_REG_R11)
        saved = [u.reg_read(reg) for reg in saved_regs]
        for reg, value in ((r.UC_ARM_REG_R0, id_u32), (r.UC_ARM_REG_R1, cpu.DATA),
                (r.UC_ARM_REG_R2, capacity), (r.UC_ARM_REG_R3, 0), (r.UC_ARM_REG_SP, sp),
                (r.UC_ARM_REG_LR, cpu.STOP | 1), (r.UC_ARM_REG_FPSCR, 0)):
            u.reg_write(reg, value)
        self.deadline = time.monotonic() + 2
        try:
            u.emu_start(0x255ad, cpu.STOP, count=200_000)
            expected = bytes.fromhex(self.prompts[id_u32]['payloadHex'])
            raw = bytes(u.mem_read(cpu.DATA, len(before)))
            regs = [u.reg_read(reg) for reg in (r.UC_ARM_REG_R0, r.UC_ARM_REG_R1, r.UC_ARM_REG_R2, r.UC_ARM_REG_R3)]
            cpu._check(u.reg_read(r.UC_ARM_REG_PC) == cpu.STOP and u.reg_read(r.UC_ARM_REG_SP) == sp
                and [u.reg_read(reg) for reg in saved_regs] == saved and regs[0] == len(expected)
                and raw == expected + before[len(expected):] and not self.opened and not self.allocations,
                'minmax-language-output-or-lifetime-invariant-failed')
            cpu._check(all(a in self.seen for a in (0x255ac, 0x25574, 0x251c0, 0x25614, 0x32bc8, 0x32e00)),
                'minmax-language-original-chain-not-observed')
            return dict(idHex=f'{id_u32:08X}', capacity=capacity, payloadHex=expected.hex(),
                returnValue=regs[0], returnedRegisters=regs, returnedApsr=u.reg_read(r.UC_ARM_REG_APSR),
                returnedFpscr=u.reg_read(r.UC_ARM_REG_FPSCR), trace=dict(self.trace),
                fileReads=self.reads.copy(), originalFunctionEntries=self.seen.copy(),
                virtualFileClosed=True, virtualAllocationsRemaining=0, **GUARDS)
        except Exception:
            self.poisoned = True; raise


def validate_pair(fmt, words, fpscr=0):
    cpu._check(type(fmt) is bytes and b'\0' not in fmt
        and hashlib.sha256(fmt + b'\0').hexdigest() in {r[2] for r in PROMPTS[:2]},
        'minmax-sprintf-format-unreviewed')
    cpu._check(type(words) is bytes and len(words) == 8, 'minmax-sprintf-two-words-required')
    values = struct.unpack('<II', words)
    cpu._check(all(v <= 131070 for v in values), 'minmax-sprintf-integer-domain-unreviewed')
    cpu._check(type(fpscr) is int and 0 <= fpscr <= 0xffffffff and not fpscr & 0x00c00000,
        'minmax-sprintf-rounding-mode-unreviewed')
    return values


class CpuMinMaxSprintfReference(integer.CpuIntegerSprintfReference):
    def write(self, u, access, address, size, value, userdata):
        cpu._check(cpu.STACK <= address and address + size <= cpu.STACK + 0x100000
            or cpu.DATA <= address and address + size <= cpu.DATA + 100,
            'minmax-sprintf-write-range-unreviewed')

    def code(self, u, address, size, userdata):
        if self.hooks.get(address) == 'putc':
            r = self.r; a = u.reg_read(r.UC_ARM_REG_R0); b = u.reg_read(r.UC_ARM_REG_R1)
            cpu._check(b == self.imports['__sF'] + 84 and len(self.sink) < 99,
                'minmax-sprintf-stdio-unreviewed')
            self.sink.append(a & 255); self.trace['putc'] += 1
            u.reg_write(r.UC_ARM_REG_R0, a & 255); u.reg_write(r.UC_ARM_REG_PC, u.reg_read(r.UC_ARM_REG_LR))
            return
        return super().code(u, address, size, userdata)

    def format_pair(self, fmt, words, fpscr=0):
        lo, hi = validate_pair(fmt, words, fpscr)
        cpu._check(not self.closed and not self.poisoned, 'minmax-sprintf-prior-outcome-unknown-or-closed')
        u, r = self.u, self.r; sp = cpu.STACK + 0x80000; before = bytes([0xa5]) * 132
        u.mem_write(cpu.DATA, before); u.mem_write(cpu.DATA + 0x10000, fmt + b'\0')
        self.trace.clear(); self.sink.clear()
        for reg, value in ((r.UC_ARM_REG_R0, cpu.DATA), (r.UC_ARM_REG_R1, cpu.DATA + 0x10000),
                (r.UC_ARM_REG_R2, lo), (r.UC_ARM_REG_R3, hi), (r.UC_ARM_REG_SP, sp),
                (r.UC_ARM_REG_LR, cpu.STOP | 1), (r.UC_ARM_REG_FPSCR, fpscr)):
            u.reg_write(reg, value)
        self.deadline = time.monotonic() + 2
        try:
            u.emu_start(0x7dc9, cpu.STOP, count=200_000)
            raw = bytes(u.mem_read(cpu.DATA, len(before))); out = raw.split(b'\0', 1)[0]
            regs = [u.reg_read(reg) for reg in (r.UC_ARM_REG_R0, r.UC_ARM_REG_R1, r.UC_ARM_REG_R2, r.UC_ARM_REG_R3)]
            cpu._check(u.reg_read(r.UC_ARM_REG_PC) == cpu.STOP and u.reg_read(r.UC_ARM_REG_SP) == sp
                and len(out) < 100 and regs[0] == len(out) and self.sink == out
                and raw == out + b'\0' + before[len(out) + 1:], 'minmax-sprintf-output-invariant-failed')
            return dict(outputBytesHex=out.hex(), returnValue=regs[0], returnedRegisters=regs,
                returnedApsr=u.reg_read(r.UC_ARM_REG_APSR), returnedFpscr=u.reg_read(r.UC_ARM_REG_FPSCR),
                trace=dict(self.trace), **GUARDS)
        except Exception:
            self.poisoned = True; raise


def audit_matrix(calc, std, deviceid, language_blob, output):
    audit = audit_minmax_sources(calc, std, language_blob)
    output.mkdir(); lookup = CpuMinMaxLanguageReference(calc, std, language_blob)
    formatter = None; rows = []; formats = {int(r['idHex'], 16):bytes.fromhex(r['payloadHex'])[:-1] for r in audit['prompts'][:2]}
    try:
        formatter = CpuMinMaxSprintfReference(calc, std, deviceid)
        for id_u32, _, _ in PROMPTS:
            for capacity in (200, 1024):
                rows.append(dict(kind='lookup', result=lookup.lookup(id_u32, capacity)))
        for id_u32, fmt in formats.items():
            for lo, hi in ((0, 1), (1, 30), (0, 4092), (0, 65535), (8, 12), (16, 24), (6, 20), (0, 256), (131070, 131070)):
                result = formatter.format_pair(fmt, struct.pack('<II', lo, hi))
                comparison = fmt.replace(b'%d', str(lo).encode(), 1).replace(b'%d', str(hi).encode(), 1)
                cpu._check(bytes.fromhex(result['outputBytesHex']) == comparison, 'minmax-sprintf-decimal-comparison-failed')
                rows.append(dict(kind='format', idHex=f'{id_u32:08X}', words=[lo, hi], result=result))
    finally:
        try:
            if formatter is not None:
                formatter.close()
        finally:
            lookup.close()
    report = dict(kind='x431-original-minmax-prompts', schemaVersion=1, sourceAudit=audit, cases=rows,
        caseCount=len(rows), lookupCases=10, formatCases=18, enginesDisposed=True,
        codeSha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(), **GUARDS)
    with (output / 'result.json').open('x', encoding='utf8', newline='\n') as out:
        out.write(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    return report


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    for key in ('calc', 'std', 'deviceid', 'language', 'output-dir'):
        p.add_argument('--' + key, type=Path, required=True)
    a = p.parse_args(argv)
    r = audit_matrix(a.calc.read_bytes(), a.std.read_bytes(), a.deviceid.read_bytes(),
        a.language.read_bytes(), a.output_dir)
    print(json.dumps(dict(cases=r['caseCount'], enginesDisposed=r['enginesDisposed'], **GUARDS)))


if __name__ == '__main__':
    main()
