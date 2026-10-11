"""Explicit offline original-input instruction reference; never a vehicle editor.

Default execution stops before unbound runtime callbacks. Completed original
control flow under bounded foundation models does not qualify Android UI input,
character encoding, legal vehicle values, dynamic binding or writable coding.
"""
from pathlib import Path
import argparse, collections, hashlib, json, re, struct, time, weakref
from . import x431_cpu_reference as base
from . import x431_field_reference as field
from .coding_input_source import _pic_address
from .x431_formula import parse_formula_text

INPUT_REGIONS = (('_Z9testInputSsRSsSsi', 212952, 4116, '2eb44d71d052c8b9e92119cdf484300b4a46983db5bea077ced0c25c51aa8d51'),
 ('_Z14GetLINEARInforSsRSsRiS_S_i',
  201896,
  2084,
  '9c06f1eec1036df8b34c904c6c4d59da30ff10629d4d9b5d9540bce5a4bb3457'),
 ('_Z17GetIDENTICALInforSsRSsS_RiS0_S0_S_i',
  200608,
  1288,
  '712350aeaf016f0853677ee5c1ee06da03ba597d4e9446066ee62a7dbee3ecf8'),
 ('_Z10strReplaceSsSsSs', 232144, 100, '7348582c803161f4db2f6d90f1548511356d651f40fa0ef25a93c5fa96b17c1c'),
 ('_Z9textRadixSsPci', 217068, 116, 'd531b898449a5a99e699f28b62fc05f84142a77fc740b99aaaa2d67ff866751d'),
 ('Strcpy', 677088, 42, '4cb960be201defc3879115e97a45d785ec9087c1680072ac24764bae53849442'),
 ('_Z12GetPrecisionSs', 164216, 612, '57c69bc6cc74988a96412b790e1484139206c06c111c091df4a5708408b1573e'))
CALLBACK_SITES = ((213704, 213688, 213692, 1082384, 1095536, 'GetTextFromTextLib'),
 (213722, 213706, 213710, 1082428, 1096284, 'SPRINTF'),
 (213970, 213950, 213954, 1082384, 1095536, 'GetTextFromTextLib'),
 (213990, 213972, 213976, 1082428, 1096284, 'SPRINTF'),
 (214150, 214128, 214132, 1082384, 1095536, 'GetTextFromTextLib'),
 (214174, 214152, 214160, 1082428, 1096284, 'SPRINTF'),
 (214254, 214232, 214236, 1082384, 1095536, 'GetTextFromTextLib'),
 (214276, 214256, 214262, 1082428, 1096284, 'SPRINTF'),
 (214348, 214332, 214336, 1082384, 1095536, 'GetTextFromTextLib'),
 (214366, 214350, 214354, 1082428, 1096284, 'SPRINTF'),
 (214480, 214458, 214462, 1082384, 1095536, 'GetTextFromTextLib'),
 (214498, 214482, 214486, 1082428, 1096284, 'SPRINTF'),
 (214798, 214776, 214780, 1082384, 1095536, 'GetTextFromTextLib'),
 (214822, 214800, 214806, 1082428, 1096284, 'SPRINTF'),
 (214950, 214928, 214932, 1082384, 1095536, 'GetTextFromTextLib'),
 (214974, 214952, 214960, 1082428, 1096284, 'SPRINTF'),
 (215124, 215102, 215106, 1082384, 1095536, 'GetTextFromTextLib'),
 (215142, 215126, 215130, 1082428, 1096284, 'SPRINTF'),
 (215290, 215272, 215276, 1082384, 1095536, 'GetTextFromTextLib'),
 (215314, 215292, 215298, 1082428, 1096284, 'SPRINTF'),
 (215830, 215808, 215812, 1082384, 1095536, 'GetTextFromTextLib'),
 (215848, 215832, 215836, 1082428, 1096284, 'SPRINTF'),
 (215910, 215888, 215892, 1082384, 1095536, 'GetTextFromTextLib'),
 (215930, 215912, 215916, 1082428, 1096284, 'SPRINTF'),
 (215998, 215980, 215984, 1082384, 1095536, 'GetTextFromTextLib'),
 (216022, 216000, 216006, 1082428, 1096284, 'SPRINTF'),
 (216154, 216134, 216136, 1082384, 1095536, 'GetTextFromTextLib'),
 (216170, 216156, 216158, 1082428, 1096284, 'SPRINTF'),
 (203078, 203042, 203046, 1082428, 1096284, 'SPRINTF'),
 (203116, 203084, 203088, 1082428, 1096284, 'SPRINTF'))

GUARDS = {**base.GUARDS, 'inputInstructionsOnly': True,
    'androidInputWorkflowVerified': False, 'legalInputDomainQualified': False,
    'inputCharacterEncodingQualified': False, 'runtimeCallbacksBound': False}


def source_audit(blob):
    rows = field.source_audit(blob)
    elf = base._Elf(blob)
    for name, start, size, sha in INPUT_REGIONS:
        symbols = [s for s in elf.symbols if s['name'] == name and s['type'] == 2 and s['section']]
        if (len(symbols) != 1 or symbols[0]['va'] != (start | 1) or symbols[0]['bytes'] != size
                or hashlib.sha256(elf.raw(start, size)).hexdigest() != sha):
            raise ValueError('input-reference-function-unreviewed')
        rows.append(dict(name=name, startVa=start, bytes=size, sha256=sha))
    callbacks = []
    for call, load, add, got, obj, name in CALLBACK_SITES:
        relocation = elf.relocations.get(got, {})
        symbol = relocation.get('symbol', {})
        instruction = struct.unpack('<H', elf.raw(call, 2))[0]
        if (_pic_address(elf, load, add) != got or relocation.get('type') != 21
                or symbol.get('name') != name or symbol.get('va') != obj
                or symbol.get('type') != 1 or symbol.get('bytes') != 4
                or instruction & 0xff87 != 0x4780):
            raise ValueError('input-reference-callback-site-unreviewed')
        callbacks.append(dict(callVa=call, loadVa=load, addVa=add, gotVa=got,
                              objectVa=obj, name=name))
    return rows, callbacks


def validate_input(formula, input_bytes, flag):
    if (type(formula) is not str or not 1 <= len(formula) <= 1000
            or '\0' in formula or not formula.isascii()):
        raise ValueError('input-reference-formula-invalid')
    parsed = parse_formula_text(formula)
    if not parsed.get('ok') or parsed.get('kind') not in ('IDENTICAL', 'LINEAR'):
        raise ValueError('input-reference-formula-family-unreviewed')
    if type(input_bytes) is not bytes or len(input_bytes) > 128 or b'\0' in input_bytes:
        raise ValueError('input-reference-bytes-invalid')
    if type(flag) is not int or flag not in (0, 1):
        raise ValueError('input-reference-flag-unreviewed')
    return parsed


def integer_prefix(data, radix, signed, bits):
    """ASCII, bounded C integer prefix model; overflow/errno are not simulated.

    Prefix/sign/radix behavior is checked against Android Bionic strtol.cpp:
    https://android.googlesource.com/platform/bionic/+/9dc37b9439063df2b0db6d1b3a37b491bc3cd427/libc/bionic/strtol.cpp
    This is a foundation model, not the retained Android libc execution.
    """
    if (type(data) is not bytes or len(data) > 1024 or b'\0' in data
            or not data.isascii() or type(radix) is not int or radix not in (2, 10, 16)
            or type(signed) is not bool or type(bits) is not int or bits not in (32, 64)):
        raise ValueError('input-reference-integer-contract-unreviewed')
    text = data.decode('ascii').lstrip(' \t\r\n\v\f')
    negative = text.startswith('-')
    if text[:1] in ('+', '-'):
        text = text[1:]
    if radix == 16 and re.match(r'0[xX][0-9A-Fa-f]', text):
        text = text[2:]
    n = 0
    digits = '0123456789abcdef'
    for c in text.lower():
        if c not in digits or digits.index(c) >= radix:
            break
        n = n * radix + digits.index(c)
    limit = ((1 << (bits - 1)) if negative else (1 << (bits - 1)) - 1) if signed else (1 << bits) - 1
    if n > limit:
        raise ValueError('input-reference-integer-overflow-unreviewed')
    return (-n if negative else n) & ((1 << bits) - 1)


class CallbackBoundary(Exception):
    pass


class FoundationBoundary(Exception):
    pass


class CpuInputReference(field.CpuFieldReference):
    def __init__(self, blob):
        regions, callbacks = source_audit(blob)  # Before optional engine import.
        self.callback_sites = {r['callVa']: r for r in callbacks}
        self.closed = False
        super().__init__(blob)
        # The retained 2.1.4 implementation keys this table by hook handle.
        # Capture the single existing code hook without changing the shared
        # bit/field reference API or fingerprints of their earlier wrappers.
        hooks = getattr(self.u, '_callbacks', None)
        base._check(type(hooks) is dict and len(hooks) == 1
                   and all(type(key) is int and key > 0 for key in hooks),
                    'input-reference-engine-hook-table-unreviewed')
        self.code_hook = next(iter(hooks))
        self.function_regions = regions

    def code(self, u, address, size, userdata):
        row = self.callback_sites.get(address)
        if row:
            base._check(self.uint(row['objectVa']) == 0, 'input-reference-runtime-slot-not-unbound')
            registers = [u.reg_read(r) for r in (base.UC_ARM_REG_R0, base.UC_ARM_REG_R1,
                                                base.UC_ARM_REG_R2, base.UC_ARM_REG_R3)]
            observation = dict(row, registers=registers, slotValue=0,
                               stoppedBeforeCallback=True, callbackExecuted=False)
            if row['name'] == 'SPRINTF':
                # The second argument is an original C string, not an integer precision.
                observation['formatBytesHex'] = self.cstr(registers[1]).hex().upper()
            raise CallbackBoundary(observation)
        name = self.hooks.get(address)
        if name == 'strtod':
            a, b = [u.reg_read(r) for r in (base.UC_ARM_REG_R0, base.UC_ARM_REG_R1)]
            base._check(b == 0, 'input-reference-endptr-unreviewed')
            data = self.cstr(a)
            try:
                low, high = field.libc_double(data.decode('ascii'))
            except (ValueError, UnicodeError) as error:
                raise FoundationBoundary(dict(name=name, reason=str(error), inputBytesHex=data.hex().upper(),
                    stoppedBeforeConversion=True, conversionExecuted=False)) from error
            self.trace[name] += 1
            u.reg_write(base.UC_ARM_REG_R0, low); u.reg_write(base.UC_ARM_REG_R1, high)
            u.reg_write(base.UC_ARM_REG_PC, u.reg_read(base.UC_ARM_REG_LR))
            return
        if name in ('strtol', 'strtoul', 'strtoll', 'strtoull'):
            a, b, c = [u.reg_read(r) for r in (base.UC_ARM_REG_R0, base.UC_ARM_REG_R1, base.UC_ARM_REG_R2)]
            base._check(b == 0, 'input-reference-endptr-unreviewed')
            bits = 64 if name.endswith('ll') else 32
            data = self.cstr(a)
            try:
                value = integer_prefix(data, c, name in ('strtol', 'strtoll'), bits)
            except ValueError as error:
                raise FoundationBoundary(dict(name=name, reason=str(error), inputBytesHex=data.hex().upper(),
                    radix=c, bits=bits, stoppedBeforeConversion=True, conversionExecuted=False)) from error
            self.trace[name] += 1
            u.reg_write(base.UC_ARM_REG_R0, value & 0xffffffff)
            if bits == 64:
                u.reg_write(base.UC_ARM_REG_R1, value >> 32)
            u.reg_write(base.UC_ARM_REG_PC, u.reg_read(base.UC_ARM_REG_LR))
            return
        if name in ('__aeabi_l2d', '__aeabi_ul2d'):
            low, high = [u.reg_read(r) for r in (base.UC_ARM_REG_R0, base.UC_ARM_REG_R1)]
            value = low | (high << 32)
            if name == '__aeabi_l2d' and value >> 63:
                value -= 1 << 64
            lo, hi = struct.unpack('<II', struct.pack('<d', float(value)))
            u.reg_write(base.UC_ARM_REG_R0, lo); u.reg_write(base.UC_ARM_REG_R1, hi)
            self.trace[name] += 1
            u.reg_write(base.UC_ARM_REG_PC, u.reg_read(base.UC_ARM_REG_LR))
            return
        if name == 'sprintf':
            a, b, c = [u.reg_read(r) for r in (base.UC_ARM_REG_R0, base.UC_ARM_REG_R1, base.UC_ARM_REG_R2)]
            fmt = self.cstr(b).decode('ascii')
            base._check(fmt in ('%d', '%u', '%X', '%x', '%02X'), 'input-reference-integer-format-unreviewed')
            value = c - (1 << 32) if fmt == '%d' and c >> 31 else c
            data = (fmt % value).encode('ascii')
            base._check(len(data) < 50, 'input-reference-format-unbounded')
            u.mem_write(a, data + b'\0'); u.reg_write(base.UC_ARM_REG_R0, len(data))
            self.trace[name] += 1
            u.reg_write(base.UC_ARM_REG_PC, u.reg_read(base.UC_ARM_REG_LR))
            return
        if any(start <= address < start + length for _, start, length, _ in INPUT_REGIONS):
            self.last.append(address); self.trace['originalInstructions'] += 1
            if self.trace['originalInstructions'] % 1024 == 0:
                base._check(time.monotonic() < self.deadline, 'input-reference-wall-budget-exhausted')
            return
        return super().code(u, address, size, userdata)

    def put_string(self, obj, rep, data):
        if not data:
            self.word(obj, 0x10ce10 + 12)
            return
        rep = self.alloc(len(data) + 13)
        self.u.mem_write(rep, struct.pack('<III', len(data), len(data), 0) + data + b'\0')
        self.word(obj, rep + 12)

    def validate(self, formula, input_bytes, flag=0):
        validate_input(formula, input_bytes, flag)
        if self.poisoned or self.closed:
            raise ValueError('input-reference-prior-outcome-unknown-or-closed')
        self.trace.clear(); self.last.clear(); self.allocations.clear(); self.next_alloc = base.HEAP
        self.put_string(base.DATA + 0x20000, base.DATA + 0x21000, formula.encode('ascii'))
        self.put_string(base.DATA + 0x20010, base.DATA + 0x22000, b'')
        self.put_string(base.DATA + 0x20020, base.DATA + 0x23000, input_bytes)
        u = self.u; sp = base.STACK + 0x80000
        for r, v in ((base.UC_ARM_REG_R0, base.DATA + 0x20000), (base.UC_ARM_REG_R1, base.DATA + 0x20010),
                     (base.UC_ARM_REG_R2, base.DATA + 0x20020), (base.UC_ARM_REG_R3, flag),
                     (base.UC_ARM_REG_SP, sp), (base.UC_ARM_REG_LR, base.STOP | 1)):
            u.reg_write(r, v)
        self.deadline = time.monotonic() + 3
        try:
            u.emu_start(0x33fd9, base.STOP, count=1_000_000)
            base._check(u.reg_read(base.UC_ARM_REG_PC) == base.STOP, 'input-reference-not-returned')
            base._check(u.reg_read(base.UC_ARM_REG_SP) == sp, 'input-reference-stack-not-restored')
            value = u.reg_read(base.UC_ARM_REG_R0)
            if value >> 31:
                value -= 1 << 32
            output = self.cstr(self.uint(base.DATA + 0x20010)).hex().upper()
            # Original C++ disposes each caller-owned argument/result representation.
            for obj in (base.DATA + 0x20000, base.DATA + 0x20010, base.DATA + 0x20020):
                u.reg_write(base.UC_ARM_REG_R0, self.uint(obj) - 12)
                u.reg_write(base.UC_ARM_REG_R1, base.DATA + 0x20300)
                u.reg_write(base.UC_ARM_REG_LR, base.STOP | 1)
                u.emu_start(0xc6309, base.STOP, count=300_000)
                base._check(u.reg_read(base.UC_ARM_REG_PC) == base.STOP
                           and u.reg_read(base.UC_ARM_REG_SP) == sp, 'input-reference-dispose-not-returned')
            base._check(not self.allocations, 'input-reference-allocations-not-released')
            return dict(completed=True, returnValue=value,
                outputBytesHex=output,
                virtualAllocationsRemaining=0, trace=dict(self.trace), **GUARDS)
        except CallbackBoundary as error:
            self.poisoned = True
            return dict(completed=False, returnValue=None, outputBytesHex=None,
                boundaryKind='unbound-runtime-callback', boundary=error.args[0], virtualAllocationsRemaining=len(self.allocations),
                trace=dict(self.trace), **GUARDS)
        except FoundationBoundary as error:
            self.poisoned = True
            return dict(completed=False, returnValue=None, outputBytesHex=None,
                boundaryKind='unreviewed-foundation-conversion', boundary=error.args[0], virtualAllocationsRemaining=len(self.allocations),
                trace=dict(self.trace), **GUARDS)
        except Exception:
            self.poisoned = True
            raise

    def close(self):
        if self.closed:
            return
        u = self.u
        # Version 2.1.4's finalizer calls uc_close once, even if an exception
        # traceback still retains the Python Uc object. Never call uc_close twice.
        finalizer = getattr(u, '_Uc__finalizer', None)
        base._check(isinstance(finalizer, weakref.finalize) and finalizer.alive,
                    'input-reference-engine-finalizer-unreviewed')
        u.hook_del(self.code_hook)
        finalizer()
        base._check(not finalizer.alive, 'input-reference-engine-lifetime-not-ended')
        u._uch = None
        self.u = None; self.closed = True; self.poisoned = True


def matrix_cases():
    for bits in (8, 16, 32):
        for signed in (False, True):
            formula = field.integer_formula('IDENTICAL', bits, 1, signed).replace('32', str(bits))
            lo, hi = (-(1 << (bits - 1)), (1 << (bits - 1)) - 1) if signed else (0, (1 << bits) - 1)
            for radix in (10, 16):
                actual = formula.replace('Radix=10', f'Radix={radix}')
                values = (lo, 0, 1, hi, hi + 1, lo - 1)
                for value in values:
                    text = str(value) if radix == 10 else ('-' if value < 0 else '') + format(abs(value), 'X')
                    yield dict(kind='integer-boundary', formula=actual, inputBytesHex=text.encode().hex().upper(), flag=0)
                for text in (b'', b'abc', b'1tail', b'  +1', b'0x1', b'1.5'):
                    yield dict(kind='integer-prefix', formula=actual, inputBytesHex=text.hex().upper(), flag=0)
    for dtype in ('A_UNICODE2STRING', 'A_BYTEFIELD'):
        formula = field.integer_formula('IDENTICAL', 16, 1).replace('DataType=A_UINT32', f'DataType={dtype}')
        for data in (b'', b'A', b'AB', b'ABC', b'ABCD', b'G123', bytes.fromhex('C3A9'), bytes.fromhex('E4B880')):
            yield dict(kind='stored-byte-length', formula=formula, inputBytesHex=data.hex().upper(), flag=0)
    for precision in (0, 1, 3):
        formula = field.integer_formula('LINEAR', 16, 1).replace('Precision=0', f'Precision={precision}')
        for data in (b'0', b'4660', b'65535'):
            yield dict(kind='linear-runtime-callback', formula=formula, inputBytesHex=data.hex().upper(), flag=0)


def audit_matrix(blob, output):
    regions, callbacks = source_audit(blob)
    output.mkdir()  # Fresh output only; never replace previous evidence.
    counts = collections.Counter(); observations = []
    start = time.monotonic()
    with (output / 'cases.jsonl').open('w', encoding='utf-8', newline='\n') as stream:
        for case in matrix_cases():
            probe = CpuInputReference(blob)
            try:
                result = probe.validate(case['formula'], bytes.fromhex(case['inputBytesHex']), case['flag'])
                counts['cases'] += 1
                counts['completed' if result['completed'] else result['boundaryKind']] += 1
                if not result['completed']:
                    counts[result['boundary']['name']] += 1
                else:
                    counts['returnedZero' if result['returnValue'] == 0 else 'returnedOther'] += 1
            finally:
                probe.close()
            result['engineDisposed'] = True
            row = dict(case, result=result)
            stream.write(json.dumps(row, ensure_ascii=True, separators=(',', ':')) + '\n')
            observations.append(row)
    report = dict(kind='x431-input-instruction-reference', schemaVersion=1, counts=dict(counts),
        sourceSha256=base.SHA, functionRegions=regions, callbackSites=callbacks,
        engineVersion='2.1.4', elapsedSeconds=round(time.monotonic()-start, 5),
        engineDisposedCases=len(observations),
        code=[dict(name=name, sha256=hashlib.sha256(Path(__file__).with_name(name).read_bytes()).hexdigest())
              for name in ('x431_input_reference.py', 'x431_field_reference.py', 'x431_cpu_reference.py',
                           'x431_formula.py', 'x431_values.py', 'x431_native_binding.py', 'coding_input_source.py')],
        casesSha256=hashlib.sha256((output/'cases.jsonl').read_bytes()).hexdigest(), **GUARDS)
    (output/'result.json').write_text(json.dumps(report, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--calc-source', type=Path, required=True)
    parser.add_argument('--output-dir', type=Path, required=True)
    args = parser.parse_args()
    report = audit_matrix(args.calc_source.read_bytes(), args.output_dir)
    print(json.dumps(dict(counts=report['counts'], casesSha256=report['casesSha256'],
                         elapsedSeconds=report['elapsedSeconds'], **GUARDS)))


if __name__ == '__main__':
    main()
