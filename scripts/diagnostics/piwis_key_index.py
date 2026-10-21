"""Bounded reader for the retained PIWIS PBL key index, never vendor execution.

Disk page/prefix layout: peterGraf/pbl, commit
3ee159ed6ef2e9d55a9d37340326f537ef109e49, src/src/pblkf.c and pbl.c.
This independently written reader supports 4096-byte tree pages, the empty
reserved data page and four-byte numeric keys only. Other layouts are rejected.
MCD payload interpretation requires the caller's exact native kernel profile:
PblDbKeyAndDataFile::retrieveObject, RVA 0x2d2550 (length branches 0x2d274c).
Neither an indexed object nor a resolved reference qualifies a vehicle operation.
"""
import struct

PAGE_SIZE = 4096
HEADER_SIZE = 13
FILE_LIMIT = 32 * 1024 * 1024
ENTRY_LIMIT = 100_000
MAGIC = b"1.00 Peter's B Tree\0"
SOURCE_EVIDENCE_RVAS = {
    'referenceLoader': 0x31a60, 'attributedReferenceLoader': 0x4c9b0,
    'dopReferenceResolver': 0xb6a50, 'simpleDopReferenceResolver': 0x2371f0,
    'pblFindObject': 0x2d22f0, 'pblRetrieveObject': 0x2d2550,
    'pblNumericKey': 0x2d25fe, 'pblReadCall': 0x2d2736,
    'payloadLengthSwitch': 0x2d274c, 'databaseSeek': 0x2d27da,
    'databaseRead': 0x2d27ef, 'inflateCall': 0x2d2833,
}


def variable_uint(raw, pos, end):
    if pos < 0 or end > len(raw) or pos >= end:
        raise ValueError('truncated-pbl-variable-integer')
    first = raw[pos]
    size, mask, minimum = next((s, m, low) for limit, s, m, low in (
        (0x80, 0, 0x7f, 0), (0xc0, 1, 0x3f, 0x80),
        (0xe0, 2, 0x1f, 0x4000), (0xf0, 3, 0x0f, 0x200000),
        (0x100, 4, 0, 0x10000000)) if first < limit)
    if first > 0xf0 or pos + 1 + size > end:
        raise ValueError('pbl-variable-integer-prefix-or-bounds')
    value = ((first & mask) << (8 * size)) | int.from_bytes(raw[pos + 1:pos + 1 + size], 'big')
    if value < minimum:
        raise ValueError('noncanonical-pbl-variable-integer')
    return value, pos + 1 + size


def key_index(raw):
    if len(raw) < 2 * PAGE_SIZE or len(raw) > FILE_LIMIT or len(raw) % PAGE_SIZE:
        raise ValueError('pbl-file-size-or-page-alignment')
    pages = []
    total = 0
    for number in range(len(raw) // PAGE_SIZE):
        data = raw[number * PAGE_SIZE:(number + 1) * PAGE_SIZE]
        level, nxt, prev, count, free = struct.unpack_from('>BIIHH', data)
        if level > 32 and not (number == 1 and level == 255):
            raise ValueError('unsupported-pbl-page-level')
        if free < HEADER_SIZE or free > PAGE_SIZE - 2 * count:
            raise ValueError('pbl-free-or-slot-array-bounds')
        if number == 1:
            if (level, nxt, prev, count, free) != (255, 0, 0, 0, HEADER_SIZE):
                raise ValueError('unsupported-pbl-reserved-data-page')
        elif not count:
            raise ValueError('empty-pbl-tree-page')
        entries, previous, ranges = [], b'', []
        if level != 255:
            for slot in range(count):
                off = struct.unpack_from('>H', data, PAGE_SIZE - 2 * (slot + 1))[0]
                if off < HEADER_SIZE or off + 3 > free:
                    raise ValueError('pbl-entry-offset-bounds')
                length, common = data[off:off + 2]
                if common > length or common > len(previous):
                    raise ValueError('pbl-key-prefix-bounds')
                value, pos = variable_uint(data, off + 2, free)
                suffix_end = pos + length - common
                if suffix_end > free:
                    raise ValueError('pbl-key-suffix-bounds')
                key = previous[:common] + data[pos:suffix_end]
                if slot and key <= previous:
                    raise ValueError('pbl-key-order-or-duplicate')
                pos = suffix_end
                entry = {'page': number, 'slot': slot, 'entryOffset': off, 'keyHex': key.hex()}
                if level:
                    if (slot == 0 and key) or (slot > 0 and not key):
                        raise ValueError('pbl-internal-minimum-key')
                    entry['child'] = value
                else:
                    if value > 1024:
                        raise ValueError('unsupported-pbl-external-data')
                    if pos + value > free:
                        raise ValueError('pbl-inline-data-bounds')
                    entry['payloadHex'] = data[pos:pos + value].hex()
                    pos += value
                entry['entryEndOffset'] = pos
                ranges.append((off, pos))
                entries.append(entry)
                previous = key
            expected = HEADER_SIZE
            for off, end in sorted(ranges):
                if off != expected:
                    raise ValueError('pbl-entry-overlap-or-gap')
                expected = end
            if expected != free:
                raise ValueError('unconsumed-pbl-entry-area')
        pages.append({'level': level, 'next': nxt, 'previous': prev, 'entries': entries})
        total += count
        if total > ENTRY_LIMIT:
            raise ValueError('pbl-total-entry-limit')
    # In the four retained MCD files root POFFSET equals file page count,
    # whereas the public PBL implementation also permits zero. Preserve this
    # profile marker without assuming its role is identical to public PBL's
    # free-list pointer. No nonempty/free pages are supported here.
    if pages[0]['next'] != 1 or pages[0]['previous'] not in (0, len(pages)):
        raise ValueError('unsupported-pbl-root-data-or-free-chain')
    seen, levels, leaf_entries = set(), {}, []

    def visit(number, level, low=b'', high=None):
        if number >= len(pages) or number == 1 or number in seen:
            raise ValueError('pbl-child-bounds-cycle-or-shared-page')
        page = pages[number]
        if page['level'] != level:
            raise ValueError('pbl-child-level-mismatch')
        seen.add(number)
        levels.setdefault(level, []).append(number)
        entries = page['entries']
        if level:
            for i, entry in enumerate(entries):
                lower = bytes.fromhex(entry['keyHex']) if i else low
                upper = bytes.fromhex(entries[i + 1]['keyHex']) if i + 1 < len(entries) else high
                if lower < low or (high is not None and lower >= high):
                    raise ValueError('pbl-internal-separator-bounds')
                visit(entry['child'], level - 1, lower, upper)
        else:
            for entry in entries:
                key = bytes.fromhex(entry['keyHex'])
                if key < low or (high is not None and key >= high):
                    raise ValueError('pbl-leaf-key-outside-separator-range')
                leaf_entries.append(entry)

    visit(0, pages[0]['level'])
    if seen != set(range(len(pages))) - {1}:
        raise ValueError('unreachable-pbl-tree-page')
    for level, numbers in levels.items():
        if numbers == [0]:
            continue  # Root links are reserved data/free-chain metadata.
        for i, number in enumerate(numbers):
            expected = (numbers[i - 1] if i else 0, numbers[i + 1] if i + 1 < len(numbers) else 0)
            if (pages[number]['previous'], pages[number]['next']) != expected:
                raise ValueError('pbl-sibling-chain-mismatch')
    if not leaf_entries or leaf_entries[0]['keyHex'] or bytes.fromhex(leaf_entries[0]['payloadHex']) != MAGIC:
        raise ValueError('pbl-magic-item-mismatch')
    result = []
    previous = b''
    for entry in leaf_entries[1:]:
        key, payload = bytes.fromhex(entry['keyHex']), bytes.fromhex(entry['payloadHex'])
        if len(key) != 4 or key == b'\0' * 4:
            raise ValueError('unsupported-pbl-object-key')
        if key <= previous:
            raise ValueError('pbl-global-key-order-or-duplicate')
        if len(payload) not in (6, 8, 12):
            raise ValueError('unsupported-mcd-index-payload-length')
        width = (len(payload) - 4) // 2
        comp = int.from_bytes(payload[4:4 + width], 'little')
        expanded = int.from_bytes(payload[4 + width:], 'little')
        if not comp or not expanded:
            raise ValueError('empty-mcd-index-record-length')
        result.append({**entry, 'objectId': int.from_bytes(key, 'little'),
            'sourceOffset': int.from_bytes(payload[:4], 'little'),
            'compressedBytes': comp, 'decompressedBytes': expanded})
        previous = key
    return {'pageBytes': PAGE_SIZE, 'pages': len(pages), 'rootLevel': pages[0]['level'],
            'rootPreviousRaw': pages[0]['previous'], 'rootPreviousMeaningVerified': False,
            'reachableTreePages': len(seen), 'entries': result}


def bind_frames(index, frames):
    """Validate index offsets AND both lengths against independently framed data."""
    by_offset = {row['sourceOffset']: row for row in frames}
    if len(by_offset) != len(frames):
        raise ValueError('duplicate-database-frame-offset')
    bound, seen = {}, set()
    for entry in index['entries']:
        frame = by_offset.get(entry['sourceOffset'])
        if frame is None or (entry['compressedBytes'], entry['decompressedBytes']) != (
                frame['compressedBytes'], frame['decompressedBytes']):
            raise ValueError('key-index-frame-offset-or-length-mismatch')
        if entry['sourceOffset'] in seen or entry['objectId'] in bound:
            raise ValueError('key-index-record-or-object-alias-unsupported')
        seen.add(entry['sourceOffset'])
        bound[entry['objectId']] = {**entry, 'recordSha256': frame['recordSha256'],
                                   'recordType': frame['recordType'], 'record': frame['record']}
    if seen != set(by_offset):
        raise ValueError('key-index-incomplete-database-coverage')
    return bound


def reference_index_target(reference, containers):
    """Locate a retained target without claiming reference/runtime semantics.

    Native reference load stores two AString identifiers. Their roles here are
    still an explicitly labelled static candidate; lookup is limited to the
    caller-provided database names, never inferred paths or another container.
    """
    object_field, container_field = reference['nativeField08'], reference['nativeField0c']
    result = {'lookupScope': 'explicit-retained-container-name',
              'referenceSemanticsVerified': False, 'ecuApplicabilityVerified': False,
              'executionEnabled': False, 'indexTargetMatched': False}
    if (container_field.get('pool') != 'AStringData' or not container_field.get('resolved')
            or not container_field.get('text')):
        return {**result, 'unresolvedReason': 'container-string-unresolved-or-empty'}
    container = containers.get(container_field['text'])
    if container is None:
        return {**result, 'unresolvedReason': 'container-not-retained'}
    if object_field.get('pool') != 'AStringData' or object_field.get('storage') != 'table-reference':
        return {**result, 'unresolvedReason': 'unsupported-object-reference-storage'}
    object_id = object_field.get('id')
    if not object_id:
        return {**result, 'unresolvedReason': 'null-object-reference'}
    target = container['objects'].get(object_id)
    if target is None:
        return {**result, 'unresolvedReason': 'object-key-not-in-selected-container'}
    return {**result, 'indexTargetMatched': True, 'container': container_field['text'],
            'databaseSource': container['databaseSource'], 'keyIndexSource': container['keyIndexSource'],
            'target': dict(target)}
