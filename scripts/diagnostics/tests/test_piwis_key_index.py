import struct
import unittest

from scripts.diagnostics.piwis_key_index import key_index, variable_uint, bind_frames, reference_index_target


def page(level, entries, nxt=0, prev=0):
    """Test page serializer intentionally uses uncompressed keys and byte values."""
    raw = bytearray(4096)
    pos = 13
    for slot, (key, value) in enumerate(entries):
        struct.pack_into('>H', raw, 4096 - 2 * (slot + 1), pos)
        prefix = bytes((len(key), 0, value if level else len(value)))
        item = prefix + key + (b'' if level else value)
        raw[pos:pos + len(item)] = item
        pos += len(item)
    struct.pack_into('>BIIHH', raw, 0, level, nxt, prev, len(entries), pos)
    return bytes(raw)


MAGIC = (b'', b"1.00 Peter's B Tree\0")
ONE = (b'\x01\0\0\0', struct.pack('<IBB', 0, 15, 21))
TWO = (b'\x02\0\0\0', struct.pack('<IHH', 15, 260, 512))


def fixture():
    return page(1, [(b'', 2), (TWO[0], 3)], 1, 4) + page(255, []) + page(0, [MAGIC, ONE], 3) + page(0, [TWO], 0, 2)


def changed(raw, offset, value):
    return raw[:offset] + value + raw[offset + len(value):]


class PiwisKeyIndexTests(unittest.TestCase):
    def test_pbl_prefix_integer_boundaries_and_malformed_inputs(self):
        vectors = [(b'\x7f', 127), (b'\x80\x80', 128), (b'\xbf\xff', 16383),
                   (b'\xc0\x40\0', 16384), (b'\xdf\xff\xff', 2097151),
                   (b'\xe0\x20\0\0', 2097152), (b'\xef\xff\xff\xff', 268435455),
                   (b'\xf0\x10\0\0\0', 268435456), (b'\xf0\xff\xff\xff\xff', 4294967295)]
        for raw, expected in vectors:
            with self.subTest(raw=raw):
                self.assertEqual(variable_uint(raw, 0, len(raw)), (expected, len(raw)))
        for raw in (b'', b'\x80', b'\xc0\1', b'\xe0\1\2', b'\xf0\1\2\3', b'\xf1\x10\0\0\0', b'\x80\1', b'\xc0\0\x80'):
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                variable_uint(raw, 0, len(raw))

    def test_full_tree_traversal_and_little_endian_mcd_payload(self):
        parsed = key_index(fixture())
        self.assertEqual(parsed['reachableTreePages'], 3)
        self.assertEqual(parsed['rootPreviousRaw'], 4)
        self.assertFalse(parsed['rootPreviousMeaningVerified'])
        self.assertEqual([(e['objectId'], e['sourceOffset'], e['compressedBytes'], e['decompressedBytes']) for e in parsed['entries']],
                         [(1, 0, 15, 21), (2, 15, 260, 512)])
        large = (ONE[0], struct.pack('<III', 0x123456, 0x10000, 0x20000))
        parsed = key_index(page(0, [MAGIC, large], 1) + page(255, []))
        self.assertEqual(parsed['entries'][0]['decompressedBytes'], 0x20000)

    def test_key_prefix_expands_against_previous_logical_slot(self):
        # Prefix key 01 00 00 01 using three bytes of its predecessor.
        leaf = bytearray(page(0, [MAGIC, ONE, (b'\1\0\0\1', ONE[1])], 1))
        off = struct.unpack_from('>H', leaf, 4090)[0]
        free = struct.unpack_from('>H', leaf, 11)[0]
        leaf[off + 1] = 3
        leaf[off + 3:free - 3] = leaf[off + 6:free]
        struct.pack_into('>H', leaf, 11, free - 3)
        parsed = key_index(bytes(leaf) + page(255, []))
        self.assertEqual(parsed['entries'][1]['objectId'], 0x1000001)

    def test_reject_cycles_shared_pages_levels_and_unreachable_pages(self):
        raw = fixture()
        cases = [('cycle', 15, b'\0'), ('shared-page', 18, b'\2'),
                 ('child-outside-file', 15, b'\x7f'), ('wrong-level', 8192, b'\1'),
                 ('broken-sibling', 8193, struct.pack('>I', 0)),
                 ('broken-backlink', 12293, struct.pack('>I', 0)),
                 ('separator-range', 19, b'\1')]
        for name, off, value in cases:
            with self.subTest(name=name), self.assertRaises(ValueError):
                key_index(changed(raw, off, value))
        with self.assertRaisesRegex(ValueError, 'unreachable'):
            key_index(changed(raw, 5, b'\0\0\0\0') + page(0, [ONE]))

    def test_reject_offsets_overlap_bad_magic_and_unknown_layout(self):
        raw = fixture()
        cases = [('slot-outside-payload', 8192 + 4094, b'\0\1'),
                 ('bad-prefix', 8192 + 14, b'\1'), ('bad-magic', 8192 + 16, b'X'),
                 ('slot-overlap', 8192 + 4092, b'\0\x0d'),
                 ('free-gap', 8192 + 11, struct.pack('>H', 8192 % 4096 + 51)),
                 ('data-page-with-content', 4096 + 11, b'\0\x0e'),
                 ('free-page', 12288, b'\xfe'), ('root-marker', 5, b'\0\0\0\x7f')]
        for name, off, value in cases:
            with self.subTest(name=name), self.assertRaises(ValueError):
                key_index(changed(raw, off, value))
        for data in (raw[:-1], raw[:4096], b''):
            with self.subTest(size=len(data)), self.assertRaises(ValueError):
                key_index(data)
        for key, payload in ((b'name', b'12345'), (b'abc', ONE[1]), (b'\0'*4, ONE[1]), (ONE[0], b'\0'*6)):
            with self.subTest(key=key, payload=payload), self.assertRaises(ValueError):
                key_index(page(0, [MAGIC, (key, payload)], 1) + page(255, []))

    def test_frame_binding_requires_all_three_sizes_and_exact_coverage(self):
        index = key_index(fixture())
        frames = [{'sourceOffset': off, 'compressedBytes': comp, 'decompressedBytes': expanded,
                   'record': n, 'recordType': 0xab, 'recordSha256': str(n) * 64}
                  for n, (off, comp, expanded) in enumerate(((0, 15, 21), (15, 260, 512)))]
        self.assertEqual(bind_frames(index, frames)[2]['record'], 1)
        for field in ('sourceOffset', 'compressedBytes', 'decompressedBytes'):
            broken = [{**frames[0], field: frames[0][field] + 1}, frames[1]]
            with self.subTest(field=field), self.assertRaises(ValueError):
                bind_frames(index, broken)
        for broken in (frames[:1], frames + [{**frames[1], 'sourceOffset': 275}], frames + [frames[0]]):
            with self.assertRaises(ValueError):
                bind_frames(index, broken)
        aliased = {'entries': [index['entries'][0], {**index['entries'][0], 'objectId': 3}]}
        with self.assertRaisesRegex(ValueError, 'alias'):
            bind_frames(aliased, frames)

    def test_reference_target_uses_explicit_container_and_remains_unqualified(self):
        obj = {'objectId': 1, 'recordType': 0xab}
        containers = {'retained.bv': {'objects': {1: obj}, 'databaseSource': {'sha256': 'd'}, 'keyIndexSource': {'sha256': 'k'}}}
        ref = {'nativeField08': {'pool': 'AStringData', 'storage': 'table-reference', 'id': 1},
               'nativeField0c': {'pool': 'AStringData', 'text': 'retained.bv', 'resolved': True}}
        result = reference_index_target(ref, containers)
        self.assertTrue(result['indexTargetMatched'])
        self.assertEqual(result['target'], obj)
        self.assertFalse(result['referenceSemanticsVerified'])
        self.assertFalse(result['ecuApplicabilityVerified'])
        self.assertFalse(result['executionEnabled'])
        for changed_field, expected in (({'text': '../else.bv'}, 'container-not-retained'),
                                        ({'resolved': False}, 'container-string-unresolved-or-empty')):
            bad = {**ref, 'nativeField0c': {**ref['nativeField0c'], **changed_field}}
            self.assertEqual(reference_index_target(bad, containers)['unresolvedReason'], expected)
        for changed_field, expected in (({'id': 2}, 'object-key-not-in-selected-container'),
                                        ({'id': 0}, 'null-object-reference'),
                                        ({'storage': 'inline'}, 'unsupported-object-reference-storage')):
            bad = {**ref, 'nativeField08': {**ref['nativeField08'], **changed_field}}
            self.assertEqual(reference_index_target(bad, containers)['unresolvedReason'], expected)


if __name__ == '__main__':
    unittest.main()
