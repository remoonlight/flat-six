import copy
import itertools
import json
from pathlib import Path
import struct
import tempfile
import unittest

from scripts.diagnostics.piwis_constraint_candidates import (
    CONFIG_FIELDS, _neighbor, probe_constraint_classification, write_constraint_probes)
from scripts.diagnostics.piwis_database import REQUEST_KERNEL_SHA256 as PROFILE, audit
from scripts.diagnostics.tests.test_piwis_response_numeric import fixture


def value(number, kind=11):
    return {'nativeType': kind, 'nativeValueDefined': kind != 255,
        'rawHex': '' if kind == 255 else struct.pack({4: '<f', 5: '<d', 7: '<i', 11: '<I'}[kind], number).hex()}


def interval(low=0, high=100, kind=11, flags=17):
    return {'recordType': 0xfb, 'lowerLimit': value(low, kind), 'upperLimit': value(high, kind),
        'nativeLimitFlags': flags, 'nativeLowerIntervalType': 0x6d03 if flags & 2 else 0x6d01 + bool(flags & 1),
        'nativeUpperIntervalType': 0x6d03 if flags & 0x20 else 0x6d01 + bool(flags & 0x10)}


def constraint(kind=11, scales=None):
    _, row = fixture(); row['layout'] = {'recordType': 0x203, 'fieldLayoutComplete': True,
        'interval': interval(kind=kind), 'nativeComputedRaw': 0,
        'scaleConstraints': {'recordType': 0x201, 'declaredScales': len(scales or []), 'scales': scales or []}}
    return row


def scale(info=0x603, low=10, high=20, kind=11):
    return {'recordType': 0x202, 'interval': interval(low, high, kind), 'nativeRangeInfo': info, 'nativeComputedRaw': 0}


class PiwisConstraintTests(unittest.TestCase):
    def probe(self, row, number, kind=11, config=None, mode=1, direction=False, **changes):
        args = {'kernel_sha256': PROFILE, 'configuration': config or dict(zip(CONFIG_FIELDS, (True, False, True, True))),
            'native_property_mode': mode, 'native_direction_flag': direction}
        args.update(changes)
        return probe_constraint_classification(row, number, kind, **args)

    def test_primary_range_and_unmatched_scale_preserve_valid(self):
        row = constraint(scales=[scale()])
        for number, info, exception, index in ((-1, None, None, None), (0, 0x602, False, None),
                (9, 0x602, False, None), (10, 0x603, True, 0), (20, 0x603, True, 0),
                (21, 0x602, False, None), (100, 0x602, False, None), (101, 0x605, True, None)):
            r = self.probe(row, number)
            if info is None:
                self.assertFalse(r['ok']); continue
            self.assertTrue(r['ok'], r); self.assertEqual(r['nativeRangeInfo'], info)
            self.assertEqual(r['candidateNativeException'], exception); self.assertEqual(r['selectedScaleIndex'], index)
        self.assertFalse(self.probe(row, 101, mode=0)['candidateNativeException'])

    def test_all_configurations_modes_directions_and_range_labels(self):
        for bits in itertools.product((False, True), repeat=4):
            cfg = dict(zip(CONFIG_FIELDS, bits))
            for info, direction, mode in itertools.product(range(0x602, 0x608), (False, True), (0, 1, 3)):
                r = self.probe(constraint(scales=[scale(info)]), 15, config=cfg, mode=mode, direction=direction)
                self.assertTrue(r['ok'], r)
                if bits[1]:
                    expected = info in (0x602, 0x604) if direction else info != 0x605
                else:
                    expected = info == 0x602 if (bits[0] if bits[3] else bits[2]) else True
                self.assertEqual(r['candidatePolicyValid'], expected)
                self.assertEqual(r['candidateNativeException'], mode == 1 and not expected)
                self.assertEqual(r['nativeRangeInfo'], info)
                self.assertFalse(r['callerGatesVerified'])

    def test_first_match_does_not_examine_later_unsupported_interval(self):
        row = constraint(scales=[scale(0x604, 10, 20), scale(0x605, 15, 25), None])
        r = self.probe(row, 15); self.assertTrue(r['ok'], r)
        self.assertEqual(r['nativeRangeInfo'], 0x604); self.assertEqual(r['selectedScaleIndex'], 0)
        row['layout']['scaleConstraints']['scales'][0]['interval']['upperLimit'] = value(0, 255)
        self.assertEqual(self.probe(row, 15)['reason'], 'constraint-probe-limit-type-unqualified')
        # Outside the primary interval does not require a usable scale collection.
        row['layout']['scaleConstraints'] = None
        self.assertTrue(self.probe(row, 101)['ok'])
        self.assertEqual(self.probe(row, 50)['reason'], 'constraint-probe-scales-unqualified')

    def test_same_type_signed_float_boundaries_and_adjacent_values(self):
        for kind, low, high in ((7, -20, -10), (4, .1, .2), (5, .1, .2)):
            row = constraint(kind); row['layout']['interval'] = interval(low, high, kind)
            lo = struct.unpack({7: '<i', 4: '<f', 5: '<d'}[kind], bytes.fromhex(row['layout']['interval']['lowerLimit']['rawHex']))[0]
            hi = struct.unpack({7: '<i', 4: '<f', 5: '<d'}[kind], bytes.fromhex(row['layout']['interval']['upperLimit']['rawHex']))[0]
            for number, inside in ((_neighbor(lo, kind, -1), False), (lo, True), (_neighbor(lo, kind, 1), True),
                                   (_neighbor(hi, kind, -1), True), (hi, True), (_neighbor(hi, kind, 1), False)):
                r = self.probe(row, number, kind); self.assertTrue(r['ok'], r)
                self.assertEqual(r['primaryIntervalMatched'], inside)
        self.assertLess(_neighbor(0.0, 4, -1), 0); self.assertGreater(_neighbor(-0.0, 4, 1), 0)

    def test_open_closed_unlimited_priority_and_undefined_closed_rejection(self):
        for flags, expected0, expected100 in ((0, False, False), (1, True, False), (16, False, True), (17, True, True)):
            row = constraint(); row['layout']['interval'] = interval(flags=flags)
            self.assertEqual(self.probe(row, 0)['primaryIntervalMatched'], expected0)
            self.assertEqual(self.probe(row, 100)['primaryIntervalMatched'], expected100)
        row = constraint(); row['layout']['interval'] = interval(kind=255, flags=0x33)
        self.assertTrue(self.probe(row, 0)['primaryIntervalMatched'])
        row['layout']['interval'] = interval(kind=255, flags=17)
        self.assertEqual(self.probe(row, 0)['reason'], 'constraint-probe-limit-type-unqualified')
        row['layout']['interval'] = interval()
        row['layout']['interval']['upperLimit'] = value(0, 255)
        # No short circuit permits the mismatched upper getter when lower fails.
        self.assertEqual(self.probe(row, 101)['reason'], 'constraint-probe-limit-type-unqualified')

    def test_corrupt_source_policy_layout_types_and_flags_are_blocked(self):
        row = constraint()
        cases = [{'kernel_sha256': '0'*64}, {'native_property_mode': True}, {'native_property_mode': -1},
                 {'native_direction_flag': 1}, {'configuration': {}}, {'configuration': dict(zip(CONFIG_FIELDS, (1, False, False, False)))}]
        for args in cases:
            self.assertFalse(self.probe(row, 1, **args)['ok'])
        for number, kind in ((True, 11), (1.0, 11), (-1, 11), (2**32, 11), (float('nan'), 5), (float('inf'), 4), (2**2000, 5), (0, 3)):
            self.assertFalse(self.probe(row, number, kind)['ok'])
        for target, field, new in (('row', 'recordSha256', 'bad'), ('layout', 'fieldLayoutComplete', False),
                                   ('interval', 'nativeLimitFlags', 256), ('interval', 'nativeUpperIntervalType', 0x6d03)):
            changed = copy.deepcopy(row); obj = changed if target == 'row' else changed['layout'] if target == 'layout' else changed['layout']['interval']
            obj[field] = new; self.assertFalse(self.probe(changed, 1)['ok'])
        changed = constraint(5); changed['layout']['interval']['lowerLimit'] = value(float('inf'), 5)
        self.assertEqual(self.probe(changed, 1.0, 5)['reason'], 'constraint-probe-limit-not-finite')
        self.assertFalse(self.probe(constraint(scales=[scale(0x601)]), 15)['ok'])

    def test_writer_exact_bindings_both_sides_and_synthetic_policy_labels(self):
        parameter, dop = fixture(); dop['sourceOffset'] = 200
        target = constraint(); target.update(sourceOffset=90, recordSha256='c'*64)
        ref = parameter['objectReference']; ref['indexLookup']['target'].update(sourceOffset=90, recordType=0x203, recordSha256='c'*64)
        dop['layout']['internalConstraintReference'] = ref; dop['layout']['physicalConstraintReference'] = None
        target['layout']['scaleConstraints']['scales'] = [scale()]; target['layout']['scaleConstraints']['declaredScales'] = 1
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); layout = root/'layouts.jsonl'
            layout.write_text('\n'.join(json.dumps(r) for r in (dop, target)), encoding='utf8')
            counts = write_constraint_probes(layout, root/'bindings', root/'probes', kernel_sha256=PROFILE)
            self.assertEqual(counts['internalConstraintReference:bound'], 1)
            self.assertEqual(counts['physicalConstraintReference:constraint-probe-no-declared-constraint'], 1)
            r = json.loads((root/'probes').read_text(encoding='utf8')); self.assertIsNone(r['fixtureBlockedReason'])
            self.assertEqual(len(r['probes']), 120)
            for probe in r['probes']:
                result = probe['result']; self.assertTrue(result['ok'], result)
                for field in ('constraintSemanticsVerified', 'runtimeConstraintSelectionVerified', 'runtimeConfigurationVerified',
                              'physicalConversionQualified', 'actualVehicleResponse', 'ecuApplicabilityVerified', 'executionEnabled', 'callerGatesVerified'):
                    self.assertFalse(result[field])
            ref['indexLookup']['target']['recordSha256'] = 'e'*64
            layout.write_text('\n'.join(json.dumps(r) for r in (dop, target)), encoding='utf8')
            counts = write_constraint_probes(layout, root/'bad-bindings', root/'bad-probes', kernel_sha256=PROFILE)
            self.assertNotIn('internalConstraintReference:bound', counts)
            binding = json.loads((root/'bad-bindings').read_text(encoding='utf8').splitlines()[0])
            self.assertFalse(binding['binding']['ok'])

    def test_audit_requires_pinned_layout_and_indexes_before_creating_output(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)/'audit'
            with self.assertRaisesRegex(ValueError, 'constraint-probes-require-layouts-key-indexes'):
                audit([], [], output, constraint_probes=True)
            self.assertFalse(output.exists())
