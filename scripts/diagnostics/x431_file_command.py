"""Pinned offline FILE command conversion; no device or vehicle execution.

Input is an explicit fresh source-table observation, not an ECU response. The
original file reader's unchecked callbacks and actual dynamic binding remain
unqualified. Native descriptor lengths are separate from wire candidates.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re

SOURCE_SHA256 = '80a4e5692adefa90a6bbf210bd6b9d5715eeb6410f3ee34f3bc25994e148bbab'
SOURCE_BYTES = 431792
REGIONS = (
    ('FromAddrGetCommand', 0x17498, 140, 'b1a5e1246f88a1c39fb6e1b8105a342e8247d6c5a46250667f468477a09ce70d'),
    ('FromCmdInfoToSendCmd', 0x177f8, 58, 'fc0f7cacaf4ef96dbc217f673625c64c127acf42eb52d421a21a90b94ae82db7'),
)
GUARDS = dict(offlineOnly=True, integerModelOnly=True,
    nativeOriginalInstructionsExecuted=False, actualDynamicBindingQualified=False,
    actualFileReadFreshnessQualified=False, runtimeReadSequenceQualified=False,
    vehicleVerified=False, diagnosticsQualified=False, codingWriteQualified=False,
    codingRestoreQualified=False, executionEnabled=False, writePayload=None)


def convert_source_command(table: bytes) -> dict:
    """Model both source bodies on explicitly supplied, complete table bytes.

    The 0xff marker selects one SID byte. Normal lengths 0..254 retain the
    native uint8 length, including 254+2 wrapping to zero; that case produces
    no wire candidate. Unknown bytes after the declared source extent are not
    read or included in either result.
    """
    if type(table) is not bytes or not 3 <= len(table) <= 4096:
        raise ValueError('file-command-table-window-invalid')
    sid, second, n = table[:3]
    consumed = 3 if n == 255 else 3 + n
    if len(table) < consumed:
        raise ValueError('file-command-source-extent-incomplete')
    request = table[:1] if n == 255 else table[:2] + table[3:consumed]
    length = len(request) & 255
    complete = length == len(request)
    descriptor = bytes((1, length)) + request
    return dict(kind='x431-offline-file-command', sourceLengthByte=n,
        sourceBytesConsumed=consumed, trailingSourceBytesIgnored=len(table)-consumed,
        sourceTableHex=table[:consumed].hex().upper(),
        nativeDescriptorHexCandidate=descriptor.hex().upper(),
        nativeLengthByte=length, nativePayloadBytes=len(request),
        lengthWrapped=not complete,
        status='single-byte-source-command' if n == 255 else
            'complete-source-command' if complete else 'native-length-wrapped-unqualified',
        requestHexCandidate=request.hex().upper() if complete else None,
        **GUARDS)


def audit_source(blob: bytes) -> dict:
    if type(blob) is not bytes or len(blob) != SOURCE_BYTES or hashlib.sha256(blob).hexdigest() != SOURCE_SHA256:
        raise ValueError('file-command-source-unreviewed')
    from .x431_native_binding import _Elf
    elf = _Elf(blob)
    regions = []
    for name, start, size, sha in REGIONS:
        symbols = [s for s in elf.symbols if s['name'] == name and s['type'] == 2 and s['section']]
        if (len(symbols) != 1 or symbols[0]['va'] != (start | 1) or symbols[0]['bytes'] != size
                or hashlib.sha256(elf.raw(start, size)).hexdigest() != sha):
            raise ValueError('file-command-function-unreviewed')
        regions.append(dict(name=name, startVa=start, bytes=size, sha256=sha))
    return dict(sourceSha256=SOURCE_SHA256, sourceBytes=SOURCE_BYTES, functions=regions, **GUARDS)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', required=True, type=Path)
    parser.add_argument('--table-hex', required=True)
    args = parser.parse_args(argv)
    if not re.fullmatch(r'(?:[0-9a-fA-F]{2}){3,4096}', args.table_hex):
        parser.error('file-command-table-hex-invalid')
    try:
        provenance = audit_source(args.source.read_bytes())
        result = convert_source_command(bytes.fromhex(args.table_hex))
    except (OSError, ValueError) as error:
        parser.error(str(error))
    print(json.dumps(dict(result, provenance=provenance)))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
