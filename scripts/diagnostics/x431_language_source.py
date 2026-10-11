"""Read-only audit of four retained X431 input prompt records and their source.

No original ARM instructions, host ELF, language-file cache or callbacks execute.
The returned length is a source-derived expectation, not an emulated result.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from .coding_input_source import _pic_address
from .x431_cpu_reference import SHA as CALC_SHA256
from .x431_input_reference import source_audit as input_source_audit
from .x431_native_binding import _Elf, STD_SHA256
from scripts.x431_re.gag_codec import decode_record, encrypt_number_strict, parse_text_gag
from scripts.x431_re.ggp_index import parse_header

HASHES = dict(calc=CALC_SHA256, std=STD_SHA256,
    language='7caf1c27e475511e4a75db8fe92008684f5632eeb9fbb4484cbc16fa0eec7de3')
TEXT_SHA256 = '4fb89f8afadf05d12b494103da1e3ce67ba448acee626aeb80dadd5c39557ec0'
REGIONS = (
    ('GetTextFromTextLib', 0x255ac, 104, 'c35db228325e3a59b09173b5b26ef06047771be88ba88fe07c8ce2abbfa8b3e6'),
    ('FromIdGetBinaryString', 0x25574, 54, '7df067cc3224b9b2c43df4a7fd123816d3eb3847cd456cb92eed939c9ab77a46'),
    ('IdSearch', 0x251c0, 804, '8a47e1a020bae055bf17c63caaa173ea2e0bf58576c6c947b06247c5eabbc3d9'),
    ('IndirectGetText', 0x25614, 692, '2c1c43f7fcecaa23efc889d5808b3b36cbd1c2d42274958d31fb7f1dc78dbc29'),
    ('OpenFileIssueSearchID', 0x32680, 1300, '0e4b1a479b5fd5d3ba942ef1cfddf5f520d6eabfbfb0c71704476ec388d03cc8'),
    ('CloseFileIssueSearchID', 0x32b98, 42, 'bb3b99bf813470eeac596e30db6852f49c93474286d5d5f4469d429b4ab53e38'),
    ('GagDecrypt', 0x32bc8, 564, '86f2fa1ce51b74d971ca02c4fc734b4b11347ff70341f7eb2e97a70b2f6d683f'),
    ('EncryptNumber', 0x32e00, 80, 'd3b94aa51d9e143e9c99d6038fbb56e07f23dff2678b228b13443c1c6926f34b'),
)
# Reviewed input call site, ID, source record index and complete decoded bytes.
PROMPTS = (
    (0x342c8, 0xff30001e, 1577, 'eb3b7cfa69571a94d550fca6b0862068f13bbe2551c6a2181675d11a627cc543'),
    (0x3454c, 0xff300023, 1517, 'aa3bdab2cea25f985ad2e5d4c9c5cc1361b70953565691347e09c8831d411661'),
    (0x34854, 0xff300020, 1520, 'f05158d97e4b368188329ee55b86cef71f21f17165fac6add4e29973e195755c'),
    (0x34b16, 0x00210365, 191576, '910ed01386e110626d15b7f7bc3bdaa104d842e27a6a8f50096add59c66d831f'),
)
GUARDS = dict(nativeCodeExecuted=False, cpuEngineImported=False, hostElfLoaded=False,
    actualLanguageFileStateQualified=False, actualDynamicBindingQualified=False,
    inputCharacterEncodingQualified=False, completeInputWorkflowQualified=False,
    headOpened=False, vehicleVerified=False, codingWriteQualified=False,
    executionEnabled=False, writePayload=None)


def _lookup(st, id_u32):
    """Binary lookup by encrypted key, preserving the complete decoded payload."""
    needle = bytearray(id_u32.to_bytes(4, 'big'))
    encrypt_number_strict(needle, st['ctx'])
    stride = st['keylen'] + 4
    base = st['index_off'] + 5
    lo, hi = 0, st['count'] - 1
    while lo <= hi:
        mid = (lo + hi) // 2
        start = base + mid * stride
        key = st['gag'][start:start + st['keylen']]
        if needle < key:
            hi = mid - 1
        elif needle > key:
            lo = mid + 1
        else:
            actual_key, body = decode_record(st, mid)
            if actual_key != key:
                raise ValueError('language-record-key-mismatch')
            return mid, key, body
    raise ValueError('language-prompt-id-missing')


def _plain_prompt_contract(body, capacity=200):
    # Reject truncation, missing NUL and indirect references. IdSearch itself
    # truncates before decrypting; this tool does not model that unsafe branch.
    if (type(body) is not bytes or type(capacity) is not int or capacity != 200
            or not body or len(body) > capacity or body[-1:] != b'\0'
            or b'\0' in body[:-1]):
        raise ValueError('language-complete-prompt-required')
    strlen = len(body) - 1
    if not 22 <= strlen <= 544:
        bypass = 'strlen-outside-22-through-544'
    elif body[:12] != b'SubIndexNum=':
        bypass = 'indirect-prefix-mismatch'
    else:
        raise ValueError('language-indirect-prompt-unreviewed')
    return dict(capacity=capacity, payloadBytes=len(body), strlenBytes=strlen,
        payloadContainsTerminalNul=True, truncationNeeded=False,
        indirectLookupBypass=bypass, sourceExpectedReturn=len(body),
        sourceExpectedReturnExecuted=False)


def audit_language_sources(calc_blob, std_blob, language_blob):
    blobs = dict(calc=calc_blob, std=std_blob, language=language_blob)
    for key, blob in blobs.items():
        if type(blob) is not bytes or hashlib.sha256(blob).hexdigest() != HASHES[key]:
            raise ValueError('language-source-' + key + '-unreviewed')
    _, sites = input_source_audit(calc_blob)
    std = _Elf(std_blob)
    regions = []
    for name, start, size, sha in REGIONS:
        symbols = [s for s in std.symbols if s['name'] == name and s['type'] == 2 and s['section']]
        if (len(symbols) != 1 or symbols[0]['va'] != (start | 1)
                or symbols[0]['bytes'] != size
                or hashlib.sha256(std.raw(start, size)).hexdigest() != sha):
            raise ValueError('language-source-function-unreviewed')
        regions.append(dict(symbol=name, startVa=start, bytes=size, sha256=sha))
    filename_got = _pic_address(std, 0x255b4, 0x255ba)
    relocation = std.relocations.get(filename_got, {})
    expected = dict(name='FileName', va=974346, bytes=1300, type=1, section=23)
    if filename_got != 0xa4f28 or relocation != dict(type=21, symbol=expected):
        raise ValueError('language-filename-object-unreviewed')
    prefix_va = _pic_address(std, 0x25658, 0x2565e)
    if std.raw(prefix_va, 13) != b'SubIndexNum=\0':
        raise ValueError('language-indirect-prefix-unreviewed')
    directory = parse_header(language_blob)
    entries = [e for e in directory['entries'] if e['name_ascii'] == 'TEXT_CN.GAG']
    if len(entries) != 1 or (entries[0]['offset'], entries[0]['size']) != (733, 7366717):
        raise ValueError('language-text-section-unreviewed')
    entry = entries[0]
    text_blob = language_blob[entry['offset']:entry['offset'] + entry['size']]
    if hashlib.sha256(text_blob).hexdigest() != TEXT_SHA256:
        raise ValueError('language-text-identity-unreviewed')
    st = parse_text_gag(text_blob)
    if (st['ver'], st['count'], st['keylen']) != (5.03, 201588, 4):
        raise ValueError('language-text-header-unreviewed')
    base, stride = st['index_off'] + 5, st['keylen'] + 4
    previous = None
    for i in range(st['count']):
        key = text_blob[base + i * stride:base + i * stride + st['keylen']]
        if previous is not None and previous >= key:
            raise ValueError('language-index-not-strictly-sorted')
        previous = key
    prompts = []
    for site, id_u32, expected_index, sha in PROMPTS:
        if not any(s['name'] == 'GetTextFromTextLib' and s['callVa'] == site for s in sites):
            raise ValueError('language-input-call-site-unreviewed')
        index, key, body = _lookup(st, id_u32)
        if index != expected_index or hashlib.sha256(body).hexdigest() != sha:
            raise ValueError('language-prompt-payload-unreviewed')
        prompts.append(dict(callVa=site, idHex=f'{id_u32:08X}', index=index,
            storedKeyHex=key.hex(), payloadHex=body.hex(), payloadSha256=sha,
            displayText=body[:-1].decode('gb18030'), displayDecoder='gb18030',
            displayDecoderQualifiesAndroidEncoding=False, **_plain_prompt_contract(body)))
    return dict(kind='x431-language-input-source-audit', schemaVersion=1,
        sourceSha256=HASHES.copy(), functionRegions=regions, prompts=prompts,
        textSection=dict(name='TEXT_CN.GAG', offset=entry['offset'], bytes=entry['size'],
            sha256=TEXT_SHA256, records=st['count'], keyBytes=st['keylen'],
            indexStrictlySorted=True), filenameObject=dict(gotVa=filename_got, **expected),
        indirectPrefix=dict(va=prefix_va, hex=b'SubIndexNum=\0'.hex()),
        sourceContract=dict(idByteOrder='big-endian-four-byte',
            idSearchTruncatesBeforePayloadDecrypt=True, truncationCompareVa=0x253d4,
            truncationAssignVa=0x253ec, payloadDecryptVa=0x25406,
            indirectCallVa=0x252ea, indirectReturnPropagated=True,
            openFailureReturn=0, closeReturnCheckedByWrapper=False,
            filenameObjectContentsObserved=False),
        blockers=['actual-language-filename-cache-and-dynamic-binding-unqualified',
            'integer-formatting-and-complete-input-path-unqualified',
            'other-language-records-and-indirect-text-not-executed'], **GUARDS)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for key in HASHES:
        parser.add_argument('--' + key, required=True, type=Path)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args(argv)
    report = audit_language_sources(*(getattr(args, k).read_bytes() for k in HASHES))
    rendered = json.dumps(report, ensure_ascii=False, indent=2) + '\n'
    if args.output:
        with args.output.open('x', encoding='utf8') as out:
            out.write(rendered)
    else:
        print(rendered, end='')
    return report


if __name__ == '__main__':
    main()
