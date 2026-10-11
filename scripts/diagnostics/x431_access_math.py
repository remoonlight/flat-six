"""Version-pinned offline integer transforms from X431 local helper bodies.

Local-helper windows are relative to the communication object, not an ECU PDU.
Mode 2/6 inputs are explicit unsigned register words; no response map is implied.
Named calculations use explicit source algorithm bytes; names do not select an ECU.
This module has no transport, clock, native loader, security session or write API.
The containing native helpers and their runtime callbacks are not reproduced.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re

SOURCE_SHA256 = 'fcba8457442a932ce520cdfea717462e9ff60d732e54421705a240897ea755b2'
SOURCE_BYTES = 591520
REGIONS = (
    ('1c940', 0x1c940, 188, 'beb08a287bd9d7120f36539cf2d4537668bd72e0b62c810f774bb82ddd43ff41'),
    ('1cd20', 0x1cd20, 184, 'fcf9f5712133395f76e7e48b16374483573dfffc16adca39c4349057f5f55c29'),
)
SCALAR_FUNCTIONS = (
    ('mode2', 'Porsche_Mode_2', 0x1e738, 110, '16c45a92390f76cee4ea982ee398a96aac4dd1f889d3e6e3f47393978b0e80e8'),
    ('mode6', 'Porsche_Mode_6', 0x1e7de, 112, 'd844140e5c5295b898d1375f489625b45869f6e50a7f659c11370ab51588517f'),
)
NAMED_FUNCTIONS = (
    ('bde-cod-w06', '_Z62ASAP1A_CCP_ComputeKeyFromSeed_DFEBB459FD90615ADC7AEE7698377B71PhtS_tPt',
     0x22220, 204, '34d734671796549b2560516a1d941f90f729c47476f2f46580453338d108302c', 4, 1, 'ASAM_seedkey_BDE_COD_W06'),
    ('gateway', '_Z62ASAP1A_CCP_ComputeKeyFromSeed_3F879E2AE51C814CDCDC18549BF9390APhtS_tPt',
     0x23ae4, 140, '3d5d8aed961da0522c778390fdcd51496cc05e6d6e1bdabefe2cfed3d5d1336e', 4, 40, 'ASAM_seedkey_Gateway'),
    ('psm', '_Z62ASAP1A_CCP_ComputeKeyFromSeed_E70BF466EE7EC94D7CA91F163898FEE5PhtS_tPt',
     0x230ec, 208, 'c69ccb2c6d4b43c404417e650a0d41d8b44ba166f63a1c65be004ca2ed947ab1', 2, 22, 'ASAM_seedkey_PSM'),
)
GUARDS = dict(integerModelOnly=True, nativeOriginalInstructionsExecuted=False,
    actualProcessBindingQualified=False, vehicleResponseMappingQualified=False,
    vehicleVerified=False, securityAccessEnabled=False, codingWriteQualified=False,
    codingRestoreQualified=False, executionEnabled=False, writePayload=None)


def _window(value, length):
    if type(value) is not bytes or len(value) != length:
        raise ValueError('access-math-input-window-invalid')
    return value


def transform_1c940(window: bytes) -> bytes:
    """Model source context[0x1326:0x132e], including its parity-controlled packing."""
    _window(window, 8)
    start = 0 if window[0] & 1 else 1
    state = int.from_bytes(window[start::2], 'big')
    for _ in range(36):
        carry = state & 0x80000000
        state = (state << 1) & 0xffffffff
        if carry:
            state ^= 0x42145091
    return state.to_bytes(4, 'big')


def transform_1cd20(window: bytes, *, second: int, minute: int, hour: int) -> bytes:
    """Model context[0x1326:0x1328] with explicitly supplied local tm words.

    The native body obtains these through time/localtime; no host clock is read.
    Valid ordinary localtime domains include a possible leap second value of 60.
    """
    _window(window, 2)
    if any(type(v) is not int or not 0 <= v <= limit
           for v, limit in ((second, 60), (minute, 59), (hour, 23))):
        raise ValueError('access-math-explicit-clock-words-invalid')
    state = int.from_bytes(window, 'big')
    for _ in range(44):
        feedback = (state & 0xb8e7).bit_count() & 1
        state = (state >> 1) | (feedback << 15)
    local_seconds = hour * 3600 + minute * 60 + second
    return bytes((state >> 8, (state & 0xe0) | (local_seconds & 0x1f)))


def _rotate_low_word(word: int):
    if type(word) is not int or not 0 <= word <= 0xffffffff:
        raise ValueError('access-math-unsigned-register-word-invalid')
    count = (word & 4) | ((word >> 2) & 8) | ((word >> 3) & 2) | ((word >> 14) & 1)
    low = word & 0xffff
    if count:
        if word & 0x200:
            low = ((low >> count) | (low << (16-count))) & 0xffff
        else:
            low = ((low << count) | (low >> (16-count))) & 0xffff
    return low, count


def transform_mode2(word: int) -> int:
    """Unsigned register result; source SXTH occurs only inside a nonzero loop."""
    low, count = _rotate_low_word(word)
    result = (low if low < 0x8000 else low-0x10000) if count else word
    operation = ((word >> 6) & 2) | ((word >> 11) & 1)
    if operation == 0:
        result &= word
    elif operation == 1:
        result |= word
    elif operation == 2:
        result ^= word
    return result & 0xffffffff


def transform_mode6(word: int) -> int:
    """Unsigned register result with the source's low-16-bit truncation."""
    result, _ = _rotate_low_word(word)
    low = word & 0xffff
    operation = ((word >> 6) & 2) | ((word >> 11) & 1)
    if operation == 0:
        result &= low
    elif operation == 1:
        result |= low
    elif operation == 2:
        result ^= low
    return result


def _unsigned_word(word: int, bits: int) -> int:
    if type(word) is not int or not 0 <= word < 1 << bits:
        raise ValueError('access-math-unsigned-algorithm-word-invalid')
    return word


def transform_bde_cod_w06(word: int) -> int:
    """Fixed four-byte source algorithm; no ECU identity or session is selected."""
    word = _unsigned_word(word,32)
    count = ((word >> 1) & 1) | ((word >> 10) & 2) | ((word >> 19) & 4) | ((word >> 23) & 8)
    if word & 0x20000000:
        count = 32-count
    rotated = ((word >> count) | (word << (32-count))) & 0xffffffff
    operation = ((word >> 6) & 1) | ((word >> 17) & 2)
    if operation == 0:
        return word & rotated
    if operation == 1:
        return word | rotated
    if operation == 2:
        return word ^ rotated
    return ~word & 0xffffffff


def transform_gateway(word: int) -> int:
    """Fixed source XOR/multiply/add with explicit 32-bit arithmetic."""
    word = _unsigned_word(word,32)
    return ((word ^ 0x23da0e7b) * 0xa953fd4e + 0x6ed9eba1) & 0xffffffff


def transform_psm(word: int) -> int:
    """Fixed two-byte source algorithm; its left branch shifts without wrapping."""
    word = _unsigned_word(word,16)
    count = ((word >> 13) & 1) | ((word >> 3) & 2) | ((word << 1) & 12)
    if word & 0x400:
        shifted = word << count
    else:
        shifted = (word >> count) | (word << (16-count))
    operation = ((word >> 12) & 1) | ((word >> 5) & 2)
    if operation == 0:
        shifted |= word
    elif operation == 1:
        shifted &= word
    elif operation == 2:
        shifted ^= word
    return shifted & 0xffff


def audit_source(blob: bytes):
    """Verify the exact retained ELF before attaching source provenance to CLI output."""
    if type(blob) is not bytes or len(blob) != SOURCE_BYTES or hashlib.sha256(blob).hexdigest() != SOURCE_SHA256:
        raise ValueError('access-math-source-unreviewed')
    from .x431_native_binding import _Elf
    elf = _Elf(blob)
    regions = []
    for entry, start, size, sha in REGIONS:
        if hashlib.sha256(elf.raw(start, size)).hexdigest() != sha:
            raise ValueError('access-math-source-region-unreviewed')
        regions.append(dict(entry=entry, startVa=start, sourceRangeBytes=size, sha256=sha,
            exactNativeFunctionExtentAsserted=False))
    for entry, name, start, size, sha in SCALAR_FUNCTIONS + tuple(row[:5] for row in NAMED_FUNCTIONS):
        symbols = [s for s in elf.symbols if s['name'] == name and s['section'] and s['type'] == 2]
        if (len(symbols) != 1 or symbols[0]['va'] != start|1 or symbols[0]['bytes'] != size
                or hashlib.sha256(elf.raw(start,size)).hexdigest() != sha):
            raise ValueError('access-math-source-scalar-function-unreviewed')
        regions.append(dict(entry=entry, symbol=name, startVa=start, sourceRangeBytes=size,
            sha256=sha, exactNativeFunctionExtentAsserted=True))
    return dict(sourceSha256=SOURCE_SHA256, sourceBytes=SOURCE_BYTES, regions=regions,
        namedSourceAlgorithms=[dict(entry=row[0],inputBytes=row[5],outputBytes=row[5],
            sourceNameTableIndex=row[6],sourceAlias=row[7],actualVehicleBindingQualified=False) for row in NAMED_FUNCTIONS],
        sourceWindowBaseOffset=0x1326, scalarSourceWindowOffset=None,
        scalarInputInterpretation='unsigned-32-bit-register-word',
        clockWordsMustBeExplicit=True, **GUARDS)


def main(argv=None):
    parser = argparse.ArgumentParser(description='Pinned X431 offline integer math; no vehicle/session operations')
    parser.add_argument('--source', required=True, type=Path)
    parser.add_argument('--entry', required=True, choices=('1c940', '1cd20', 'mode2', 'mode6', 'bde-cod-w06', 'gateway', 'psm'))
    parser.add_argument('--window-hex', required=True)
    parser.add_argument('--second', type=int)
    parser.add_argument('--minute', type=int)
    parser.add_argument('--hour', type=int)
    args = parser.parse_args(argv)
    scalar = args.entry in ('mode2', 'mode6')
    named = args.entry in ('bde-cod-w06','gateway','psm')
    size = (2 if args.entry == 'psm' else 4) if named else 4 if scalar else 8 if args.entry == '1c940' else 2
    if not re.fullmatch('[0-9a-fA-F]{' + str(size * 2) + '}', args.window_hex):
        parser.error('window must contain exactly the required hexadecimal bytes')
    window = bytes.fromhex(args.window_hex)
    try:
        provenance = audit_source(args.source.read_bytes())
        if args.entry == '1c940' or scalar or named:
            if any(v is not None for v in (args.second, args.minute, args.hour)):
                raise ValueError('access-math-clock-not-used-by-selected-entry')
            if named:
                fn = {'bde-cod-w06':transform_bde_cod_w06,'gateway':transform_gateway,'psm':transform_psm}[args.entry]
                result = fn(int.from_bytes(window,'big')).to_bytes(size,'big')
            elif scalar:
                fn = transform_mode2 if args.entry == 'mode2' else transform_mode6
                result = fn(int.from_bytes(window, 'big')).to_bytes(4, 'big')
            else:
                result = transform_1c940(window)
            clock = None
        else:
            result = transform_1cd20(window, second=args.second, minute=args.minute, hour=args.hour)
            clock = dict(second=args.second, minute=args.minute, hour=args.hour)
    except (ValueError, OSError) as error:
        parser.error(str(error))
    print(json.dumps(dict(kind='x431-offline-access-integer-math', entry=args.entry,
        inputWindowHex=window.hex(), outputBytesHex=result.hex(), localClockWords=clock,
        inputWindowInterpretation=('source-algorithm-big-endian-bytes' if named else
            'unsigned-32-bit-register-representation' if scalar else 'source-helper-context-window'),
        provenance=provenance, **GUARDS)))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
