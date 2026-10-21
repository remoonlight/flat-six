"""Bounded, data-only audit of retained MCD string tables and zlib record framing.

Sliding string ID matches are candidates. An optional hash-pinned native profile
decodes request headers and, when requested, request/response parameter prefixes.
Complete request bodies, response decoding and ECU applicability remain unknown.
This tool does not produce executable diagnostic definitions or load vendor code.
"""
import argparse
from collections import Counter
from contextlib import ExitStack
import gzip
import hashlib
from io import BytesIO
import json
from pathlib import Path
import struct
import zlib

FILE_LIMIT = 32 * 1024 * 1024
TABLE_LIMIT = 128 * 1024 * 1024
RECORD_LIMIT = 1024 * 1024
# Static source evidence: request factory tag at RVA 0x11fa0, constructor
# 0x215480/0x2154b0 and load routine 0x2159e0 in this exact retained kernel.
# The prefix consumes A,U,U,A,A,A string fields; the last three roles are unknown.
REQUEST_KERNEL_SHA256 = '7806530ea209f864dd4db2b6769e93bcd657e1acc840852b292310c2cf781d8a'
REQUEST_FIELDS = (
    ('shortName', 'AStringData', 0x28), ('longName', 'UStringData', 0x24),
    ('description', 'UStringData', 0x20), ('nativeField38', 'AStringData', 0x38),
    ('nativeField34', 'AStringData', 0x34), ('nativeField30', 'AStringData', 0x30),
)


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def string_table(index, data, unicode=False):
    if len(index) > TABLE_LIMIT or len(data) > TABLE_LIMIT or len(index) < 4:
        raise ValueError('string-table-size-or-header')
    count = struct.unpack_from('<I', index)[0]
    if count > 1_200_000 or len(index) != 4 + count * 8:
        raise ValueError('string-index-count-or-length')
    result, pos = {}, 0
    width = 2 if unicode else 1
    for n in range(count):
        offset, key = struct.unpack_from('<II', index, 4 + n * 8)
        if offset != pos or pos + 4 > len(data) or key in result:
            raise ValueError('string-index-offset-or-duplicate-id')
        size = struct.unpack_from('<I', data, pos)[0]
        end = pos + 4 + size * width
        if size > RECORD_LIMIT or end > len(data):
            raise ValueError('string-value-size-or-length')
        raw = data[pos + 4:end]
        if unicode:
            text, encoding = raw.decode('utf-16-le'), 'utf-16-le'
        elif raw.isascii():
            text, encoding = raw.decode('ascii'), 'ascii'
        else:
            text, encoding = None, 'unverified-single-byte-encoding'
        result[key] = {'offset': pos, 'units': size, 'text': text, 'encoding': encoding}
        if text is None:
            result[key]['rawHex'] = raw.hex()
        pos = end
    if pos != len(data):
        raise ValueError('unconsumed-string-table')
    return result


def records(raw, record_limit=RECORD_LIMIT, total_limit=64 * 1024 * 1024):
    """Each accepted frame must end with a valid zlib checksum; no resync/skip."""
    if not raw or len(raw) > FILE_LIMIT:
        raise ValueError('empty-or-oversized-compiled-database')
    pos, total, count = 0, 0, 0
    while pos < len(raw):
        start, parts, size = pos, [], 0
        stream = zlib.decompressobj()
        while not stream.eof:
            chunk = memoryview(raw)[pos:pos + 4096]
            if not chunk:
                raise ValueError('truncated-zlib-record')
            try:
                part = stream.decompress(chunk, record_limit + 1 - size)
            except zlib.error as exc:
                raise ValueError('invalid-zlib-record-at-' + str(start)) from exc
            size += len(part)
            used = len(chunk) - len(stream.unconsumed_tail) - len(stream.unused_data)
            if size > record_limit or used <= 0:
                raise ValueError('zlib-record-size-or-progress-limit')
            parts.append(part)
            pos += used
        total += size
        count += 1
        if total > total_limit or count > 100_000:
            raise ValueError('compiled-database-total-limit')
        yield {'sourceOffset': start, 'compressedBytes': pos - start, 'data': b''.join(parts)}


def candidates(raw, pools):
    # Sliding matches intentionally remain untyped, including cross-pool overlaps.
    found = []
    for offset in range(len(raw) - 3):
        key = int.from_bytes(raw[offset:offset + 4], 'little')
        for pool, table in pools.items():
            if key in table:
                found.append({'recordOffset': offset, 'id': key, 'pool': pool, **table[key]})
    return found


def serialized_string(raw, pos, pool, tables):
    """A source-proven loader selects one pool; never fall back across pools."""
    if pool not in ('AStringData', 'UStringData') or pool not in tables:
        raise ValueError('request-header-string-pool')
    if pos < 0 or pos + 4 > len(raw):
        raise ValueError('truncated-request-header-string')
    token = struct.unpack_from('<I', raw, pos)[0]
    row = {'recordOffset': pos, 'token': token, 'pool': pool}
    pos += 4
    if token & 0x80000000:
        units, width = token & 0x7fffffff, 2 if pool == 'UStringData' else 1
        end = pos + units * width
        if units > RECORD_LIMIT or end > len(raw):
            raise ValueError('request-header-inline-string-length')
        data = raw[pos:end]
        row.update({'storage': 'inline', 'units': units, 'text': None})
        if width == 2:
            row.update({'text': data.decode('utf-16-le'), 'encoding': 'utf-16-le'})
        elif data.isascii():
            row.update({'text': data.decode('ascii'), 'encoding': 'ascii'})
        else:
            row.update({'encoding': 'unverified-single-byte-encoding', 'rawHex': data.hex()})
        row['resolved'] = row['text'] is not None
        return row, end
    value = tables[pool].get(token) if token else None
    row.update({'storage': 'table-reference', 'id': token, 'text': None,
                'resolved': False, 'nullReference': token == 0})
    if value is not None:
        row['stringTableValue'] = dict(value)
        row['text'] = value['text']
        row['resolved'] = value['text'] is not None
    elif token:
        row['unresolvedReason'] = 'id-not-in-selected-pool'
    return row, pos


def request_header(raw, tables):
    """Exact prefix for the pinned kernel profile; never consume the body."""
    return named_record_header(raw, tables, 0x78, 'MCDDbRequestImpl', 'request')


def response_header(raw, tables):
    """The response loader uses the same six named fields in this exact kernel."""
    return named_record_header(raw, tables, 0x91, 'MCDDbResponseImpl', 'response')


def named_record_header(raw, tables, tag, native_type, kind):
    if len(raw) < 2:
        raise ValueError('truncated-native-record-type')
    if struct.unpack_from('<H', raw)[0] != tag:
        return None
    pos, fields = 2, []
    for name, pool, destination in REQUEST_FIELDS:
        field, pos = serialized_string(raw, pos, pool, tables)
        fields.append({'field': name, 'nativeDestinationOffset': destination, **field})
    return {'recordType': tag, 'nativeObjectType': native_type,
        'nativeKernelSha256': REQUEST_KERNEL_SHA256, 'headerBytes': pos, 'fields': fields,
        'remainingBytes': len(raw) - pos, 'remainingSha256': digest(raw[pos:]),
        'headerLayoutProfileMatched': True, kind + 'BodyVerified': False,
        'ecuApplicabilityVerified': False, 'executionEnabled': False}


def source(path):
    path = Path(path)
    if path.stat().st_size > FILE_LIMIT:
        raise ValueError('database-source-file-limit')
    raw = path.read_bytes()
    if len(raw) > FILE_LIMIT:
        raise ValueError('database-source-file-limit')
    return raw, {'file': str(path.resolve()), 'bytes': len(raw), 'sha256': digest(raw)}


def inflated(raw):
    with gzip.GzipFile(fileobj=BytesIO(raw)) as stream:
        result = stream.read(TABLE_LIMIT + 1)
        if len(result) > TABLE_LIMIT:
            raise ValueError('gzip-table-expansion-limit')
    return result


def audit(databases, table_paths, output, native_kernel=None, parse_request_parameters=False, parse_response_parameters=False, key_indexes=None, parse_data_objects=False, parse_data_layouts=False, numeric_probes=False, response_numeric_probes=False, table_selection_probes=False, unit_probes=False, constraint_probes=False, constraint_flow_probes=False, ecu_config_mappings=False):
    if ecu_config_mappings and key_indexes is None:
        raise ValueError('ecu-config-mappings-require-key-indexes')
    if constraint_flow_probes and (not parse_data_layouts or key_indexes is None):
        raise ValueError('constraint-flow-probes-require-layouts-key-indexes')
    if constraint_probes and (not parse_data_layouts or key_indexes is None):
        raise ValueError('constraint-probes-require-layouts-key-indexes')
    if unit_probes and (not parse_data_layouts or key_indexes is None):
        raise ValueError('unit-probes-require-layouts-key-indexes')
    if table_selection_probes and (not parse_data_layouts or key_indexes is None):
        raise ValueError('table-selection-probes-require-layouts-key-indexes')
    if response_numeric_probes and (not parse_response_parameters or not parse_data_layouts or key_indexes is None):
        raise ValueError('response-numeric-probes-require-response-parameters-layouts-key-indexes')
    databases = list(databases)
    if numeric_probes:
        if not parse_data_layouts:
            raise ValueError('numeric-probes-require-data-object-layouts')
        from .piwis_numeric import synthetic_dop_probes, SOURCE_EVIDENCE_RVAS as NUMERIC_RVAS
    numeric_counts = Counter()
    if key_indexes is not None:
        key_indexes = list(key_indexes)
        if native_kernel is None:
            raise ValueError('key-index-audit-requires-pinned-native-kernel')
        if not databases or len(key_indexes) != len(databases):
            raise ValueError('key-index-and-database-count-mismatch')
        from .piwis_key_index import key_index, bind_frames, reference_index_target
    if parse_data_objects:
        if native_kernel is None:
            raise ValueError('data-object-prefixes-require-pinned-native-kernel')
        from .piwis_data_objects import dop_compu_prefix
    if parse_data_layouts:
        if native_kernel is None:
            raise ValueError('data-object-layouts-require-pinned-native-kernel')
        from .piwis_data_objects import dop_definition_layout, unit_definition_layout, constraint_definition_layout
        from .piwis_unit_groups import unit_group_definition_layout, SOURCE_EVIDENCE_RVAS as GROUP_RVAS_FOR_LAYOUT
        from .piwis_tables import definition_layout as table_structure_layout, layout_references
    if (parse_request_parameters or parse_response_parameters) and native_kernel is None:
        raise ValueError('request-parameters-require-pinned-native-kernel' if parse_request_parameters else 'response-parameters-require-pinned-native-kernel')
    if parse_request_parameters or parse_response_parameters:
        from .piwis_parameters import SOURCE_EVIDENCE_RVAS, request_parameters, response_parameters
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    pools, tables, original_sources = {}, [], []
    kernel_source = None
    if native_kernel is not None:
        _, kernel_source = source(native_kernel)
        if kernel_source['sha256'] != REQUEST_KERNEL_SHA256:
            raise ValueError('unsupported-native-request-header-profile')
        original_sources.append(kernel_source)
    for name, index_path, data_path, unicode in table_paths:
        if name in pools:
            raise ValueError('duplicate-string-pool')
        index_raw, index_source = source(index_path)
        data_raw, data_source = source(data_path)
        pools[name] = string_table(inflated(index_raw), inflated(data_raw), unicode)
        tables.append({'pool': name, 'strings': len(pools[name]), 'indexSource': index_source, 'dataSource': data_source,
                       'unverifiedEncodingStrings': sum(row['text'] is None for row in pools[name].values())})
        original_sources.extend((index_source, data_source))
    counts = Counter(key for table in pools.values() for key in table)
    containers, indexed_sources = {}, {}
    if key_indexes is not None:
        for db_path, key_path in zip(databases, key_indexes):
            if Path(db_path).suffix != '.db' or Path(key_path).suffix != '.key' or Path(db_path).stem != Path(key_path).stem:
                raise ValueError('key-index-and-database-name-mismatch')
            container_name = Path(db_path).stem
            if container_name in containers:
                raise ValueError('duplicate-retained-index-container')
            db_raw, db_source = source(db_path)
            key_raw, key_source = source(key_path)
            index = key_index(key_raw)
            frames = []
            for number, frame in enumerate(records(db_raw)):
                data = frame.pop('data')
                if len(data) < 2:
                    raise ValueError('indexed-record-type-truncated')
                frames.append({**frame, 'record': number, 'decompressedBytes': len(data),
                               'recordSha256': digest(data), 'recordType': int.from_bytes(data[:2], 'little')})
            objects = bind_frames(index, frames)
            containers[container_name] = {'objects': objects, 'databaseSource': db_source, 'keyIndexSource': key_source}
            indexed_sources[str(Path(db_path).resolve())] = {**index, 'source': key_source, 'databaseSource': db_source,
                'objects': objects, 'referenceMatches': Counter(), 'unresolvedReferences': Counter()}
            original_sources.extend((db_source, key_source))
    databases_summary = []
    with ExitStack() as stack:
        target = stack.enter_context((output / 'record-string-candidates.jsonl').open('x', encoding='utf8'))
        headers = stack.enter_context((output / 'request-record-headers.jsonl').open('x', encoding='utf8')) if kernel_source else None
        parameters = stack.enter_context((output / 'request-parameter-prefixes.jsonl').open('x', encoding='utf8')) if parse_request_parameters else None
        responses = stack.enter_context((output / 'response-parameter-prefixes.jsonl').open('x', encoding='utf8')) if parse_response_parameters else None
        index_targets = stack.enter_context((output / 'indexed-records.jsonl').open('x', encoding='utf8')) if key_indexes is not None else None
        dop_prefixes = stack.enter_context((output / 'dop-compu-prefixes.jsonl').open('x', encoding='utf8')) if parse_data_objects else None
        data_layouts = stack.enter_context((output / 'data-object-layouts.jsonl').open('x', encoding='utf8')) if parse_data_layouts else None
        probe_output = stack.enter_context((output / 'numeric-probes.jsonl').open('x', encoding='utf8')) if numeric_probes else None
        config_mappings = stack.enter_context((output / 'ecu-config-mappings.jsonl').open('x', encoding='utf8')) if ecu_config_mappings else None
        config_data_refs = stack.enter_context((output / 'ecu-config-data-reference-prefixes.jsonl').open('x', encoding='utf8')) if ecu_config_mappings else None
        config_record_refs = stack.enter_context((output / 'ecu-config-record-reference-prefixes.jsonl').open('x', encoding='utf8')) if ecu_config_mappings else None
        config_record_cores = stack.enter_context((output / 'ecu-config-data-record-core-prefixes.jsonl').open('x', encoding='utf8')) if ecu_config_mappings else None
        config_record_bodies = stack.enter_context((output / 'ecu-config-data-record-body-prefixes.jsonl').open('x', encoding='utf8')) if ecu_config_mappings else None
        config_record_core_counts = Counter()
        config_record_body_counts = Counter()
        for path in databases:
            raw, src = source(path)
            original_sources.append(src)
            indexed = indexed_sources.get(src['file'])
            if indexed is not None:
                if src != indexed['databaseSource']:
                    raise ValueError('database-source-changed-during-index-audit')
                for target_row in indexed['objects'].values():
                    index_targets.write(json.dumps({'source': src, 'keyIndexSource': indexed['source'],
                        **target_row, 'indexedRecordVerified': True, 'recordSemanticsVerified': False,
                        'executionEnabled': False}, ensure_ascii=True) + '\n')

            def add_index_targets(body):
                if indexed is None:
                    return
                for parameter in body['parsedParameters']:
                    for kind in ('objectReference', 'tableReference'):
                        if kind not in parameter:
                            continue
                        match = reference_index_target(parameter[kind], containers)
                        parameter[kind]['indexLookup'] = match
                        if match['indexTargetMatched']:
                            indexed['referenceMatches'][str(match['target']['recordType'])] += 1
                        else:
                            indexed['unresolvedReferences'][match['unresolvedReason']] += 1
            count = size = matched = references = header_count = 0
            parameter_count = collection_count = 0
            blocked_parameters = Counter()
            response_count = response_parameter_count = response_collection_count = 0
            blocked_responses = Counter()
            dop_count = compu_count = 0
            blocked_dop = Counter()
            layout_counts, layout_complete, layout_blocked, layout_refs = Counter(), Counter(), Counter(), Counter()
            row_prefix_count = 0
            for row in records(raw):
                data = row.pop('data')
                if config_mappings is not None:
                    from .piwis_ecu_config import ecu_config_mapping_layout, config_data_record_reference_prefix, config_record_data_reference_prefix, data_record_core_prefix, data_record_body_prefix
                    config_layout = ecu_config_mapping_layout(data, pools, kernel_sha256=kernel_source['sha256'])
                    if config_layout is not None:
                        config_mappings.write(json.dumps({'source': src, 'record': count,
                            **row, 'recordSha256': digest(data), 'layout': config_layout,
                            'actualVehicleOriginalCoding': False, 'executionEnabled': False}, ensure_ascii=True) + '\n')
                    data_reference_prefix = config_data_record_reference_prefix(data, pools, kernel_sha256=kernel_source['sha256'])
                    if data_reference_prefix is not None:
                        config_data_refs.write(json.dumps({'source': src, 'record': count,
                            **row, 'recordSha256': digest(data), 'layout': data_reference_prefix,
                            'actualVehicleOriginalCoding': False, 'executionEnabled': False}, ensure_ascii=True) + '\n')
                    record_reference_prefix = config_record_data_reference_prefix(data, pools, kernel_sha256=kernel_source['sha256'])
                    if record_reference_prefix is not None:
                        config_record_refs.write(json.dumps({'source': src, 'record': count,
                            **row, 'recordSha256': digest(data), 'layout': record_reference_prefix,
                            'actualVehicleOriginalCoding': False, 'executionEnabled': False}, ensure_ascii=True) + '\n')
                    record_core = data_record_core_prefix(data, pools, kernel_sha256=kernel_source['sha256'])
                    if record_core is not None:
                        config_record_cores.write(json.dumps({'source': src, 'record': count,
                            **row, 'recordSha256': digest(data), 'layout': record_core,
                            'actualVehicleOriginalCoding': False, 'executionEnabled': False}, ensure_ascii=True) + '\n')
                        config_record_core_counts['dataRecords'] += 1
                        if record_core['prefixLayoutComplete']:
                            config_record_core_counts['dataRecordsValidated'] += 1
                            config_record_core_counts['bytePresence:' + str(record_core['byteField']['presence'])] += 1
                            config_record_core_counts['nativeDataFormat:' + hex(record_core['dataFormat']['nativeU16'])] += 1
                            config_record_core_counts['staticDataIdType:' + str(record_core['dataId']['nativeType'])] += 1
                            config_record_core_counts['codingPresence:' + str(record_core['codingDataPresence'])] += 1
                            if record_core['codingDataPresence']:
                                config_record_core_counts['lateBound:' + str(record_core['codingData']['isLateBound'])] += 1
                        else:
                            config_record_core_counts['blocked:' + record_core['unsupportedReason']] += 1
                    record_body = data_record_body_prefix(data, pools, kernel_sha256=kernel_source['sha256'])
                    if record_body is not None:
                        config_record_bodies.write(json.dumps({'source': src, 'record': count,
                            **row, 'recordSha256': digest(data), 'layout': record_body,
                            'actualVehicleOriginalCoding': False, 'executionEnabled': False}, ensure_ascii=True) + '\n')
                        config_record_body_counts['dataRecords'] += 1
                        if record_body['loaderBodyLayoutComplete']:
                            config_record_body_counts['loaderBodiesValidated'] += 1
                            config_record_body_counts['remainingBytes:' + str(record_body['remainingBytes'])] += 1
                        else:
                            config_record_body_counts['blocked:' + record_body['unsupportedReason']] += 1
                refs = candidates(data, pools)
                count += 1
                size += len(data)
                references += len(refs)
                if references > 1_000_000:
                    raise ValueError('database-string-reference-limit')
                if refs:
                    matched += 1
                    target.write(json.dumps({'source': src, 'record': count - 1, **row,
                        'decompressedBytes': len(data), 'recordSha256': digest(data),
                        'candidateStringRefs': refs, 'recordSemanticsVerified': False}, ensure_ascii=True) + '\n')
                if headers is not None:
                    header = request_header(data, pools)
                    if header is not None:
                        header_count += 1
                        headers.write(json.dumps({'source': src, 'record': count - 1, **row,
                            'decompressedBytes': len(data), 'recordSha256': digest(data),
                            'header': header}, ensure_ascii=True) + '\n')
                        if parameters is not None:
                            body = request_parameters(data, header['headerBytes'], pools)
                            add_index_targets(body)
                            parameter_count += len(body['parsedParameters'])
                            collection_count += int(body['parameterCollectionLayoutComplete'])
                            if body['blockedReason']:
                                blocked_parameters[body['blockedReason']] += 1
                            parameters.write(json.dumps({'source': src, 'record': count - 1, **row,
                                'recordSha256': digest(data), 'header': header, 'parameters': body}, ensure_ascii=True) + '\n')
                if responses is not None:
                    header = response_header(data, pools)
                    if header is not None:
                        body = response_parameters(data, header['headerBytes'], pools)
                        add_index_targets(body)
                        response_count += 1
                        response_parameter_count += len(body['parsedParameters'])
                        response_collection_count += int(body['parameterCollectionLayoutComplete'])
                        if body['blockedReason']:
                            blocked_responses[body['blockedReason']] += 1
                        responses.write(json.dumps({'source': src, 'record': count - 1, **row,
                            'recordSha256': digest(data), 'header': header, 'parameters': body}, ensure_ascii=True) + '\n')
                if dop_prefixes is not None:
                    dop = dop_compu_prefix(data, pools)
                    if dop is not None:
                        dop_count += 1
                        compu_count += int(dop['compuPrefixLayoutComplete'])
                        if dop['blockedReason']:
                            blocked_dop[dop['blockedReason']] += 1
                        dop_prefixes.write(json.dumps({'source': src, 'record': count - 1, **row,
                            'recordSha256': digest(data), 'prefix': dop}, ensure_ascii=True) + '\n')
                if data_layouts is not None:
                    layout = dop_definition_layout(data, pools)
                    if layout is None:
                        layout = unit_definition_layout(data, pools)
                    if layout is None:
                        layout = constraint_definition_layout(data, pools)
                    if layout is None:
                        layout = unit_group_definition_layout(data, pools)
                    if layout is None:
                        layout = table_structure_layout(data, pools)
                    if layout is not None:
                        native_type = str(layout['recordType'])
                        layout_counts[native_type] += 1
                        layout_complete[native_type] += int(layout['fieldLayoutComplete'])
                        row_prefix_count += int(layout.get('rowPrefixLayoutComplete', False))
                        if layout['blockedReason']:
                            layout_blocked[layout['blockedReason']] += 1
                        refs = [layout.get(field) for field in ('unitReference',
                            'internalConstraintReference', 'physicalConstraintReference')]
                        refs += [item['reference'] for item in (layout.get('unitGroups') or {}).get('items', [])]
                        refs += [item['reference'] for item in (layout.get('unitReferences') or {}).get('items', [])]
                        refs += layout_references(layout)
                        method = (layout.get('compuPrefix') or {}).get('compuMethod') or {}
                        for field in ('nativeField18', 'nativeField28'):
                            info = (method.get(field) or {}).get('codeInformation') or {}
                            refs += [item['reference'] for item in info.get('libraries', [])]
                        if indexed is not None:
                            for reference in refs:
                                if reference is None:
                                    continue
                                match = reference_index_target(reference, containers)
                                reference['indexLookup'] = match
                                layout_refs[str(match['target']['recordType']) if match['indexTargetMatched']
                                            else 'unresolved:' + match['unresolvedReason']] += 1
                            if layout.get('unitReference') is not None:
                                lookup = layout['unitReference']['indexLookup']
                                layout['unitReference']['targetIsUnitRecord'] = bool(
                                    lookup['indexTargetMatched'] and lookup['target']['recordType'] == 0x102)
                            if layout.get('keyDopReference') is not None:
                                lookup = layout['keyDopReference']['indexLookup']
                                layout['keyDopReference']['targetIsSimpleDopRecord'] = bool(
                                    lookup['indexTargetMatched'] and lookup['target']['recordType'] == 0x2c)
                        data_layouts.write(json.dumps({'source': src, 'record': count - 1, **row,
                            'recordSha256': digest(data), 'layout': layout}, ensure_ascii=True) + '\n')
                        if probe_output is not None and layout['recordType'] == 0x2c:
                            probes = synthetic_dop_probes(layout, kernel_sha256=kernel_source['sha256'])
                            for probe in probes:
                                value = probe['result']
                                numeric_counts[value.get('probeKind') if value['ok'] else value['reason']] += 1
                            probe_output.write(json.dumps({'source': src, 'record': count - 1, **row,
                                'recordSha256': digest(data), 'probes': probes,
                                'actualVehicleResponse': False, 'executionEnabled': False}, ensure_ascii=True) + '\n')
            summary = {'source': src, 'records': count, 'decompressedBytes': size,
                'recordsWithCandidateStrings': matched, 'candidateReferences': references,
                'requestHeaders': header_count,
                'framingCoverageExact': True, 'recordSemanticsVerified': False}
            if parameters is not None:
                summary['requestParameterAudit'] = {'parsedParameters': parameter_count,
                    'completeCollections': collection_count, 'blockedReasons': dict(blocked_parameters)}
            if responses is not None:
                summary['responseParameterAudit'] = {'responseHeaders': response_count,
                    'parsedParameters': response_parameter_count, 'completeCollections': response_collection_count,
                    'blockedReasons': dict(blocked_responses)}
            if indexed is not None:
                summary['keyIndexAudit'] = {'source': indexed['source'], 'objects': len(indexed['objects']),
                    'pages': indexed['pages'], 'rootLevel': indexed['rootLevel'],
                    'rootPreviousRaw': indexed['rootPreviousRaw'], 'rootPreviousMeaningVerified': False,
                    'frameCoverageExact': True, 'referenceTargetTypes': dict(indexed['referenceMatches']),
                    'unresolvedReferences': dict(indexed['unresolvedReferences']),
                    'referenceSemanticsVerified': False, 'ecuApplicabilityVerified': False}
            if dop_prefixes is not None:
                summary['dopCompuPrefixAudit'] = {'dopSimpleRecords': dop_count,
                    'completeCompuPrefixes': compu_count, 'blockedReasons': dict(blocked_dop),
                    'dopBodyVerified': False, 'physicalConversionQualified': False}
            if data_layouts is not None:
                summary['dataObjectLayoutAudit'] = {'recordTypes': dict(layout_counts),
                    'completeFieldLayouts': dict(layout_complete), 'blockedReasons': dict(layout_blocked),
                    'completeTableRowPrefixes': row_prefix_count,
                    'referenceTargetTypes': dict(layout_refs), 'recordEnvelopeVerified': False,
                    'physicalConversionQualified': False}
            databases_summary.append(summary)
    response_probe_counts = None
    if response_numeric_probes:
        from .piwis_response_numeric import write_response_numeric_probes, SOURCE_EVIDENCE_RVAS as RESPONSE_NUMERIC_RVAS
        response_probe_counts = write_response_numeric_probes(output / 'response-parameter-prefixes.jsonl',
            output / 'data-object-layouts.jsonl', output / 'response-numeric-probes.jsonl',
            kernel_sha256=kernel_source['sha256'])
        from .piwis_nested_numeric import write_nested_response_numeric_probes, SOURCE_EVIDENCE_RVAS as NESTED_RVAS
        nested_probe_counts = write_nested_response_numeric_probes(output / 'response-parameter-prefixes.jsonl',
            output / 'data-object-layouts.jsonl', output / 'nested-response-numeric-probes.jsonl',
            kernel_sha256=kernel_source['sha256'])
        from .piwis_table_key import write_declared_table_key_probes, SOURCE_EVIDENCE_RVAS as TABLE_KEY_RVAS
        table_key_probe_counts = write_declared_table_key_probes(output / 'response-parameter-prefixes.jsonl',
            output / 'data-object-layouts.jsonl', output / 'table-key-response-probes.jsonl',
            ascii_strings=pools.get('AStringData', {}), kernel_sha256=kernel_source['sha256'])
        from .piwis_table_struct import write_table_structure_child_probes, SOURCE_EVIDENCE_RVAS as TABLE_STRUCT_RVAS
        table_struct_probe_counts = write_table_structure_child_probes(output / 'response-parameter-prefixes.jsonl',
            output / 'data-object-layouts.jsonl', output / 'table-structure-child-probes.jsonl',
            ascii_strings=pools.get('AStringData', {}), kernel_sha256=kernel_source['sha256'])
        from .piwis_recursive_numeric import write_recursive_response_probes, SOURCE_EVIDENCE_RVAS as RECURSIVE_RVAS
        recursive_probe_counts = write_recursive_response_probes(output / 'response-parameter-prefixes.jsonl',
            output / 'data-object-layouts.jsonl', output / 'recursive-response-numeric-probes.jsonl',
            ascii_strings=pools.get('AStringData', {}), kernel_sha256=kernel_source['sha256'])
    if table_selection_probes:
        from .piwis_table_selection import write_table_selection_probes, SOURCE_EVIDENCE_RVAS as SELECTION_RVAS
        table_selection_counts = write_table_selection_probes(output / 'data-object-layouts.jsonl',
            output / 'table-selection-probes.jsonl', kernel_sha256=kernel_source['sha256'])
    if unit_probes:
        from .piwis_units import write_unit_probes, SOURCE_EVIDENCE_RVAS as UNIT_RVAS
        unit_counts = write_unit_probes(output / 'data-object-layouts.jsonl',
            output / 'unit-bindings.jsonl', output / 'unit-conversion-probes.jsonl',
            kernel_sha256=kernel_source['sha256'])
        from .piwis_unit_groups import write_unit_group_selections, SOURCE_EVIDENCE_RVAS as GROUP_RVAS
        unit_group_counts = write_unit_group_selections(output / 'data-object-layouts.jsonl',
            output / 'unit-group-selection-probes.jsonl', kernel_sha256=kernel_source['sha256'])
    if constraint_probes:
        from .piwis_constraint_candidates import write_constraint_probes, SOURCE_EVIDENCE_RVAS as CONSTRAINT_RVAS
        constraint_counts = write_constraint_probes(output / 'data-object-layouts.jsonl',
            output / 'constraint-bindings.jsonl', output / 'constraint-classification-probes.jsonl',
            kernel_sha256=kernel_source['sha256'])
    if constraint_flow_probes:
        from .piwis_constraint_flow import write_constraint_flow_probes, SOURCE_EVIDENCE_RVAS as CONSTRAINT_FLOW_RVAS
        constraint_flow_counts = write_constraint_flow_probes(output / 'data-object-layouts.jsonl',
            output / 'constraint-flow-probes.jsonl', kernel_sha256=kernel_source['sha256'],
            response_path=output / 'response-parameter-prefixes.jsonl' if parse_response_parameters else None,
            response_output_path=output / 'response-constraint-flow-probes.jsonl' if parse_response_parameters else None)
    if ecu_config_mappings:
        from .piwis_ecu_config import write_ecu_config_mapping_targets, write_ecu_config_data_reference_targets, write_ecu_config_record_reference_targets, SOURCE_EVIDENCE_RVAS as ECU_CONFIG_RVAS
        ecu_config_counts = write_ecu_config_mapping_targets(output / 'ecu-config-mappings.jsonl',
            indexed_sources, pools, output / 'ecu-config-mapping-targets.jsonl', kernel_sha256=kernel_source['sha256'])
        config_data_reference_counts = write_ecu_config_data_reference_targets(
            output / 'ecu-config-data-reference-prefixes.jsonl', indexed_sources, pools,
            output / 'ecu-config-data-record-targets.jsonl', kernel_sha256=kernel_source['sha256'])
        config_record_reference_counts = write_ecu_config_record_reference_targets(
            output / 'ecu-config-record-reference-prefixes.jsonl', indexed_sources, pools,
            output / 'ecu-config-record-data-record-targets.jsonl', kernel_sha256=kernel_source['sha256'])
    for item in original_sources:
        raw, current = source(item['file'])
        if current != item:
            raise ValueError('database-source-changed-during-audit')
    result = {'schemaVersion': 1, 'kind': 'piwis-compiled-database-static-research',
        'scope': 'provided-sources-only', 'executionEnabled': False, 'liveVerified': False,
        'targetExecuted': False, 'noDeviceIO': True, 'stringTables': tables,
        'requestHeaderProfile': {'nativeKernel': kernel_source, 'recordType': 0x78,
            'sourceEvidenceRvas': {'requestTypeRegistration': 0x11fa0, 'requestFactory': 0x215480,
                'requestConstructor': 0x2154b0, 'requestLoader': 0x2159e0,
                'asciiStringLoader': 0x37d20, 'unicodeStringLoader': 0xa5b00},
            'scope': 'prefix-only; nativeField38/34/30 roles unknown; request body and ECU binding unresolved'} if kernel_source else None,
        'crossPoolIdOverlaps': sum(n > 1 for n in counts.values()), 'databases': databases_summary,
        'limitations': ['string tables, zlib framing and optional pinned header/parameter prefixes only; not general MCD database decoding',
            'sliding ID matches may be accidental and namespace selection is unresolved',
            'no ECU applicability, complete request/response layout, security access or executable definitions established']}
    if parse_request_parameters or parse_response_parameters:
        parameter_profile = {'nativeKernel': kernel_source,
            'sourceEvidenceRvas': SOURCE_EVIDENCE_RVAS,
            'supportedParameterTypes': [0xa5, 0xb2, 0xb0, 0xa7], 'supportedDefaultValueTypes': [11, 14],
            'supportedCodedTypeKinds': [0, 1, 2],
            'supportedNrcValueTypes': [11, 14],
            'scope': 'known parameter collection prefixes only; references unbound; encoding fields raw; trailing request data unresolved',
            'executionEnabled': False, 'responseLayoutVerified': False}
        if parse_request_parameters:
            result['requestParameterProfile'] = parameter_profile
        if parse_response_parameters:
            result['responseParameterProfile'] = {**parameter_profile, 'recordType': 0x91, 'collectionType': 0x92,
                'sourceEvidenceRvas': {**SOURCE_EVIDENCE_RVAS, 'responseRegistration': 0x129c0,
                    'responseFactory': 0x219b00, 'responseLoader': 0x21a050,
                    'responseCollectionLoader': 0x21ab90, 'responseParametersRegistration': 0x129f0,
                    'responseParametersFactory': 0x21b050, 'responseParametersConstructor': 0x21b0d0,
                    'responseParametersVtable': 0x5f56b0},
                'scope': 'response header and known parameter collection prefixes only; references unbound; no physical-value decoding or runtime response'}
    if key_indexes is not None:
        from .piwis_key_index import SOURCE_EVIDENCE_RVAS as KEY_RVAS
        result['keyIndexProfile'] = {'nativeKernel': kernel_source, 'sourceEvidenceRvas': KEY_RVAS,
            'publicPageLayoutSource': 'https://github.com/peterGraf/pbl/blob/3ee159ed6ef2e9d55a9d37340326f537ef109e49/src/src/pblkf.c',
            'supportedPageBytes': 4096, 'supportedObjectKeyBytes': 4, 'supportedPayloadBytes': [6, 8, 12],
            'scope': 'exact retained index/frame coverage and static reference target candidates; reference roles and target layouts not qualified',
            'executionEnabled': False, 'ecuApplicabilityVerified': False}
    if parse_data_objects:
        from .piwis_data_objects import SOURCE_EVIDENCE_RVAS as DOP_RVAS
        result['dataObjectPrefixProfile'] = {'nativeKernel': kernel_source, 'sourceEvidenceRvas': DOP_RVAS,
            'supportedRecordType': 0x2c,
            'scope': 'DbDopSimple compu prefix only; full DOP, units, category/direction semantics and vehicle values unqualified',
            'dopBodyVerified': False, 'physicalConversionQualified': False, 'executionEnabled': False}
    if parse_data_layouts:
        from .piwis_data_objects import SOURCE_EVIDENCE_RVAS as DOP_RVAS
        from .piwis_parameters import SOURCE_EVIDENCE_RVAS as PARAMETER_RVAS
        from .piwis_tables import SOURCE_EVIDENCE_RVAS as TABLE_RVAS
        result['dataObjectLayoutProfile'] = {'nativeKernel': kernel_source,
            'sourceEvidenceRvas': {**DOP_RVAS, **PARAMETER_RVAS, **TABLE_RVAS, **GROUP_RVAS_FOR_LAYOUT}, 'supportedRecordTypes': [0x2c, 0x102, 0x104, 0x203, 0xaa, 0xab, 0xac],
            'scope': 'known loaded DOP, unit, constraint, table, structure and table-row fields; named row streams restricted to flag/empty preconditions; richer branches, record tails, native enums, conversion/validation semantics and vehicle values unqualified',
            'recordEnvelopeVerified': False, 'physicalConversionQualified': False, 'executionEnabled': False}
    if numeric_probes:
        result['numericProbeProfile'] = {'nativeKernel': kernel_source,
            'sourceEvidenceRvas': NUMERIC_RVAS, 'caseResults': dict(numeric_counts),
            'scope': 'synthetic fixed 1..32-bit UINT32/None or INT32/TwoComplement fields with 0..7-bit positions, optional same-width noncondensed masks and matching identity, single integer/floating linear stages with same-type integer coded limits (integer coefficients truncate separately, 32-bit multiply/add wraps, signed division truncates toward zero; CPU-invalid coefficient conversion and signed division overflow blocked), or ordered floating scale-linear ranges with typed finite numeric defaults; closed-point Unicode texttable limited to UINT32; byte-aligned fixed ASCII-base ISO88591/bytefield None identity leaves preserve zeros and input order without masks; MIN-MAX candidates validate nonnegative byte minimum and cap at maximum; termination 1/2 scan zero/FF within capped input without requiring a marker at input end; variable ASCII-base then truncates at zero; empty Unicode/bytefield assignments pinned separately; byte/text input bounded by offline 4095-byte PDU guard; other length/encoding branches, condensed masks, runtime cursor consumption, parameter binding, constraints, units and ECU applicability unqualified',
            'actualVehicleResponse': False, 'physicalConversionQualified': False, 'executionEnabled': False}
    if response_numeric_probes:
        result['responseNumericProbeProfile'] = {'nativeKernel': kernel_source,
            'sourceEvidenceRvas': RESPONSE_NUMERIC_RVAS, 'counts': response_probe_counts,
            'nestedSourceEvidenceRvas': NESTED_RVAS, 'nestedCounts': nested_probe_counts,
            'tableKeySourceEvidenceRvas': TABLE_KEY_RVAS, 'tableKeyCounts': table_key_probe_counts,
            'tableStructureSourceEvidenceRvas': TABLE_STRUCT_RVAS, 'tableStructureCounts': table_struct_probe_counts,
            'recursiveSourceEvidenceRvas': RECURSIVE_RVAS, 'recursiveCounts': recursive_probe_counts,
            'recursiveScope': 'synthetic fixed or qualified MIN-MAX byte/text leaf candidates through 2..8 source-bound ordinary structures rooted in VALUE or TABLE-STRUCT; explicit positions only; depth/path bounds are offline resource guards; dynamic positions, inner tables, cycles, sibling completion, runtime owners and service/ECU applicability unqualified',
            'tableStructureScope': 'synthetic root TABLE-STRUCT/key/structure-child candidates with ordinary names, unique preceding same-response key names, explicit noninterleaved positions and C-locale declared keys; runtime owners, generated-name transforms, constraints, recursive/dynamic layouts and complete row/service/ECU responses unqualified',
            'tableKeyScope': 'synthetic explicitly positioned 8/16-bit identity UINT32 TABLE-KEY candidates for unique declared row names and C-locale canonical decimal keys; runtime names, expected-key validation, constraints, row values and service/ECU binding unqualified',
            'nestedScope': 'one-level ordinary structures with explicit parent/child positions and exact retained index candidates; dynamic cursors, sibling processing, recursive structures and table selection unqualified',
            'scope': 'synthetic explicitly positioned response-root VALUE field slices with exact retained index target candidates; fixed or qualified MIN-MAX byte/text stages only; runtime references, service matching, constraints, envelopes and ECU applicability unqualified',
            'referenceResolutionVerified': False, 'responseEnvelopeVerified': False,
            'actualVehicleResponse': False, 'physicalConversionQualified': False, 'executionEnabled': False}
    if table_selection_probes:
        result['tableSelectionProbeProfile'] = {'nativeKernel': kernel_source,
            'sourceEvidenceRvas': SELECTION_RVAS, 'counts': table_selection_counts,
            'scope': 'synthetic typed Unicode and explicit C-locale UINT32 direct table-key/index candidates; fallback, runtime locale, parameter binding, row values, audience/preconditions and ECU applicability unqualified',
            'referenceResolutionVerified': False, 'tableBindingVerified': False,
            'actualVehicleResponse': False, 'physicalConversionQualified': False, 'executionEnabled': False}
    if unit_probes:
        result['unitProbeProfile'] = {'nativeKernel': kernel_source,
            'sourceEvidenceRvas': {**UNIT_RVAS, **GROUP_RVAS}, 'counts': unit_counts,
            'unitGroupSelectionCounts': unit_group_counts,
            'scope': 'declared units via retained exact key-index targets; synthetic explicit compatible unit pairs and first name/exponent match in explicitly supplied unit groups; no inferred runtime display unit or automatic SI conversion',
            'unitSemanticsVerified': False, 'runtimeUnitSelectionVerified': False,
            'runtimeUnitIdentityVerified': False, 'runtimeFloatingEnvironmentVerified': False,
            'physicalConversionQualified': False, 'actualVehicleResponse': False, 'executionEnabled': False}
    if constraint_probes:
        result['constraintProbeProfile'] = {'nativeKernel': kernel_source,
            'sourceEvidenceRvas': CONSTRAINT_RVAS, 'counts': constraint_counts,
            'scope': 'declared internal/physical constraint index targets and numeric checkConstraint candidates under explicit synthetic configuration, property-mode and direction inputs; caller gates, computed constraints, runtime policy and vehicle values unqualified',
            'constraintSemanticsVerified': False, 'runtimeConstraintSelectionVerified': False,
            'runtimeConfigurationVerified': False, 'callerGatesVerified': False,
            'physicalConversionQualified': False, 'actualVehicleResponse': False, 'executionEnabled': False}
    if constraint_flow_probes:
        result['constraintFlowProbeProfile'] = {'nativeKernel': kernel_source,
            'sourceEvidenceRvas': CONSTRAINT_FLOW_RVAS, 'counts': constraint_flow_counts,
            'scope': 'synthetic forward simple-DOP and declared root VALUE fields; exact constraint references, mode/type/computed bypass and ordered pre/post checks under explicit policy/type/initial-state inputs; higher callers, runtime types/settings, units and complete responses unqualified',
            'callerGateSourcePinned': True, 'parameterDataTypeResolutionVerified': False,
            'constraintSemanticsVerified': False, 'runtimeConstraintSelectionVerified': False,
            'runtimeConfigurationVerified': False, 'higherCallerExceptionHandlingVerified': False,
            'physicalConversionQualified': False, 'actualVehicleResponse': False, 'executionEnabled': False}
    if ecu_config_mappings:
        result['ecuConfigMappingProfile'] = {'nativeKernel': kernel_source,
            'sourceEvidenceRvas': ECU_CONFIG_RVAS, 'counts': ecu_config_counts,
            'dataReferenceCounts': config_data_reference_counts,
            'configurationRecordReferenceCounts': config_record_reference_counts,
            'dataRecordCoreCounts': dict(config_record_core_counts),
            'dataRecordBodyCounts': dict(config_record_body_counts),
            'scope': 'static configuration mappings, Data/Record references, DataRecord core/embedded CodingData and absent SDG/additional-audience loader tails; absent optional audiences and Record ID items only; exact retained same-database index/container/short-name candidates; record envelopes, other parent tails, file resolution, data-format/size-unit/neutral-field meanings, native map selection, ECU identity and vehicle coding unqualified',
            'runtimeMappingSelectionVerified': False, 'mapInsertionSemanticsVerified': False,
            'sourceByteOrder': 'retained-little-endian', 'runtimeStreamByteOrderVerified': False,
            'recordEnvelopeVerified': False, 'actualVehicleOriginalCoding': False,
            'ecuApplicabilityVerified': False, 'executionEnabled': False}
    (output / 'database-index.json').write_text(json.dumps(result, indent=2) + '\n', encoding='utf8')
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database', type=Path, action='append', required=True)
    parser.add_argument('--ascii-index', type=Path, required=True)
    parser.add_argument('--ascii-data', type=Path, required=True)
    parser.add_argument('--unicode-index', type=Path, required=True)
    parser.add_argument('--unicode-data', type=Path, required=True)
    parser.add_argument('--output-dir', type=Path, required=True)
    parser.add_argument('--native-kernel', type=Path, help='optional exact retained kernel; checked as bytes, never loaded')
    parser.add_argument('--request-parameters', action='store_true', help='audit known parameter prefixes; requires --native-kernel; no executable requests')
    parser.add_argument('--response-parameters', action='store_true', help='audit response definition prefixes; requires --native-kernel; no live response decoding')
    parser.add_argument('--key-index', type=Path, action='append', help='optional key index per --database, in the same order; requires pinned kernel; strict index/frame audit')
    parser.add_argument('--data-object-prefixes', action='store_true', help='audit simple DOP compu prefixes; requires pinned kernel; no vehicle value decoder')
    parser.add_argument('--data-object-layouts', action='store_true', help='audit known DOP, unit, constraint, table/structure/row fields; requires pinned kernel; record tails and vehicle decoding unqualified')
    parser.add_argument('--numeric-probes', action='store_true', help='synthetic offline numeric stage checks; requires --data-object-layouts; no ECU responses or vehicle qualification')
    parser.add_argument('--response-numeric-probes', action='store_true', help='synthetic fixed root, one-level structure, declared TABLE-KEY and root TABLE-STRUCT child checks over retained index targets; requires response parameters, data object layouts and key indexes; table fixtures assume C locale; no service or vehicle qualification')
    parser.add_argument('--table-selection-probes', action='store_true', help='synthetic direct typed table-key/index checks; requires data object layouts and key indexes; UINT32 fixtures assume C locale; no fallback, ECU response or vehicle qualification')
    parser.add_argument('--unit-probes', action='store_true', help='declared unit bindings, synthetic explicit unit-pair and unit-group selection checks; requires data object layouts and key indexes; no runtime unit selection or vehicle qualification')
    parser.add_argument('--constraint-probes', action='store_true', help='declared constraint bindings and synthetic numeric classifications with explicit policies; requires data object layouts and key indexes; no inferred runtime settings, caller gates or vehicle qualification')
    parser.add_argument('--constraint-flow-probes', action='store_true', help='forward pre/conversion/post candidates with exact constraints and explicit mode/type/configuration/initial-state fixtures; requires layouts and key indexes; includes root fields when response parameters supplied; no runtime settings, full responses or vehicle qualification')
    parser.add_argument('--ecu-config-mappings', action='store_true', help='static ECU configuration maps, Data/Record references, DataRecord core/embedded CodingData file patterns and absent SDG/additional-audience tails; nonempty optional bodies/envelopes unqualified; requires key indexes and pinned kernel; no file resolution, runtime selection, ECU identity or vehicle original coding')
    args = parser.parse_args()
    result = audit(args.database, [('AStringData', args.ascii_index, args.ascii_data, False),
        ('UStringData', args.unicode_index, args.unicode_data, True)], args.output_dir, args.native_kernel, args.request_parameters, args.response_parameters, args.key_index, args.data_object_prefixes, args.data_object_layouts, args.numeric_probes, args.response_numeric_probes, args.table_selection_probes, args.unit_probes, args.constraint_probes, args.constraint_flow_probes, args.ecu_config_mappings)
    print(json.dumps({'databases': len(result['databases']), 'records': sum(d['records'] for d in result['databases']),
        'requestHeaders': sum(d['requestHeaders'] for d in result['databases']),
        'responseHeaders': sum(d.get('responseParameterAudit', {}).get('responseHeaders', 0) for d in result['databases']),
        'requestParameterItems': sum(d.get('requestParameterAudit', {}).get('parsedParameters', 0) for d in result['databases']),
        'responseParameterItems': sum(d.get('responseParameterAudit', {}).get('parsedParameters', 0) for d in result['databases']),
        'indexedRecords': sum(d.get('keyIndexAudit', {}).get('objects', 0) for d in result['databases']),
        'dopSimpleRecords': sum(d.get('dopCompuPrefixAudit', {}).get('dopSimpleRecords', 0) for d in result['databases']),
        'completeCompuPrefixes': sum(d.get('dopCompuPrefixAudit', {}).get('completeCompuPrefixes', 0) for d in result['databases']),
        'completeDataObjectFieldLayouts': sum(sum(d.get('dataObjectLayoutAudit', {}).get('completeFieldLayouts', {}).values()) for d in result['databases']),
        'stringCounts': {t['pool']: t['strings'] for t in result['stringTables']}, 'targetExecuted': False}))


if __name__ == '__main__':
    main()
