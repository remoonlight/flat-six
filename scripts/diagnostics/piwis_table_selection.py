"""Direct table-key/index candidates over retained data, never ECU responses.

Unicode keys use exact text. UINT32 fixtures require an explicit C locale;
the native formatter uses a decimal stream whose runtime locale is unqualified.
Failed direct lookups do not attempt the native type-conversion fallback.
"""
from collections import Counter
import json
from pathlib import Path

from .piwis_database import REQUEST_KERNEL_SHA256
from .piwis_response_numeric import _source_identity, _sha256

SOURCE_EVIDENCE_RVAS = {
    'rowReferenceByKey': 0x236d00, 'keyAsUnicode': 0x8b7d0,
    'keyTypeDispatch': 0x8bcd8, 'uint32KeyGetter': 0x8baf5,
    'uint32KeyFormatter': 0x91690, 'streamFormatManipulator': 0x98640,
    'exactUnicodeMapComparison': 0xab030, 'directRowReferenceCopy': 0x236dbf,
    'nativeLookupFallback': 0x236dce, 'tableStructureByKey': 0x208c20,
}


def _key_text(key, fixture_locale):
    if not isinstance(key, dict) or type(key.get('nativeType')) is not int:
        raise ValueError('table-selection-key-type-invalid')
    value = key.get('value')
    if key['nativeType'] == 14:
        if type(value) is not str or len(value) > 4096:
            raise ValueError('table-selection-unicode-key-invalid')
        try:
            value.encode('utf-16-le')
        except UnicodeError as exc:
            raise ValueError('table-selection-unicode-key-invalid') from exc
        return value, 'unicode-exact'
    if key['nativeType'] == 11:
        if type(value) is not int or not 0 <= value <= 0xffffffff:
            raise ValueError('table-selection-uint32-key-invalid')
        if fixture_locale != 'C':
            raise ValueError('table-selection-uint32-fixture-locale-required')
        return str(value), 'uint32-decimal-C-fixture'
    raise ValueError('table-selection-key-type-unqualified')


def _row_identity(reference, row):
    if not isinstance(reference, dict):
        raise ValueError('table-selection-row-index-unresolved')
    lookup = reference.get('indexLookup') or {}
    target = lookup.get('target') or {}
    if lookup.get('indexTargetMatched') is not True or target.get('recordType') != 0xac:
        raise ValueError('table-selection-row-index-unresolved')
    if (not _source_identity(lookup.get('databaseSource')) or not _source_identity(lookup.get('keyIndexSource'))
            or not _sha256(target.get('recordSha256'))
            or any(type(target.get(k)) is not int or target[k] < minimum for k, minimum in
                (('sourceOffset', 0), ('compressedBytes', 1), ('decompressedBytes', 1), ('record', 0), ('objectId', 1)))):
        raise ValueError('table-selection-row-index-identity-invalid')
    obj, container = reference.get('nativeField08') or {}, reference.get('nativeField0c') or {}
    if (obj.get('storage') != 'table-reference' or obj.get('pool') != 'AStringData'
            or type(obj.get('id')) is not int or obj['id'] != target['objectId']
            or container.get('resolved') is not True or container.get('pool') != 'AStringData'
            or not container.get('text') or container['text'] != lookup.get('container')):
        raise ValueError('table-selection-row-reference-identity-mismatch')
    if (not isinstance(row, dict) or row.get('source') != lookup['databaseSource']
            or Path(row['source']['file']).stem != lookup['container']
            or Path(lookup['keyIndexSource']['file']).stem != lookup['container']
            or any(row.get(k) != target.get(k) for k in ('record', 'sourceOffset', 'compressedBytes', 'recordSha256'))):
        raise ValueError('table-selection-row-record-identity-mismatch')
    layout = row.get('layout') or {}
    if not isinstance(layout, dict):
        raise ValueError('table-selection-row-prefix-incomplete')
    if layout.get('recordType') != 0xac or layout.get('rowPrefixLayoutComplete') is not True:
        raise ValueError('table-selection-row-prefix-incomplete')
    return lookup, layout


def probe_table_row_selection(table, row, key, *, kernel_sha256, fixture_locale=None):
    result = {'ok': False, 'staticIndexTargetMatched': False, 'directKeyRuleVerified': False,
        'referenceResolutionVerified': False, 'tableBindingVerified': False,
        'keyParameterBindingVerified': False, 'runtimeLocaleVerified': False,
        'fallbackSelectionVerified': False, 'audienceAndPreconditionsVerified': False,
        'rowValueDecoded': False, 'responseEnvelopeVerified': False,
        'serviceResponseMatchingVerified': False, 'physicalConversionQualified': False,
        'ecuApplicabilityVerified': False, 'actualVehicleResponse': False, 'executionEnabled': False}
    try:
        if kernel_sha256 != REQUEST_KERNEL_SHA256:
            raise ValueError('table-selection-kernel-profile-mismatch')
        if not isinstance(table, dict) or not _source_identity(table.get('source')) or not _sha256(table.get('recordSha256')):
            raise ValueError('table-selection-table-source-invalid')
        layout = table.get('layout') or {}
        if not isinstance(layout, dict):
            raise ValueError('table-selection-table-layout-incomplete')
        items, count = layout.get('keyMap'), layout.get('declaredKeys')
        if (layout.get('recordType') != 0xab or layout.get('fieldLayoutComplete') is not True
                or type(count) is not int or not 0 <= count <= 4096
                or not isinstance(items, list) or len(items) != count):
            raise ValueError('table-selection-table-layout-incomplete')
        text, kind = _key_text(key, fixture_locale)
        seen, selected = set(), None
        for index, item in enumerate(items):
            if not isinstance(item, dict) or not isinstance(item.get('key'), dict):
                raise ValueError('table-selection-table-key-ambiguous')
            field = item.get('key') or {}
            value = field.get('text')
            if (field.get('pool') != 'UStringData' or field.get('resolved') is not True
                    or field.get('nullReference') is not False or type(value) is not str or value in seen):
                raise ValueError('table-selection-table-key-ambiguous')
            seen.add(value)
            if value == text:
                selected = (index, item)
        if selected is None:
            raise ValueError('table-selection-direct-key-unmapped-fallback-unqualified')
        index, item = selected
        lookup, row_layout = _row_identity(item.get('reference') or {}, row)
        field = row_layout.get('key') or {}
        if (not isinstance(field, dict) or field.get('pool') != 'UStringData' or field.get('resolved') is not True
                or field.get('nullReference') is not False or field.get('text') != text):
            raise ValueError('table-selection-row-key-mismatch')
        result.update({'ok': True, 'reason': None, 'staticIndexTargetMatched': True,
            'directKeyRuleVerified': True, 'probeKind': kind, 'candidateKeyText': text,
            'selectedMapIndex': index, 'selectedKeySource': item['key'],
            'selectedRowTarget': lookup['target'], 'fixtureLocale': fixture_locale,
            'bindingScope': 'exact-retained-table-key-index-candidate'})
    except ValueError as exc:
        result['reason'] = str(exc)
    return result


def write_table_selection_probes(layout_path, output_path, *, kernel_sha256):
    layouts, tables, counts = {}, [], Counter()
    with layout_path.open(encoding='utf8') as stream:
        for line in stream:
            row = json.loads(line); identity = (row['source']['file'], row['sourceOffset'])
            if identity in layouts:
                raise ValueError('table-selection-duplicate-layout-record')
            layouts[identity] = row
            if row['layout']['recordType'] == 0xab:
                tables.append(row)
    with output_path.open('x', encoding='utf8') as output:
        for table in tables:
            counts['tables'] += 1
            items = table['layout'].get('keyMap') or []
            for index, item in enumerate(items):
                lookup = (item.get('reference') or {}).get('indexLookup') or {}
                row = layouts.get(((lookup.get('databaseSource') or {}).get('file'),
                    (lookup.get('target') or {}).get('sourceOffset')))
                text = item['key'].get('text')
                keys = [({'nativeType': 14, 'value': text}, None)]
                if (isinstance(text, str) and text.isascii() and text.isdecimal()
                        and len(text) <= 10 and 0 <= int(text) <= 0xffffffff and str(int(text)) == text):
                    keys.append(({'nativeType': 11, 'value': int(text)}, 'C'))
                probes = [{'keyFixture': key, 'fixtureLocale': locale,
                    'fixtureOrigin': 'synthetic-typed-table-key', 'actualVehicleResponse': False,
                    'result': probe_table_row_selection(table, row, key, kernel_sha256=kernel_sha256,
                        fixture_locale=locale)} for key, locale in keys]
                counts['keyPaths'] += 1
                for p in probes:
                    r = p['result']; counts['case:' + (r['probeKind'] if r['ok'] else r['reason'])] += 1
                output.write(json.dumps({'tableSource': table['source'], 'tableSourceOffset': table['sourceOffset'],
                    'tableRecordSha256': table['recordSha256'], 'keyMapIndex': index,
                    'rowSource': row['source'] if row else None, 'rowSourceOffset': row['sourceOffset'] if row else None,
                    'rowRecordSha256': row['recordSha256'] if row else None, 'probes': probes,
                    'actualVehicleResponse': False, 'executionEnabled': False}, ensure_ascii=True) + '\n')
            texts = {i['key'].get('text') for i in items}
            missing = '\u241fsynthetic-unmapped-table-key'
            while missing in texts:
                missing += '_'
            key = {'nativeType': 14, 'value': missing}
            rejected = probe_table_row_selection(table, None, key, kernel_sha256=kernel_sha256)
            counts['case:' + rejected['reason']] += 1
            output.write(json.dumps({'tableSource': table['source'], 'tableSourceOffset': table['sourceOffset'],
                'tableRecordSha256': table['recordSha256'], 'keyMapIndex': None,
                'probes': [{'keyFixture': key, 'fixtureOrigin': 'synthetic-unmapped-table-key',
                    'actualVehicleResponse': False, 'result': rejected}],
                'actualVehicleResponse': False, 'executionEnabled': False}, ensure_ascii=True) + '\n')
    return dict(counts)
