"""Pinned X431 coding-input source audit. Never loads an ELF or opens a head.

Source storage, UI limits and rounding branches are evidence about this retained
Android implementation. They do not establish a vehicle's legal writable domain.
"""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import struct

from .offline_match import ANCHORS
from .x431_formula import formula_from_record
from .x431_native_binding import _Elf

SOURCE_HASHES = {
    'calc': '40a4bf4ebff5da4f5d3093e469287881a1aee876b483416ef0bdc8d9e8edc543',
    'readwrite': '8f5aef6bf660b0b834dd85ab55e704760a21f7dde3e9d7e46ab96c6478583c7a',
}
GUARDS = dict(nativeCodeExecuted=False, actualDynamicBindingQualified=False,
    nativeEditingBehaviorVerified=False, propertyMeaningQualified=False,
    inferredBoundsEnabled=False, vehicleVerified=False, codingWriteQualified=False,
    codingRestoreQualified=False, executionEnabled=False, writePayload=None)

# Exact original symbols, including their literal pools; not arbitrary code scans.
REGIONS = {
    'calc': (
        ('GetOdxCalrFromId', 0x25fa8, 132, 'e51fe97add9b810598de22496fd4c1ce6704c05b919213bac990f2aae40bd1fd'),
        ('ResultCalcFromOdxScriptNegate', 0x24760, 3056, '0ee550e832c987715665aebedc7b63ba50c27bb6c7e5a0494a2a530ba762f350'),
        ('_Z18GetLINEARInputInfoSsRSsSsi', 0x36d0c, 220, '9542eadb56aaafd592d2ee40062bd2b087c5f1f7b40500c4aa83421974e3e52e'),
        ('_Z19GetUserInutCondiStrSsSsRSsSsi', 0x369e0, 812, '8386ddb09764bed0b14a356f0cf4dec3d00e8d1dedd6d47f9a5b59ef25f06d37'),
        ('_Z9testInputSsRSsSsi', 0x33fd8, 4116, '2eb44d71d052c8b9e92119cdf484300b4a46983db5bea077ced0c25c51aa8d51'),
        ('_Z14GetLINEARInforSsRSsRiS_S_i', 0x314a8, 2084, '9c06f1eec1036df8b34c904c6c4d59da30ff10629d4d9b5d9540bce5a4bb3457'),
        ('_Z12GetLINEARAnsSsSsPhiiiRSs', 0x31cd0, 1552, '0bc7ba11cd20ae53ae6fa4714f544569246e0785ba83f6c2f8bd1b08ede8ef47'),
        ('_Z21GetIDENTICALInputInfoSsRSsSsi', 0x36900, 224, '9bb644b1b51035c201b182a9a8496d70e94158d471ead82225b05eead41dc0c0'),
        ('_Z17GetIDENTICALInforSsRSsS_RiS0_S0_S_i', 0x30fa0, 1288, '712350aeaf016f0853677ee5c1ee06da03ba597d4e9446066ee62a7dbee3ecf8'),
        ('_Z15GetIDENTICALAnsSsSsPhiiiRSs', 0x2ef44, 5008, '0eef53a4432d4f1a69c7bcc81c94363f1ec62b6bb33c226e8189b5dd23d0acfd'),
        ('_Z20GetIDENTICALAnsE1McrSsSsRSs', 0x302d4, 2248, '4daf6f5fe0939cc7b74f092f723ee12d9c6db5af8668ceab473496417305a91a'),
        ('GetBcdInputInfo', 0x24634, 300, 'd5098fc28dd667cd3a16e0692e3a79f355b929f77dde3c811714385ca00ec25d'),
    ),
    'readwrite': (
        ('_Z22GetOdxXmlMcrCodeInfoExP5TOKENPciRSt6vectorI15ODX_MCRCODEINFOSaIS3_EERSt3mapIiiSt4lessIiESaISt4pairIKiiEEE',
         0xff43c, 720, '92833a153d8e8654aea4a5313908974a23f684f45828fdc3eb6f9bf8fab0e01c'),
        ('_Z13WriteCodeInfoPcRSsPhS1_ish', 0xfe1e0, 260, '8bcaef2acdd26dfe81390f62c53520dda8ea233dd8349e41e54dc994ac7f8ab2'),
    ),
}
STRINGS = (
    (0x342f0, 0x342f4, 'LINEAR:'), (0x34306, 0x3430a, 'SCALE-LINEAR:'),
    (0x34340, 0x34344, '10'), (0x34352, 0x34356, '16'),
    (0x314d2, 0x314d6, 'Radix='), (0x3153a, 0x3153e, 'DataType='),
    (0x315f4, 0x315f8, 'BitLength='), (0x31678, 0x3167c, 'BaseDataType='),
    (0x31794, 0x31798, 'null'), (0x317a2, 0x317a6, 'null'),
    (0x317b2, 0x317b6, 'null'), (0x317c0, 0x317c4, 'null'),
    (0x346ba, 0x346be, 'null'), (0x346d4, 0x346d8, 'null'),
    (0x348b0, 0x348b4, 'null'), (0x348c4, 0x348c8, 'null'),
    (0x3102c, 0x31030, ',DataType='), (0x3113c, 0x31140, 'Standar'),
    (0x3114a, 0x3114e, 'MinMax'), (0x31162, 0x31164, 'MaxLength='),
    (0x311c2, 0x311c4, 'MinLength='), (0x31232, 0x31234, 'BitLength='),
    (0x34286, 0x3428a, 'A_UNICODE2STRING'), (0x34374, 0x3437a, 'A_UINT32'),
    (0x34384, 0x34388, 'A_BYTEFIELD'), (0x2f000, 0x2f004, 'BaseDataType='),
    (0x2f3e8, 0x2f3ec, 'A_ASCIISTRING'), (0x2f408, 0x2f40c, 'A_BYTEFIELD'),
    (0x2f2da, 0x2f2de, 'Termination='), (0x2f328, 0x2f32c, 'ZERO'),
    (0x2464e, 0x24650, 'Encoding='), (0x24698, 0x2469a, 'BCD-P'),
)


def _pic_address(elf, load, add):
    instruction = struct.unpack('<H', elf.raw(load, 2))[0]
    if instruction >> 11 == 9:
        register, delta = (instruction >> 8) & 7, (instruction & 255) * 4
    elif instruction == 0xf8df:
        next_half = struct.unpack('<H', elf.raw(load + 2, 2))[0]
        register, delta = next_half >> 12, next_half & 4095
    else:
        raise ValueError('coding-input-pic-load-unreviewed')
    addition = struct.unpack('<H', elf.raw(add, 2))[0]
    if (addition & 0xff00 != 0x4400 or (addition >> 3) & 15 != 15
            or ((addition & 7) | ((addition >> 4) & 8)) != register):
        raise ValueError('coding-input-pic-add-unreviewed')
    literal = ((load + 4) & ~3) + delta
    return (struct.unpack('<I', elf.raw(literal, 4))[0] + add + 4) & 0xffffffff


def audit_coding_input_sources(calc_blob, readwrite_blob):
    elves, regions = {}, []
    for key, blob in (('calc', calc_blob), ('readwrite', readwrite_blob)):
        if not isinstance(blob, bytes) or hashlib.sha256(blob).hexdigest() != SOURCE_HASHES[key]:
            raise ValueError('coding-input-' + key + '-source-unreviewed')
        elf = elves[key] = _Elf(blob)
        for name, start, size, sha in REGIONS[key]:
            symbols = [s for s in elf.symbols if s['name'] == name and s['type'] == 2 and s['section']]
            if (len(symbols) != 1 or symbols[0]['va'] & ~1 != start or symbols[0]['bytes'] != size
                    or hashlib.sha256(elf.raw(start, size)).hexdigest() != sha):
                raise ValueError('coding-input-function-region-unreviewed')
            regions.append(dict(library=key, name=name, startVa=start, bytes=size, sha256=sha))
    strings = []
    for load, add, expected in STRINGS:
        address = _pic_address(elves['calc'], load, add)
        if elves['calc'].cstring(address) != expected:
            raise ValueError('coding-input-string-unreviewed')
        strings.append(dict(loadVa=load, addVa=add, stringVa=address, value=expected))
    slots = []
    for key, load, add, name in (('calc', 0x36b06, 0x36b08, 'InputStringEx'),
                                ('readwrite', 0xfe250, 0xfe256, 'ResultCalcFromOdxScriptNegate')):
        address = _pic_address(elves[key], load, add)
        relocation = elves[key].relocations.get(address, {})
        symbol = relocation.get('symbol', {})
        if relocation.get('type') != 21 or symbol.get('name') != name or symbol.get('type') != 1 or symbol.get('bytes') != 4:
            raise ValueError('coding-input-function-slot-unreviewed')
        slots.append(dict(library=key, loadVa=load, addVa=add, gotVa=address,
                          symbol=name, objectVa=symbol['va']))
    if struct.unpack('<d', elves['calc'].raw(0x31c20, 8))[0] != 0.0:
        raise ValueError('coding-input-null-bound-constant-unreviewed')
    return dict(kind='x431-coding-native-input-source-audit', schemaVersion=2,
        sourceSha256=SOURCE_HASHES.copy(), functionRegions=regions, strings=strings, functionSlots=slots,
        propertyStorage=dict(recordBytes=96, tokenFormatByteOffset=2, threshold=11,
            sourceReadBytes=1, structureByteOffset=1, guardLoadVa=0xff5ee, guardBranchVa=0xff5f2,
            readCallVa=0xff602, storeVa=0xff60a, meaning='unresolved'),
        suffixStorage=dict(byteOffsetStructureOffset=0x58, bitOffsetStructureOffset=0x5a,
            formulaStructureOffset=0x5c, byteOffsetStoreVa=0xff61e,
            bitOffsetStoreVa=0xff634, formulaStoreVa=0xff64a),
        linearInput=dict(radixStrings=['10', '16'], decimalBranchVa=0x345f8,
            hexBranchVa=0x34884, uiFunction='InputStringEx', uiCallVa=0x36b50,
            validationCallVa=0x36b7e, invalidReturn=-1, uiRetryBranchVa=0x36bd2,
            retryIsVehicleWrite=False, nullBoundsCompared=True,
            sourceUnsignedNullBoundsBranchVa=0x31870, sourceUnsignedNullLowerConstantVa=0x31c20,
            sourceUnsignedNullLowerConstant=0.0, sourceUnsignedNullUpperUsesBitLength=True,
            inferredUiRangeIsVehicleLegalDomain=False),
        inverseNumeric=dict(function='GetLINEARAns', integerConversionVa=0x32078,
            halfStepConstantVa=0x3208c, halfStepCompareVa=0x32094,
            conditionalValueSelectVa=0x320aa, bitChangeCallVa=0x320bc,
            sourceHasHalfStepAdjustmentBranch=True, fullNativeRoundingEmulated=False,
            desktopTargetPolicy='exact-representable-source-bounded-value'),
        identicalInput=dict(inputTypeField='DataType', inputTypeStringLoadVa=0x3102c,
            codedTypeField='BaseDataType', codedTypeStringLoadVa=0x2f000,
            sharedUiConditionCallVa=0x3694c, fixedUnicodeInputTypeCompareVa=0x3428e,
            fixedUnicodeLengthCompareVa=0x342a0, variableStringMinimumCompareVa=0x343a2,
            variableStringMaximumCompareVa=0x343a6,
            inputLengthBasis='cxx-string-storage-length', inputCharacterEncodingQualified=False,
            fixedSourceBitLengthRoundedToBytes=True, sourceByteCountStoreVa=0x312a4,
            textTypeBranchVa=0x2f406, bytefieldTypeBranchVa=0x2f416,
            sourceMinMaxLengthValuesAreVehicleLegalDomain=False,
            fullNativeTextEncodeEmulated=False, arbitraryByteEditorEnabled=False),
        blockers=['property-meaning-unresolved', 'native-loader-and-runtime-unqualified',
            'native-input-and-rounding-runtime-unverified', 'vehicle-legal-coding-domain-unqualified',
            'native-text-character-encoding-and-length-workflow-unverified',
            'complete-fresh-original-coding-and-write-readback-restore-unqualified'], **GUARDS)


def describe_coding_input(record):
    """Keep source input and encoded types distinct; never infer a writable domain."""
    _, parsed = formula_from_record(record)
    if not parsed.get('ok'):
        return dict(resolved=False, reason=parsed.get('reason') or 'coding-input-formula-unresolved', **GUARDS)
    kind, coded, input_type = (parsed.get(k) for k in ('kind', 'baseDataType', 'dataType'))
    if not coded or not input_type:
        return dict(resolved=False, reason='coding-input-types-unresolved', **GUARDS)
    width = parsed.get('bitLength')
    # Byte extent is source layout metadata, not an input constraint or full block.
    extent = (width // 8 if type(width) is int and width > 0 and width % 8 == 0 else None)
    if kind == 'IDENTICAL':
        category = ('text' if coded in ('A_ASCIISTRING', 'A_UNICODE2STRING') else
                    'bytefield' if coded == 'A_BYTEFIELD' else 'numeric')
    else:
        category = kind.lower()
    return dict(resolved=True, formulaKind=kind, category=category, codedType=coded,
        inputType=input_type, inputTypeMatchesCodedType=bool(coded and coded == input_type),
        sourceEncoding=parsed.get('encoding'), sourceRadix=parsed.get('radix'),
        sourceFixedBits=width, sourceFixedBytes=extent,
        sourceLengthMode=parsed.get('lengthInfo') or ('fixed-bits' if width is not None else None),
        sourceMinimumLength=parsed.get('minLength'), sourceMaximumLength=parsed.get('maxLength'),
        sourceTermination=parsed.get('termination'),
        sourceNumericLower=parsed.get('lower'), sourceNumericUpper=parsed.get('upper'),
        inputCharacterEncodingQualified=False, legalInputDomainQualified=False,
        completeBlockLengthQualified=False, **GUARDS)


def audit_coding_record_storage(record, data):
    """Cross-check generated metadata against a bounded original record, not UI claims."""
    at = record.get('at')
    if type(at) is not int or at < 0 or at + 9 > len(data):
        raise ValueError('coding-input-record-position-invalid')
    size = struct.unpack_from('<H', data, at + 7)[0]
    end = at + 9 + size
    if not 2 <= size <= 256 or end + 8 > len(data):
        raise ValueError('coding-input-record-body-invalid')
    raw_chain, prefix, suffix = data[at+9:end], data[at:at+7], data[end:end+8]
    if b'\0' not in raw_chain:
        raise ValueError('coding-input-record-chain-unterminated')
    try:
        chain = raw_chain.split(b'\0', 1)[0].decode('ascii')
    except UnicodeDecodeError as error:
        raise ValueError('coding-input-record-chain-invalid') from error
    fields = dict(readSID=prefix[0], companionSID=struct.unpack_from('<H', prefix, 1)[0],
        pid=struct.unpack_from('<I', prefix, 3)[0], propertyByte=suffix[0],
        byteOffset=struct.unpack_from('<H', suffix, 1)[0], bitOffset=suffix[3])
    if (any(type(record.get(k)) is not int or record[k] != v for k, v in fields.items())
            or record.get('request_prefix_hex') != prefix.hex() or record.get('suffix_hex') != suffix.hex()
            or record.get('chain') != chain
            or (record.get('formula') or {}).get('id_hex') != f'{struct.unpack_from("<I", suffix, 4)[0]:08X}'):
        raise ValueError('coding-input-generated-record-source-mismatch')
    if (record.get('source') or {}).get('sha256', '').lower() != ANCHORS['data']:
        raise ValueError('coding-input-record-source-anchor-mismatch')
    return dict(sourceOffset=at, suffixSourceOffset=end, propertyByte=suffix[0],
                sourceStorageMatched=True, **GUARDS)


def audit_variant_storage(variants_path, data_blob):
    if not isinstance(data_blob, bytes) or hashlib.sha256(data_blob).hexdigest() != ANCHORS['data']:
        raise ValueError('coding-input-data-source-unreviewed')
    path = Path(variants_path)
    with path.open('rb') as source:
        if hashlib.file_digest(source, 'sha256').hexdigest() != ANCHORS['variants']:
            raise ValueError('coding-input-variants-source-unreviewed')
    properties, kinds, bounds, profiles, positions = Counter(), Counter(), Counter(), 0, set()
    shapes, identical_text, identical_bytefield = Counter(), Counter(), Counter()
    differing_types, unresolved_inputs = 0, 0
    with path.open(encoding='utf-8') as source:
        for line in source:
            variant = json.loads(line)
            profiles += 1
            for record in variant.get('pool_records', {}).get('coding', {}).get('records', []):
                result = audit_coding_record_storage(record, data_blob)
                properties[str(result['propertyByte'])] += 1
                positions.add(result['sourceOffset'])
                _, formula = formula_from_record(record)
                kinds[formula.get('kind') or 'unresolved'] += 1
                if formula.get('kind') == 'LINEAR':
                    bounds['explicit-both' if formula.get('lower') is not None and formula.get('upper') is not None
                           else 'missing-at-least-one'] += 1
                shape = describe_coding_input(record)
                if not shape['resolved']:
                    unresolved_inputs += 1
                else:
                    key = '|'.join(str(shape[k]) for k in ('formulaKind', 'codedType', 'inputType', 'sourceEncoding'))
                    shapes[key] += 1
                    if shape['codedType'] and shape['inputType'] and not shape['inputTypeMatchesCodedType']:
                        differing_types += 1
                    if shape['formulaKind'] == 'IDENTICAL':
                        lengths = {'text': identical_text, 'bytefield': identical_bytefield}.get(shape['category'])
                        if lengths is not None:
                            lengths[str(shape['sourceLengthMode'])] += 1
    with path.open('rb') as source:
        if hashlib.file_digest(source, 'sha256').hexdigest() != ANCHORS['variants']:
            raise ValueError('coding-input-variants-source-changed')
    return dict(kind='x431-coding-property-storage-census', variantsSha256=ANCHORS['variants'],
        dataSha256=ANCHORS['data'], profileCount=profiles, recordCount=sum(properties.values()),
        uniqueSourceRecordCount=len(positions), propertyCounts=dict(properties), formulaKinds=dict(kinds),
        linearBounds=dict(bounds), inputShapes=dict(shapes), unresolvedInputRecords=unresolved_inputs,
        differingInputAndCodedTypeRecords=differing_types,
        identicalTextLengths=dict(identical_text), identicalBytefieldLengths=dict(identical_bytefield),
        inputCharacterEncodingQualified=False, legalInputDomainQualified=False,
        storageMismatches=0, propertyValueIsWritePermission=False, **GUARDS)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-dir', type=Path, required=True)
    parser.add_argument('--variants', type=Path)
    parser.add_argument('--coding-bin', type=Path)
    args = parser.parse_args(argv)
    if (args.variants is None) != (args.coding_bin is None):
        parser.error('--variants and --coding-bin must be supplied together')
    try:
        report = audit_coding_input_sources((args.source_dir / 'libPORSCHE_CALC.so').read_bytes(),
                                            (args.source_dir / 'libPORSCHE_READWRITE.so').read_bytes())
        if args.variants is not None:
            report['recordStorage'] = audit_variant_storage(args.variants, args.coding_bin.read_bytes())
    except (OSError, ValueError, KeyError, TypeError) as error:
        parser.exit(2, f'{error}\n')
    print(json.dumps(report, ensure_ascii=True, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
