"""Pinned unit-group fields and explicit offline group-selection candidates."""
from collections import Counter
import json

from .piwis_database import RECORD_LIMIT, REQUEST_KERNEL_SHA256
from .piwis_parameters import Reader, UnsupportedLayout
from .piwis_data_objects import common_fields, finish_layout
from .piwis_response_numeric import _retained_reference_target
from .piwis_units import _row_identity, unit_profile

GROUP_LIMIT = 4096  # Offline resource guard, not a claimed native count limit.
SOURCE_EVIDENCE_RVAS = {
    'unitGroupRegistration': 0x141e0, 'unitGroupFactory': 0x240760,
    'unitGroupConstructor': 0x240790,
    'unitGroupLoader': 0x240e40, 'unitGroupCategoryLoad': 0x240f2a,
    'unitGroupCategoryStore': 0x240fc2, 'unitGroupCategoryGetter': 0x240b7c,
    'unitGroupUnitsLoad': 0x241039, 'unitCollectionLoader': 0xf9c00,
    'unitCollectionStreamLoader': 0x1032b0, 'unitCollectionCount': 0x103344,
    'unitCollectionName': 0x1033f6, 'unitReferenceLoad': 0x10346c,
    'unitCollectionAppend': 0x103488, 'unitCollectionIteration': 0x1034f0,
    'unitGroupSelection': 0x280680, 'unitGroupSourceDimension': 0x2807d4,
    'unitGroupUnitByIndex': 0x28086b, 'unitGroupSkipMissingDimension': 0x2808f7,
    'unitGroupDimensionCompare': 0x2809f8, 'unitGroupMatch': 0x2809ff,
    'unitGroupNext': 0x280a4d, 'unitGroupFirstMatchedOutput': 0x280a7b,
    'unitGroupNoMatchRequired': 0x280b96,
}


def unit_group_definition_layout(raw, tables):
    if len(raw) < 2 or len(raw) > RECORD_LIMIT:
        raise ValueError('unit-group-record-bounds')
    if int.from_bytes(raw[:2], 'little') != 0x104:
        return None
    reader, boundary = Reader(raw, 2, tables), 2
    row = {'recordType': 0x104, 'nativeObjectType': 'MCDDbUnitGroupImpl',
        'fieldLayoutComplete': False, 'unitGroupSemanticsVerified': False,
        'runtimeUnitSelectionVerified': False, 'executionEnabled': False, 'blockedReason': None}
    try:
        row['fields'] = common_fields(reader); boundary = reader.pos
        row['nativeCategoryRaw'] = reader.uint(2); boundary = reader.pos
        count = reader.uint(2)
        if count > GROUP_LIMIT:
            raise UnsupportedLayout('unit-group-reference-count-limit')
        items = []
        for _ in range(count):
            name = reader.string('AStringData')
            # Names are retained separately; selection iterates references.
            items.append({'name': name, 'reference': reader.reference()})
        row['unitReferences'] = {'declaredReferences': count, 'items': items}
        boundary = reader.pos
        row['fieldLayoutComplete'] = True
    except (ValueError, UnicodeError) as exc:
        row['blockedReason'] = str(exc)
    return finish_layout(row, raw, boundary, reader.pos)


def probe_unit_group_selection(source_row, group_row, unit_rows, *, kernel_sha256, require_match=False):
    """Explicit supplied group only; never infer the logical link's chosen group."""
    result = {'ok': False, 'runtimeUnitSelectionVerified': False,
        'runtimeUnitIdentityVerified': False, 'unitGroupSemanticsVerified': False,
        'physicalConversionQualified': False, 'ecuApplicabilityVerified': False,
        'executionEnabled': False, 'actualVehicleResponse': False}
    try:
        if kernel_sha256 != REQUEST_KERNEL_SHA256:
            raise ValueError('unit-group-kernel-profile-mismatch')
        if type(require_match) is not bool:
            raise ValueError('unit-group-require-match-flag-invalid')
        if not isinstance(unit_rows, dict):
            raise ValueError('unit-group-unit-record-index-invalid')
        _row_identity(group_row)
        layout = group_row.get('layout')
        if (not isinstance(layout, dict) or layout.get('recordType') != 0x104
                or layout.get('fieldLayoutComplete') is not True):
            raise ValueError('unit-group-layout-unqualified')
        refs = layout.get('unitReferences')
        if (not isinstance(refs, dict) or type(refs.get('declaredReferences')) is not int
                or not 0 <= refs['declaredReferences'] <= GROUP_LIMIT
                or not isinstance(refs.get('items'), list) or refs['declaredReferences'] != len(refs['items'])):
            raise ValueError('unit-group-reference-collection-invalid')
        source = unit_profile(source_row, kernel_sha256=kernel_sha256)
        examined = []
        if source['exponents'] is not None:
            if source['dimensionShortName'] is None:
                raise ValueError('unit-group-dimension-name-unqualified')
            for index, item in enumerate(refs['items']):
                if not isinstance(item, dict) or not isinstance(item.get('reference'), dict):
                    raise ValueError('unit-group-unit-reference-invalid')
                reference = item['reference']; lookup = reference.get('indexLookup') or {}
                target = lookup.get('target') or {}
                row = unit_rows.get(((lookup.get('databaseSource') or {}).get('file'), target.get('sourceOffset')))
                _retained_reference_target(reference, row, expected_record_type=0x102)
                candidate = unit_profile(row, kernel_sha256=kernel_sha256)
                examined.append(index)
                if candidate['exponents'] is None:
                    continue
                if candidate['dimensionShortName'] is None:
                    raise ValueError('unit-group-dimension-name-unqualified')
                if (candidate['dimensionShortName'] == source['dimensionShortName']
                        and candidate['exponents'] == source['exponents']):
                    result.update(ok=True, reason=None, selectionKind='first-matching-dimension',
                        matchedIndex=index, selectedUnitRecord={k: row[k] for k in
                            ('source', 'sourceOffset', 'record', 'compressedBytes', 'recordSha256')},
                        selectedDisplayName=candidate['displayName'], examinedIndexes=examined,
                        explicitGroupProvided=True, requireMatch=require_match)
                    return result
        if require_match:
            raise ValueError('unit-group-source-dimension-missing' if source['exponents'] is None else 'unit-group-no-matching-unit')
        result.update(ok=True, reason=None, selectionKind='no-selected-unit', matchedIndex=None,
            selectedUnitRecord=None, selectedDisplayName=None, examinedIndexes=examined,
            explicitGroupProvided=True, requireMatch=False)
    except ValueError as exc:
        result['reason'] = str(exc)
    return result


def write_unit_group_selections(layout_path, output_path, *, kernel_sha256):
    rows = {}
    with layout_path.open(encoding='utf8') as stream:
        for line in stream:
            row = json.loads(line); key = (row['source']['file'], row['sourceOffset'])
            if key in rows:
                raise ValueError('unit-group-duplicate-layout-record')
            rows[key] = row
    units = [r for r in rows.values() if r['layout']['recordType'] == 0x102]
    groups = [r for r in rows.values() if r['layout']['recordType'] == 0x104]
    if len(units)*len(groups) > 65536:
        raise ValueError('unit-group-selection-count-offline-limit')
    counts = Counter(unitGroupRecords=len(groups), sourceUnitRecords=len(units))
    with output_path.open('x', encoding='utf8') as output:
        for group in groups:
            for source in units:
                probes = [{'requireMatch': required, 'result': probe_unit_group_selection(source, group, rows,
                    kernel_sha256=kernel_sha256, require_match=required)} for required in (False, True)]
                counts['explicitUnitGroupPairs'] += 1
                for p in probes:
                    r = p['result']; counts[r['selectionKind'] if r['ok'] else r['reason']] += 1
                output.write(json.dumps({'sourceUnitRecord': source, 'unitGroupRecord': group,
                    'fixtureOrigin': 'synthetic-explicit-unit-group', 'runtimeUnitSelectionVerified': False,
                    'executionEnabled': False, 'actualVehicleResponse': False, 'probes': probes}, ensure_ascii=True)+'\n')
    return dict(counts)
