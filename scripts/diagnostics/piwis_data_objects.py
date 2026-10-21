"""Static DOP/compu, unit and constraint layouts for the retained kernel profile.

Reads definitions only, never vendor code. It does not build diagnostic PDUs or
decode a vehicle value. Record envelopes, encoding/category semantics and ECU
applicability remain unqualified. Unknown branches retain the stop offset.
"""
import math
import struct

from .piwis_database import RECORD_LIMIT, digest
from .piwis_parameters import Reader, UnsupportedLayout, diag_coded_type

SOURCE_EVIDENCE_RVAS = {
    'dopRegistration': 0x2440, 'dopFactory': 0xd14d0, 'dopConstructor': 0xd1500,
    'dopVtable': 0x5c08b8, 'dopLoader': 0xd2050, 'compuObjectLoader': 0xc8c70,
    'compuRegistration': 0x2140, 'compuFactory': 0xbc590, 'compuLoader': 0xbc810,
    'compuBaseObjectLoader': 0xbca70, 'compuBaseLoader': 0xbb8d0,
    'scalesObjectLoader': 0xbbe80, 'scalesLoader': 0xbee90,
    'scaleObjectLoader': 0xbf290, 'scaleLoader': 0xbe180,
    'coefficientsObjectLoader': 0xbe780, 'coefficientsLoader': 0xbd150,
    'doubleLoader': 0xbd400, 'limitLoader': 0x106ff0,
    'nativeValueLoader': 0xa4c00, 'nativeValueTypeSwitch': 0xa5680,
    'codedTypeObjectLoader': 0xc8fb0, 'physicalTypeObjectLoader': 0xc92f0,
    'physicalTypeLoader': 0x107e50, 'physicalPrecisionGetter': 0x107e00,
    'physicalRadixGetter': 0x107e30, 'shortNameGetter': 0x2cf90,
    'codedToPhysicalMapGetter': 0x4eab0, 'physicalToCodedMapGetter': 0xd27d0,
    'unitReferenceGetter': 0xd2870, 'internalConstraintGetter': 0xd2aa0,
    'physicalConstraintGetter': 0xd3560, 'unitRegistration': 0x196c0,
    'unitFactory': 0x2cc760, 'unitConstructor': 0x2cc790, 'unitLoader': 0x2cd730,
    'physicalDimensionRegistration': 0x181a0, 'physicalDimensionFactory': 0x2b25b0,
    'physicalDimensionConstructor': 0x2b25e0, 'physicalDimensionLoader': 0x2b34a0,
    'unitGroupsLoader': 0xfa130, 'unitGroupReferencesLoader': 0x103560,
    'codeInformationRegistration': 0x5040, 'codeInformationFactory': 0x118d70,
    'codeInformationConstructor': 0x118da0, 'codeInformationLoader': 0x119580,
    'codeInformationObjectLoader': 0xbc1c0, 'codeLibraryReferencesLoader': 0x1197d0,
    'codeLibraryReferenceLoader': 0xde200,
    'compuDirectionGetter': 0x2516b0,
    'physicalToCodedScalesGetter': 0x253710, 'codedToPhysicalScalesGetter': 0x2542b0,
    'physicalToCodedDefaultGetter': 0x253400, 'codedToPhysicalDefaultGetter': 0x253fa0,
    'compuCategoryGetter': 0xbc9c0, 'conversionStrategyRegister': 0x260660,
    'identicalStrategyRegistration': 0x14750, 'linearStrategyRegistration': 0x14780,
    'scaleLinearStrategyRegistration': 0x147e0, 'texttableStrategyRegistration': 0x14870,
    'codeStrategyRegistration': 0x14720, 'interpolatedStrategyRegistration': 0x14840,
    'rationalStrategyRegistration': 0x147b0, 'scaleRationalStrategyRegistration': 0x14810,
    'unitDisplayNameGetter': 0x2ccb20, 'unitFactorGetter': 0x2ccd40,
    'unitOffsetGetter': 0x2cce50, 'unitVirtualBaseTable': 0x60ccf0,
    'dimensionVirtualBaseTable': 0x5db0e8,
    'dimensionLengthGetter': 0x2b28a0, 'dimensionMassGetter': 0x2b29a0,
    'dimensionTimeGetter': 0x2b2aa0, 'dimensionCurrentGetter': 0x2b2ba0,
    'dimensionTemperatureGetter': 0x2b2ca0, 'dimensionMolarAmountGetter': 0x2b2da0,
    'dimensionLuminousIntensityGetter': 0x2b2ea0,
    'constraintRegistration': 0x4fb0, 'constraintFactory': 0x10f290,
    'constraintConstructor': 0x10f2c0, 'constraintLoader': 0x10f990,
    'constraintIntervalObjectLoader': 0x110700, 'constraintScalesObjectLoader': 0x110a40,
    'constraintIntervalRegistration': 0x14330, 'constraintIntervalFactory': 0x248c10,
    'constraintIntervalConstructor': 0x248ce0, 'constraintIntervalLoader': 0x249c60,
    'constraintLowerLimitGetter': 0x249d60, 'constraintUpperLimitGetter': 0x249f00,
    'constraintLowerIntervalGetter': 0x24a0a0, 'constraintUpperIntervalGetter': 0x24a220,
    'scaleConstraintRegistration': 0x14390, 'scaleConstraintFactory': 0x24bcb0,
    'scaleConstraintLoader': 0x24d020, 'scaleConstraintsRegistration': 0x143c0,
    'scaleConstraintsFactory': 0x24d5a0, 'scaleConstraintsLoader': 0x24dfc0,
}
SCALE_LIMIT = 4096
MAP_LIMIT = 4096
CONVERSION_STRATEGIES = {0: 'IdenticalConversion', 1: 'LinearConversion',
    2: 'ScaleLinearConversion', 3: 'TexttableConversion', 4: 'CompuCodeConversion',
    5: 'TabInterpolatedConversion', 6: 'RationalFunctionConversion',
    7: 'ScaleRationalFunctionConversion'}
COMMON_FIELDS = (('shortName', 'AStringData', 0x28), ('longName', 'UStringData', 0x24),
                 ('description', 'UStringData', 0x20), ('nativeField38', 'AStringData', 0x38),
                 ('nativeField34', 'AStringData', 0x34), ('nativeField30', 'AStringData', 0x30))


def definition_value(reader):
    start, native_type = reader.pos, reader.uint(1)
    row = {'recordOffset': start, 'nativeType': native_type,
           'nativeValueDefined': native_type not in (0, 255)}
    if native_type in (0, 255):
        # These select the loader's immediate return, without a value setter.
        row['rawHex'] = ''
    elif native_type in (1, 14):
        row['string'] = reader.string('AStringData' if native_type == 1 else 'UStringData')
    elif native_type in (2, 3):
        row['present'] = bool(reader.presence())
        if row['present']:
            row['rawHex'] = reader.take(reader.uint(2)).hex()
    elif native_type in (4, 5, 6, 7, 10, 11):
        # Widths verified from each switch arm; retain bytes instead of guessing
        # signedness/units or using these values as physical vehicle results.
        width = {4: 4, 5: 8, 6: 2, 7: 4, 10: 2, 11: 4}[native_type]
        row['rawHex'] = reader.take(width).hex()
    else:
        raise UnsupportedLayout('unsupported-compu-value-type-' + str(native_type))
    row['endOffset'] = reader.pos
    return row


def optional(reader, tag, loader):
    start = reader.pos
    if not reader.presence():
        return None
    actual = reader.uint(2)
    if actual != tag:
        raise UnsupportedLayout('unsupported-compu-object-' + hex(actual) + '-expected-' + hex(tag))
    result = loader(reader)
    return {'recordOffset': start, 'recordType': tag, **result, 'endOffset': reader.pos}


def rational_coefficients(reader):
    def vector():
        values = []
        for _ in range(reader.uint(1)):
            raw = reader.take(8)
            number = struct.unpack('<d', raw)[0]
            values.append({'rawHex': raw.hex(), 'value': number if math.isfinite(number) else None,
                           'finite': math.isfinite(number)})
        return values
    return {'numerator': vector(), 'denominator': vector(), 'physicalConversionQualified': False}


def limit(reader):
    return {'value': definition_value(reader), 'nativeInterval': 0x6d00 + reader.uint(1)}


def scale(reader):
    name = reader.string('AStringData')
    first = optional(reader, 0x14, rational_coefficients)
    second = optional(reader, 0x14, rational_coefficients)
    lower, upper = optional(reader, 0x37, limit), optional(reader, 0x37, limit)
    field88, fieldc8, fielda8 = definition_value(reader), definition_value(reader), definition_value(reader)
    lower_coded, upper_coded = optional(reader, 0x37, limit), optional(reader, 0x37, limit)
    return {'nativeField80': name, 'nativeField10': first, 'nativeField20': second,
        'lowerLimit': lower, 'upperLimit': upper, 'nativeField88': field88,
        'nativeFieldc8': fieldc8, 'nativeFielda8': fielda8,
        'lowerLimitCoded': lower_coded, 'upperLimitCoded': upper_coded}


def scales(reader):
    count = reader.uint(4)
    if count > SCALE_LIMIT:
        raise UnsupportedLayout('compu-scale-count-limit')
    return {'declaredScales': count, 'scales': [optional(reader, 0x19, scale) for _ in range(count)]}


def compu_base(reader):
    items = optional(reader, 0x1e, scales)
    default, code = definition_value(reader), definition_value(reader)
    info = optional(reader, 0x41, code_information)
    inverse = definition_value(reader)
    return {'scales': items, 'default': default, 'code': code, 'codeInformation': info, 'inverse': inverse}


def code_information(reader):
    # File names and entry points are data. They are never opened or executed.
    fields = {}
    for field in ('codeFile', 'nativeField24', 'syntax', 'revision', 'entryPoint'):
        fields[field] = reader.string('UStringData')
        if field in ('codeFile', 'syntax', 'revision') and fields[field].get('nullReference'):
            raise UnsupportedLayout('null-code-information-' + field)
    count, libraries, names = reader.uint(2), [], set()
    if count > MAP_LIMIT:
        raise UnsupportedLayout('code-library-reference-count-limit')
    for _ in range(count):
        name = reader.string('AStringData')
        if name.get('nullReference') or name['token'] in names:
            raise UnsupportedLayout('null-or-duplicate-code-library-name')
        names.add(name['token'])
        libraries.append({'name': name, 'reference': reader.named_reference()})
    return {'fields': fields, 'declaredLibraries': count, 'libraries': libraries,
            'codeContentVerified': False, 'targetExecuted': False, 'executionEnabled': False}


def compu_method(reader):
    category = reader.uint(1)
    if category not in CONVERSION_STRATEGIES:
        raise UnsupportedLayout('unsupported-compu-category-' + str(category))
    first, second = optional(reader, 5, compu_base), optional(reader, 5, compu_base)
    row = {'nativeCategory': category, 'nativeField18': first, 'nativeField28': second,
           'conversionStrategy': CONVERSION_STRATEGIES[category], 'strategyNameVerified': True,
           'directionFields': {'physicalToCoded': 'nativeField18', 'codedToPhysical': 'nativeField28'},
           'directionFieldNamesVerified': True,
           'categorySemanticsVerified': False, 'directionSemanticsVerified': False}
    if category == 3:
        # Source branches 0xbc8cd and 0xbc903 inspect inverse/default MCD values.
        if first and first['inverse']['nativeValueDefined']:
            row['nativeField38'] = reader.string('AStringData')
        if second and second['default']['nativeValueDefined']:
            row['nativeField3c'] = reader.string('AStringData')
    return row


def dop_compu_prefix(raw, tables):
    if len(raw) < 2 or len(raw) > RECORD_LIMIT:
        raise ValueError('dop-record-bounds')
    if int.from_bytes(raw[:2], 'little') != 0x2c:
        return None
    reader = Reader(raw, 2, tables)
    boundary = 2
    row = {'recordType': 0x2c, 'nativeObjectType': 'DbDopSimple',
        'compuPrefixLayoutComplete': False, 'dopBodyVerified': False,
        'physicalConversionQualified': False, 'ecuApplicabilityVerified': False,
        'executionEnabled': False, 'blockedReason': None}
    try:
        row['nativeField38'] = reader.string('AStringData')
        boundary = reader.pos
        row['compuMethod'] = optional(reader, 0xa, compu_method)
        boundary = reader.pos
        row['compuPrefixLayoutComplete'] = True
    except (ValueError, UnicodeError) as exc:
        row['blockedReason'] = str(exc)
    row.update({'endOffset': boundary, 'inspectedOffset': reader.pos,
        'prefixSha256': digest(raw[:boundary]), 'remainingBytes': len(raw) - boundary,
        'remainingRawHex': raw[boundary:].hex(), 'remainingSha256': digest(raw[boundary:])})
    return row


def finish_layout(row, raw, boundary, inspected):
    row.update({'endOffset': boundary, 'inspectedOffset': inspected,
        'layoutSha256': digest(raw[:boundary]), 'remainingBytes': len(raw) - boundary,
        'remainingRawHex': raw[boundary:].hex(), 'remainingSha256': digest(raw[boundary:]),
        'recordEnvelopeVerified': False})
    return row


def physical_type(reader):
    row = {'nativeBaseDataType': reader.uint(1), 'precisionPresent': bool(reader.presence())}
    if row['precisionPresent']:
        row['precision'] = reader.uint(2)
    row.update({'nativeDisplayRadix': reader.uint(1), 'typeSemanticsVerified': False})
    return row


def index_map(reader):
    count = reader.uint(2)
    if count > MAP_LIMIT:
        raise UnsupportedLayout('dop-index-map-count-limit')
    pairs, keys = [], set()
    for _ in range(count):
        key, value = reader.uint(4), reader.uint(2)
        # Preserve a conservative profile rather than guess native duplicate rules.
        if key in keys:
            raise UnsupportedLayout('duplicate-dop-index-map-key')
        keys.add(key)
        pairs.append({'key': key, 'value': value})
    return pairs


def optional_reference(reader):
    return reader.reference() if reader.presence() else None


def dop_definition_layout(raw, tables):
    """Loaded DOP fields only; unread record tail never implies qualification."""
    prefix = dop_compu_prefix(raw, tables)
    if prefix is None:
        return None
    row = {'recordType': 0x2c, 'nativeObjectType': 'DbDopSimple', 'compuPrefix': prefix,
        'fieldLayoutComplete': False, 'dopBodyVerified': False,
        'physicalConversionQualified': False, 'ecuApplicabilityVerified': False,
        'executionEnabled': False, 'blockedReason': prefix['blockedReason']}
    boundary = prefix['endOffset']
    reader = Reader(raw, boundary, tables)
    if not prefix['compuPrefixLayoutComplete']:
        return finish_layout(row, raw, boundary, prefix['inspectedOffset'])
    try:
        row['codedType'] = diag_coded_type(reader)
        boundary = reader.pos
        row['physicalType'] = optional(reader, 0x3c, physical_type)
        boundary = reader.pos
        row['physicalToCodedIndexMap'] = index_map(reader)
        boundary = reader.pos
        row['codedToPhysicalIndexMap'] = index_map(reader)
        boundary = reader.pos
        for field in ('unitReference', 'internalConstraintReference', 'physicalConstraintReference'):
            row[field] = optional_reference(reader)
            boundary = reader.pos
        row['fieldLayoutComplete'] = True
    except (ValueError, UnicodeError) as exc:
        row['blockedReason'] = str(exc)
    return finish_layout(row, raw, boundary, reader.pos)


def common_fields(reader):
    return [{'field': name, 'nativeDestinationOffset': destination, **reader.string(pool)}
            for name, pool, destination in COMMON_FIELDS]


def physical_dimension(reader):
    fields = common_fields(reader)
    raw = [reader.take(4) for _ in range(7)]
    names = ('length', 'mass', 'time', 'current', 'temperature', 'molarAmount', 'luminousIntensity')
    # Getter signed return types, interface adjustment and each loaded destination
    # are checked against this exact kernel. This is a unit definition, not a value.
    return {'fields': fields, 'nativeExponentFieldsRaw': [r.hex() for r in raw],
            'exponents': {name: int.from_bytes(value, 'little', signed=True) for name, value in zip(names, raw)},
            'exponentFieldNamesVerified': True, 'dimensionSemanticsVerified': False}


def double_definition(raw_hex):
    value = struct.unpack('<d', bytes.fromhex(raw_hex))[0]
    return {'rawHex': raw_hex, 'value': value if math.isfinite(value) else None,
            'finite': math.isfinite(value), 'physicalConversionQualified': False}


def unit_groups(reader):
    if not reader.presence():
        return None
    count = reader.uint(2)
    if count > MAP_LIMIT:
        raise UnsupportedLayout('unit-group-reference-count-limit')
    items = []
    for _ in range(count):
        name = reader.string('AStringData')
        if name.get('id') == 0:
            raise UnsupportedLayout('null-unit-group-name')
        items.append({'name': name, 'reference': reader.reference()})
    return {'declaredReferences': count, 'items': items}


def unit_definition_layout(raw, tables):
    if len(raw) < 2 or len(raw) > RECORD_LIMIT:
        raise ValueError('unit-record-bounds')
    if int.from_bytes(raw[:2], 'little') != 0x102:
        return None
    reader, boundary = Reader(raw, 2, tables), 2
    row = {'recordType': 0x102, 'nativeObjectType': 'MCDDbUnitImpl',
        'fieldLayoutComplete': False, 'unitSemanticsVerified': False,
        'physicalConversionQualified': False, 'ecuApplicabilityVerified': False,
        'executionEnabled': False, 'blockedReason': None}
    try:
        row['fields'] = common_fields(reader)
        boundary = reader.pos
        row['nativeField40'] = reader.string('UStringData')
        boundary = reader.pos
        row['nativeField48RawHex'] = reader.take(8).hex()
        boundary = reader.pos
        row['nativeField50RawHex'] = reader.take(8).hex()
        boundary = reader.pos
        row.update({'displayName': row['nativeField40'],
            'factorSItoUnit': double_definition(row['nativeField48RawHex']),
            'offsetSItoUnit': double_definition(row['nativeField50RawHex']),
            'loadedFieldNamesVerified': True})
        row['physicalDimension'] = optional(reader, 0x10c, physical_dimension)
        boundary = reader.pos
        row['unitGroups'] = unit_groups(reader)
        boundary = reader.pos
        row['fieldLayoutComplete'] = True
    except (ValueError, UnicodeError) as exc:
        row['blockedReason'] = str(exc)
    return finish_layout(row, raw, boundary, reader.pos)


def constraint_interval(reader):
    lower, upper = definition_value(reader), definition_value(reader)
    flags = reader.uint(1)
    # The source tests priority bits before the other boundary-selection bits.
    # Preserve native enum numbers; their labels/validation rules are unqualified.
    return {'lowerLimit': lower, 'upperLimit': upper, 'nativeLimitFlags': flags,
        'nativeLowerIntervalType': 0x6d03 if flags & 2 else 0x6d01 + int(bool(flags & 1)),
        'nativeUpperIntervalType': 0x6d03 if flags & 0x20 else 0x6d01 + int(bool(flags & 0x10)),
        'intervalSemanticsVerified': False}


def scale_constraint(reader):
    interval = optional(reader, 0xfb, constraint_interval)
    range_info = reader.uint(1)
    fields = [{'field': name, **reader.string(pool)} for name, pool in (
        ('description', 'UStringData'), ('descriptionId', 'AStringData'),
        ('shortLabel', 'AStringData'), ('shortLabelId', 'AStringData'))]
    return {'interval': interval, 'nativeRangeInfo': 0x600 + range_info,
            'fields': fields, 'nativeComputedRaw': reader.uint(1), 'rangeSemanticsVerified': False}


def scale_constraints(reader):
    count = reader.uint(4)
    if count > SCALE_LIMIT:
        raise UnsupportedLayout('constraint-scale-count-limit')
    return {'declaredScales': count,
            'scales': [optional(reader, 0x202, scale_constraint) for _ in range(count)]}


def constraint_definition_layout(raw, tables):
    if len(raw) < 2 or len(raw) > RECORD_LIMIT:
        raise ValueError('constraint-record-bounds')
    if int.from_bytes(raw[:2], 'little') != 0x203:
        return None
    reader, boundary = Reader(raw, 2, tables), 2
    row = {'recordType': 0x203, 'nativeObjectType': 'MCDConstraintImpl',
        'fieldLayoutComplete': False, 'constraintSemanticsVerified': False,
        'physicalConversionQualified': False, 'ecuApplicabilityVerified': False,
        'executionEnabled': False, 'blockedReason': None}
    try:
        row['interval'] = optional(reader, 0xfb, constraint_interval)
        boundary = reader.pos
        row['scaleConstraints'] = optional(reader, 0x201, scale_constraints)
        boundary = reader.pos
        row['nativeComputedRaw'] = reader.uint(1)
        boundary = reader.pos
        row['fieldLayoutComplete'] = True
    except (ValueError, UnicodeError) as exc:
        row['blockedReason'] = str(exc)
    return finish_layout(row, raw, boundary, reader.pos)
