"""Pinned InputStringEx registration, byte-length return and caller source.

Static evidence only: no native library, CPU engine, UI, head or vehicle opens.
The retained STD input message is a UI exchange, not a vehicle write command.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from .coding_input_source import _pic_address
from .x431_callback_source import HASHES as CALLBACK_HASHES, _slot
from .x431_native_binding import _Elf

HASHES = {k: CALLBACK_HASHES[k] for k in ('calc', 'std')}
REGIONS = (
    ('calc', 'OpenStdDll', 0xa8de8, 68,
     'd133d16d325b31b5b1d20e4da3842093473a427606bace85f48acb34db4c8e4a'),
    ('calc', 'OpenShowInterfaceDLL', 0xa90f8, 5544,
     'c580f15772abc49087b471f806271482d745410a701c067f48123483791d750c'),
    ('calc', 'ResultCalcFromOdxScriptNegate', 0x24760, 3056,
     '0ee550e832c987715665aebedc7b63ba50c27bb6c7e5a0494a2a530ba762f350'),
    ('calc', '_Z21GetIDENTICALInputInfoSsRSsSsi', 0x36900, 224,
     '9bb644b1b51035c201b182a9a8496d70e94158d471ead82225b05eead41dc0c0'),
    ('calc', '_Z19GetUserInutCondiStrSsSsRSsSsi', 0x369e0, 812,
     '8386ddb09764bed0b14a356f0cf4dec3d00e8d1dedd6d47f9a5b59ef25f06d37'),
    ('std', 'InputStringEx', 0x26fb4, 22,
     '09cf1c711b5f0d6469b03161dd3fbe9c2837a65b70d79f71867c00175173400a'),
    # Local bounds include reviewed literal pools; neither has a dynsym entry.
    ('std', None, 0x26fe0, 300,
     '034f0fc74fef95c0e97bfb0368cac2ec997fc5a47d6f6a21c2d57c237b294c7c'),
    ('std', None, 0x321b0, 60,
     'dcb2ecb876a004c12a34781387fa8ff7836d919111acbdcde1d4d42deb4ed8c8'),
    ('std', 'NotifyDataReadyWaitAnswerSendData', 0x41248, 512,
     '1f33a068875c157c5dbd70a8388482cd15e3aabd765d355933acad5f3293b47a'),
)
GUARDS = dict(nativeCodeExecuted=False, hostElfLoaded=False,
    actualDynamicBindingQualified=False, runtimeCallbacksBound=False,
    androidInputWorkflowVerified=False, inputCharacterEncodingQualified=False,
    legalInputDomainQualified=False, actualInputInfoReturnQualified=False,
    vehicleVerified=False, codingWriteQualified=False, codingRestoreQualified=False,
    executionEnabled=False, writePayload=None)


def _array_slot(elf, load, add):
    got = _pic_address(elf, load, add)
    relocation = elf.relocations.get(got, {})
    symbol = relocation.get('symbol', {})
    if (relocation.get('type') != 21 or symbol.get('name') != 'pSwapDataBuffer'
            or symbol.get('va') != 0xe660a or symbol.get('type') != 1
            or symbol.get('bytes') != 0x5000 or not symbol.get('section')):
        raise ValueError('input-info-source-buffer-unreviewed')
    return dict(loadVa=load, addVa=add, gotVa=got, objectVa=0xe660a,
        bytes=0x5000, symbol='pSwapDataBuffer')


def audit_input_info_sources(calc_blob, std_blob):
    blobs = dict(calc=calc_blob, std=std_blob)
    for key, blob in blobs.items():
        if type(blob) is not bytes or hashlib.sha256(blob).hexdigest() != HASHES[key]:
            raise ValueError('input-info-source-' + key + '-unreviewed')
    elves = {k: _Elf(v) for k, v in blobs.items()}
    regions = []
    for key, name, start, size, sha in REGIONS:
        elf = elves[key]
        if name is not None:
            symbols = [s for s in elf.symbols if s['name'] == name and s['type'] == 2 and s['section']]
            if len(symbols) != 1 or symbols[0]['va'] != start | 1 or symbols[0]['bytes'] != size:
                raise ValueError('input-info-source-symbol-unreviewed')
        if hashlib.sha256(elf.raw(start, size)).hexdigest() != sha:
            raise ValueError('input-info-source-region-unreviewed')
        regions.append(dict(library=key, symbol=name, startVa=start, bytes=size, sha256=sha))
    calc, std = elves['calc'], elves['std']
    paths = [calc.cstring(_pic_address(calc, a, b)) for a, b in
             ((0xa8df6, 0xa8dfa), (0xa8e02, 0xa8e06))]
    name_va = _pic_address(calc, 0xa965e, 0xa9662)
    # This registration dereferences R1 into R2 and stores R0 through R2.
    if paths != ['libSTD.so', './libSTD.so'] or calc.cstring(name_va) != 'InputStringEx':
        raise ValueError('input-info-source-registration-name-unreviewed')
    for va, expected in ((0xa9114, '76f782ef'), (0xa9668, '76f7c6ec'),
            (0xa9672, '0a68'), (0xa967a, '1060'), (0x247e2, 'f9f7c2ec0546'),
            (0x2483c, '2b46f9f7a0ec'), (0x36b50, 'a8470390'), (0x36c28, '0398')):
        if calc.raw(va, len(expected)//2).hex() != expected:
            raise ValueError('input-info-source-caller-instruction-unreviewed')
    binding = _slot(calc, 0xa966c, 0xa9670, 'InputStringEx', 0x10b82c)
    caller = _slot(calc, 0x36b06, 0x36b08, 'InputStringEx', 0x10b82c)
    if binding['gotVa'] != caller['gotVa']:
        raise ValueError('input-info-source-caller-slot-mismatch')
    buffers = [_array_slot(std, a, b) for a, b in ((0x27030, 0x27034), (0x270c2, 0x270c4))]
    sequence = _pic_address(std, 0x27032, 0x27036)
    if sequence != 0xced58 or sequence in std.relocations:
        raise ValueError('input-info-source-sequence-unreviewed')
    exchange = _slot(std, 0x321b4, 0x321b6, 'g_pSwapDataBuffer', 0xa606c)
    return dict(kind='x431-input-info-source-audit', schemaVersion=1, sourceSha256=HASHES.copy(),
        functionRegions=regions, registration=dict(libraryNames=paths, loader='OpenShowInterfaceDLL',
            handleSource='OpenStdDll', handle=_slot(calc, 0xa90fc, 0xa9100,
            'pStdShowInterFaceHandle', 0x10b37c), nameVa=name_va, dlsymCallVa=0xa9668,
            slotStoreVa=0xa967a, exportEntryVa=0x26fb5, **binding),
        caller=dict(callbackSiteVa=0x36b50, callbackReturnStoreVa=0x36b52,
            callbackReturnReloadVa=0x36c28, inputInfoCallVa=0x247e2,
            inputInfoReturnSaveVa=0x247e6, answerR3MoveVa=0x2483c,
            answerCallVa=0x2483e, answerR3Source='input-info-return-not-buffer-capacity',
            stackCapacity=4096, initialArgumentIsNull=True, **caller),
        stdContext=dict(bufferSlots=buffers, sequenceVa=sequence, exchangePointer=exchange,
            actualContextInitializationQualified=False),
        inputContract=dict(argumentRegisters=['title', 'conditions', 'empty-initial-or-null', 'destination'],
            capacityOnCallerStack=True, invalidArgumentReturn=-50, cancelReturn=-1,
            successReturn='signed16(strlen(destination))', returnSignExtendVa=0x270ea,
            uiOpcode=0x10, requestPayloadOrder=['conditions-NUL', 'title-NUL', 'initial-NUL'],
            requestLengthBytes='big-endian', requestFixedBytes=7,
            sequenceIncrementModulo=256, sourceCapacityClamp=0x5000,
            replyStatusOffset=4, replyTextOffset=5, replyTerminatorOffset='capacity+5',
            strcpyIncludesNul=True, destinationNeedsInputBytesPlusOne=True,
            destinationCapacityEnforcedByStrcpy=False, sourceMaxCapacityWithinBuffer=False,
            capacityEdgeSafetyQualified=False),
        exchangeContract=dict(helperEntryVa=0x321b1, notifyEntryVa=0x41249,
            notifyCallVa=0x321c4, positiveNotifyReturnRequired=True,
            nonpositiveNotifyPathQualified=False, actualAndroidNotifyExecuted=False), **GUARDS)


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    for key in HASHES:
        p.add_argument('--' + key, type=Path, required=True)
    p.add_argument('--output', type=Path)
    a = p.parse_args(argv)
    report = audit_input_info_sources(a.calc.read_bytes(), a.std.read_bytes())
    text = json.dumps(report, ensure_ascii=False, indent=2) + '\n'
    if a.output:
        with a.output.open('x', encoding='utf8', newline='\n') as out:
            out.write(text)
    else:
        print(text, end='')
    return report


if __name__ == '__main__':
    main()
