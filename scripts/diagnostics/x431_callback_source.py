"""Pinned original input callback registration and implementation audit.

Reads three retained ELFs as bytes. No dlopen, CPU engine, host callback, head
or vehicle operation. Function names alone never qualify a runtime binding.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from .coding_input_source import _pic_address
from .x431_input_reference import source_audit as input_source_audit
from .x431_native_binding import _Elf, STD_SHA256
from .x431_cpu_reference import SHA as CALC_SHA256

HASHES = dict(calc=CALC_SHA256, std=STD_SHA256,
    deviceid='fee4c3597b12b6f0bacd34bbc40d7e00be05a812771b77ae61abe3e4e5285ae2')
GUARDS = dict(nativeCodeExecuted=False, hostElfLoaded=False,
    actualDynamicBindingQualified=False, runtimeCallbacksBound=False,
    androidInputWorkflowVerified=False, nativeEditingBehaviorVerified=False,
    legalInputDomainQualified=False, inputCharacterEncodingQualified=False,
    vehicleVerified=False, codingWriteQualified=False, codingRestoreQualified=False,
    executionEnabled=False, writePayload=None)

REGIONS = (
    ('calc', 'OpenStdDll', 0xa8de8, 68,
     'd133d16d325b31b5b1d20e4da3842093473a427606bace85f48acb34db4c8e4a'),
    ('calc', 'OpenSearchIDDLL', 0xa8e58, 672,
     'd77ecc5dd826107ef9439558d9356c72b012a185975d6cbc040a0feb1309a0ba'),
    ('calc', 'OpenDeviceIdDll', 0xaa988, 432,
     '196470016ba76b7ce784393a7016ca941e96a3207aab7a480aa2e2f56e26cd02'),
    ('calc', 'CloseDeviceIdDll', 0xaab38, 44,
     'f3fce211a0499f488318c60c4f627503b38359cbcbd2b3ade8dafdf247bd2b6d'),
    ('std', 'GetTextFromTextLib', 0x255ac, 104,
     'c35db228325e3a59b09173b5b26ef06047771be88ba88fe07c8ce2abbfa8b3e6'),
    ('deviceid', 'SPRINTF', 0x7dc8, 80,
     '76b531822a439c08692a75acacb03b650c2c5cbcbbfd33e1d49d25090e3d5054'),
    # Local routines have no independent dynsym function. These bounds were
    # reviewed from direct callers and terminal instructions, not symbol labels.
    ('deviceid', None, 0x7a50, 888,
     'c3359dcf72db362000e6f8069c5f644d62cf7b929f69d079f6e5e4e5ac889a83'),
    ('deviceid', None, 0x866c, 196,
     '4f87eb2898bc63d31242f3e8e62354ee023ea765e97f764e9de9d21bdd98e09e'),
    ('deviceid', None, 0x8730, 228,
     '3b74828e9e0a1d81f23b957dcb7cf6dae3e4b92af318543fe3dd925fc9ad98cf'),
    ('deviceid', None, 0x8814, 68,
     'e7faa39886223f8d4539a9e5fd7add1f8a42f4ce97eb7a28fdc5a104705c262f'),
)
DEVICE_BINDINGS = (
    ('GetSysFunCfg', 0xaa9ae, 0xaa9b0, 0xaa9b2, 0xaa9b6, 0xaa9ba, 0xaa9be, 0x10ba54, -3),
    ('FreeCfgMemory', 0xaa9c4, 0xaa9c8, 0xaa9ce, 0xaa9d2, 0xaa9d6, 0xaa9da, 0x10ba58, -4),
    ('fseekEx', 0xaa9e0, 0xaa9e4, 0xaa9ea, 0xaa9ee, 0xaa9f2, 0xaa9f6, 0x10ba60, -5),
    ('freadEx', 0xaa9fc, 0xaaa00, 0xaaa06, 0xaaa0a, 0xaaa0e, 0xaaa12, 0x10ba64, -6),
    ('fopenEx', 0xaaa18, 0xaaa1c, 0xaaa22, 0xaaa26, 0xaaa2a, 0xaaa2e, 0x10ba68, -7),
    ('fcloseEx', 0xaaa34, 0xaaa38, 0xaaa3e, 0xaaa42, 0xaaa46, 0xaaa4a, 0x10ba6c, -8),
    ('SPRINTF', 0xaaa50, 0xaaa54, 0xaaa5a, 0xaaa5e, 0xaaa62, 0xaaa66, 0x10ba5c, -9),
    ('GetLanCode', 0xaaa6c, 0xaaa70, 0xaaa76, 0xaaa7a, 0xaaa7e, 0xaaa82, 0x10ba70, -10),
)


def _slot(elf, load, add, name, object_va, defined=True):
    got = _pic_address(elf, load, add)
    relocation = elf.relocations.get(got, {})
    symbol = relocation.get('symbol', {})
    if (relocation.get('type') != 21 or symbol.get('name') != name
            or symbol.get('type') != 1 or symbol.get('bytes') != (4 if defined else 0)
            or symbol.get('va') != object_va or bool(symbol.get('section')) != defined):
        raise ValueError('callback-source-slot-unreviewed')
    return dict(loadVa=load, addVa=add, gotVa=got, objectVa=object_va, symbol=name)


def _binding(elf, name, name_load, name_add, call, slot_load, slot_add, store, obj, missing):
    name_va = _pic_address(elf, name_load, name_add)
    if elf.cstring(name_va) != name or elf.raw(store - 2, 4).hex() != '09680860':
        raise ValueError('callback-source-binding-unreviewed')
    return dict(name=name, nameVa=name_va, nameLoadVa=name_load, nameAddVa=name_add,
        dlsymCallVa=call, slotStoreVa=store, missingSymbolReturn=missing,
        **_slot(elf, slot_load, slot_add, name, obj))


def audit_callback_sources(calc_blob, std_blob, deviceid_blob):
    blobs = dict(calc=calc_blob, std=std_blob, deviceid=deviceid_blob)
    # All identities before parsing any ELF or trusting cross-library symbols.
    for key, blob in blobs.items():
        if type(blob) is not bytes or hashlib.sha256(blob).hexdigest() != HASHES[key]:
            raise ValueError('callback-source-' + key + '-unreviewed')
    elves = {k: _Elf(v) for k, v in blobs.items()}
    input_regions, input_sites = input_source_audit(calc_blob)
    regions = []
    for key, name, start, size, sha in REGIONS:
        elf = elves[key]
        if name is not None:
            symbols = [s for s in elf.symbols if s['name'] == name and s['type'] == 2 and s['section']]
            if (len(symbols) != 1 or symbols[0]['va'] != (start | 1)
                    or symbols[0]['bytes'] != size):
                raise ValueError('callback-source-function-symbol-unreviewed')
        if hashlib.sha256(elf.raw(start, size)).hexdigest() != sha:
            raise ValueError('callback-source-region-unreviewed')
        regions.append(dict(library=key, symbol=name, startVa=start, bytes=size, sha256=sha))
    calc, device = elves['calc'], elves['deviceid']
    device_name_va = _pic_address(calc, 0xaa998, 0xaa99c)
    if calc.cstring(device_name_va) != 'libDEVICEID.so':
        raise ValueError('callback-source-device-library-unreviewed')
    handles = [_slot(calc, 0xaa98c, 0xaa98e, 'pDeviceIdHandle', 0x10b374),
               _slot(calc, 0xa8e5c, 0xa8e5e, 'pStdSearchIDHandle', 0x10b380),
               _slot(calc, 0xa8dec, 0xa8dee, 'pStdDll', 0x10b378)]
    std_names = [calc.cstring(_pic_address(calc, load, add))
                 for load, add in ((0xa8df6, 0xa8dfa), (0xa8e02, 0xa8e06))]
    if std_names != ['libSTD.so', './libSTD.so']:
        raise ValueError('callback-source-std-library-unreviewed')
    bindings = [_binding(calc, *row) for row in DEVICE_BINDINGS]
    text = _binding(calc, 'GetTextFromTextLib', 0xa8ec6, 0xa8eca, 0xa8ecc,
                    0xa8ed0, 0xa8ed4, 0xa8ed8, 0x10b770, -6)
    for name, library, binding in (('SPRINTF', 'deviceid', bindings[6]),
                                   ('GetTextFromTextLib', 'std', text)):
        caller_sites = [r for r in input_sites if r['name'] == name]
        if not caller_sites or any(r['gotVa'] != binding['gotVa'] or r['objectVa'] != binding['objectVa']
                                   for r in caller_sites):
            raise ValueError('callback-source-caller-registration-mismatch')
    sink = _slot(device, 0x881a, 0x881e, '__sF', 0, defined=False)
    return dict(kind='x431-input-callback-source-audit', schemaVersion=1,
        sourceSha256=HASHES.copy(), functionRegions=regions,
        inputFunctionRegions=input_regions, inputCallbackSites=input_sites,
        callbackOrigins=[dict(name='GetTextFromTextLib', library='libSTD.so',
            loader='OpenSearchIDDLL', handleSource='OpenStdDll', exportEntryVa=0x255ad,
            **{k:v for k,v in text.items() if k != 'name'}),
            dict(name='SPRINTF', library='libDEVICEID.so', loader='OpenDeviceIdDll',
                dlopenLibraryNameVa=device_name_va, exportEntryVa=0x7dc9,
                **{k:v for k,v in bindings[6].items() if k != 'name'})],
        deviceIdBindings=bindings, handleSlots=handles,
        stdLoader=dict(libraryNames=std_names, dlopenFlags=1,
            dlopenCallSites=[0xa8dfc, 0xa8e08], cachedHandleReturnsHandle=True,
            dlopenFailureReturn=0),
        deviceIdLoader=dict(dlopenCallVa=0xaa99e, dlopenFlags=1, dlopenFailureReturn=-1,
            handleStoredBeforeBindings=True, handleStoreVa=0xaa9aa,
            cachedNonzeroHandleReturn=0, cachedHandleBranchVa=0xaa996,
            cachedHandleRechecksSymbols=False, failureDoesNotClearHandleWithinLoader=True,
            failureDoesNotClearPriorBindingsWithinLoader=True,
            explicitCloseEntryVa=0xaab39, closeClearsHandle=True,
            closeClearsFunctionSlots=False, closeChecksDlcloseReturn=False,
            actualRuntimeRetrySafetyQualified=False),
        sprintfContract=dict(destinationRegister='r0', formatPointerRegister='r1',
            variadicRegistersSaved=['r2', 'r3'], variadicSaveVa=0x7dd4,
            formatterEntryVa=0x7a51, formatterCallVa=0x7dee,
            ownFormatter=True, ordinaryLibcSprintfSubstitutionQualified=False,
            floatingArgumentAlignedToBytes=8, floatReadVa=0x7b8c,
            floatPrecisionParserVa=0x7adc, floorCallVa=0x7c70,
            finiteFloatBehaviorEmulated=False, specialFloatBehaviorQualified=False,
            destinationLengthArgumentPresent=False,
            characterEmitterEntryVa=0x8815, characterEmitterPutcCallVa=0x8828,
            sourcePutcCalledBeforeDestinationPointerCheck=True,
            sourceStdioObjectOffset=84, sourceStdioSink=sink,
            hostStdioCallAllowed=False, actualRuntimeOutputSinkQualified=False),
        textContract=dict(idRegister='r0', idByteOrder='big-endian-four-byte',
            idByteStoresVa=[0x255d4, 0x255dc, 0x255e0, 0x255e6],
            delegate='FromIdGetBinaryString', delegateCallVa=0x255ec,
            actualLanguageLibraryStateQualified=False, actualTranslatedTextQualified=False),
        blockers=['android-runtime-library-identity-and-binding-unqualified',
            'original-custom-formatter-execution-and-stdio-effect-unverified',
            'language-file-state-and-translated-strings-unqualified',
            'complete-input-editing-workflow-and-vehicle-domain-unqualified'], **GUARDS)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for key in HASHES:
        parser.add_argument('--' + key, required=True, type=Path)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args(argv)
    report = audit_callback_sources(*(getattr(args, k).read_bytes() for k in HASHES))
    rendered = json.dumps(report, ensure_ascii=False, indent=2) + '\n'
    if args.output:
        # Evidence is append-only at this entry point; no silent report overwrite.
        with args.output.open('x', encoding='utf8') as out:
            out.write(rendered)
    else:
        print(rendered, end='')
    return report


if __name__ == '__main__':
    main()
