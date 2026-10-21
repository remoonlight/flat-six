import copy
import json
import math
from pathlib import Path
import struct
import tempfile
import unittest

from scripts.diagnostics.decoder_reference import reference_piwis_unit_conversion
from scripts.diagnostics.piwis_database import REQUEST_KERNEL_SHA256 as PROFILE, audit
from scripts.diagnostics.piwis_units import probe_unit_conversion, unit_profile, write_unit_probes
from scripts.diagnostics.tests.test_piwis_response_numeric import fixture


def unit(offset=90, factor=1.0, shift=0.0, dimensions=(0, 0, 0, 0, 1, 0, 0), name='K'):
    _, row = fixture()
    row.update(sourceOffset=offset, recordSha256=f'{offset:064x}')
    row['layout'] = {'recordType': 0x102, 'fieldLayoutComplete': True,
        'nativeField48RawHex': struct.pack('<d', factor).hex(),
        'nativeField50RawHex': struct.pack('<d', shift).hex(),
        'displayName': {'pool': 'UStringData', 'resolved': True, 'text': name, 'token': 18},
        'physicalDimension': None if dimensions is None else {'recordType': 0x10c,
            'fields': [{'field': 'shortName', 'pool': 'AStringData', 'resolved': True, 'text': 'dimension'}],
            'nativeExponentFieldsRaw': [int(x).to_bytes(4, 'little', signed=True).hex() for x in dimensions]}}
    return row


class PiwisUnitTests(unittest.TestCase):
    def convert(self, source, target, value, kind=5, **options):
        return probe_unit_conversion(source, target, value, kind, kernel_sha256=PROFILE, **options)

    def test_temperature_both_directions_and_offset_order(self):
        kelvin, celsius, fahrenheit = unit(), unit(91, shift=-273.15, name='°C'), unit(92, 1.8, -459.67, name='°F')
        for source, target in ((kelvin, celsius), (celsius, fahrenheit), (fahrenheit, kelvin)):
            for value in (-40.0, 0.0, 1234.5):
                r = self.convert(source, target, value)
                self.assertTrue(r['ok'], r)
                self.assertEqual(r['candidateValue'], reference_piwis_unit_conversion(source['layout'], target['layout'], value, 5))
        self.assertAlmostEqual(self.convert(celsius, fahrenheit, 100.0)['candidateValue'], 212.0)

    def test_preserves_input_value_type_and_independent_rounding(self):
        source, target = unit(), unit(91, .1, -.2)
        for kind, values in ((4, (1.5, 100.1, 2**-140)), (5, (1234.5, -1.2)), (7, (-1, 100)), (11, (0, 100, 2**32-1))):
            for value in values:
                r = self.convert(source, target, value, kind)
                self.assertTrue(r['ok'], r)
                self.assertEqual(r['nativeDataType'], kind)
                self.assertEqual(r['candidateValue'], reference_piwis_unit_conversion(source['layout'], target['layout'], value, kind))
        self.assertEqual(self.convert(source, target, 0, 11)['candidateValue'], 0)
        target = unit(91, shift=-1.8)
        self.assertEqual(self.convert(source, target, 0, 11)['candidateValue'], 2**32-1)

    def test_same_record_bypasses_zero_factor_and_missing_dimension(self):
        source = unit(factor=0, dimensions=None)
        r = self.convert(source, copy.deepcopy(source), -0.0)
        self.assertTrue(r['ok'], r)
        self.assertEqual(r['conversionKind'], 'same-retained-unit')
        self.assertFalse(r['dimensionCompared'])
        self.assertEqual(math.copysign(1, r['candidateValue']), -1)
        target = copy.deepcopy(source); target['sourceOffset'] += 1
        r = self.convert(source, target, 100.0, check_dimensions=False)
        self.assertEqual(r['reason'], 'unit-probe-source-factor-zero')

    def test_dimension_flag_requires_all_seven_exponents_or_skips_comparison(self):
        source = unit()
        for index in range(7):
            dims = [0, 0, 0, 0, 1, 0, 0]; dims[index] += 1
            target = unit(91, dimensions=dims)
            self.assertEqual(self.convert(source, target, 1)['reason'], 'unit-probe-dimension-mismatch')
            self.assertTrue(self.convert(source, target, 1, check_dimensions=False)['ok'])
        target = unit(91, dimensions=None)
        self.assertEqual(self.convert(source, target, 1)['reason'], 'unit-probe-dimension-missing')
        self.assertTrue(self.convert(source, target, 1, check_dimensions=False)['ok'])
        self.assertEqual(self.convert(source, target, 1, check_dimensions=0)['reason'], 'unit-probe-dimension-flag-invalid')

    def test_overflows_invalid_coefficients_and_input_types_blocked(self):
        source = unit()
        for value, kind, target in ((0, 7, unit(91, shift=2**31)), (0, 11, unit(91, shift=2**63)),
                                  (1, 4, unit(91, 1e40)), (1e300, 5, unit(91, 1e300))):
            r = self.convert(source, target, value, kind)
            self.assertFalse(r['ok'], r); self.assertNotIn('candidateValue', r)
        for value, kind in ((True, 7), (1.0, 11), (-1, 11), (2**2000, 7), (2**2000, 5), (float('inf'), 5), (0, 16)):
            self.assertFalse(self.convert(source, unit(91), value, kind)['ok'])
        self.assertEqual(self.convert(unit(factor=float('nan')), unit(91), 1)['reason'], 'unit-probe-coefficient-not-finite')

    def test_native_dimension_name_compared_before_exponents(self):
        source, target = unit(), unit(91)
        target['layout']['physicalDimension']['fields'][0]['text'] = 'Dimension'
        self.assertEqual(self.convert(source, target, 1)['reason'], 'unit-probe-dimension-mismatch')
        self.assertTrue(self.convert(source, target, 1, check_dimensions=False)['ok'])
        target['layout']['physicalDimension']['fields'][0]['resolved'] = False
        self.assertEqual(self.convert(source, target, 1)['reason'], 'unit-probe-dimension-name-unqualified')

    def test_source_kernel_layout_and_raw_byte_validation(self):
        source = unit()
        for change in ({'source': None}, {'recordSha256': 'bad'}, {'layout': []}):
            changed = copy.deepcopy(source); changed.update(change)
            self.assertFalse(self.convert(changed, unit(91), 1)['ok'])
        for field, value in (('physicalDimension', 1), ('physicalDimension', {'recordType': 0x10c, 'nativeExponentFieldsRaw': []}),
                             ('nativeField48RawHex', '00'), ('fieldLayoutComplete', False)):
            changed = copy.deepcopy(source); changed['layout'][field] = value
            self.assertFalse(self.convert(changed, unit(91), 1)['ok'])
        with self.assertRaisesRegex(ValueError, 'kernel-profile-mismatch'):
            unit_profile(source, kernel_sha256='0'*64)

    def test_every_success_preserves_runtime_and_vehicle_qualification_gaps(self):
        for target in (unit(), unit(91, 2)):
            r = self.convert(unit(), target, 2)
            self.assertTrue(r['ok'], r)
            for name in ('unitSemanticsVerified', 'runtimeUnitSelectionVerified', 'runtimeUnitIdentityVerified',
                         'physicalConversionQualified', 'ecuApplicabilityVerified', 'executionEnabled',
                         'runtimeFloatingEnvironmentVerified', 'actualVehicleResponse'):
                self.assertFalse(r[name], name)

    def test_writer_declared_index_bindings_missing_refs_and_explicit_pairs(self):
        parameter, dop = fixture(); source = unit()
        dop.update(sourceOffset=200, recordSha256='d'*64)
        ref = parameter['objectReference']; ref['indexLookup']['target'].update(recordType=0x102, recordSha256=source['recordSha256'])
        dop['layout']['unitReference'] = ref
        no_unit = copy.deepcopy(dop); no_unit['sourceOffset'] = 201; no_unit['layout']['unitReference'] = None
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory); layouts = path/'layouts.jsonl'
            layouts.write_text(''.join(json.dumps(r)+'\n' for r in (source, unit(91, 2), dop, no_unit)))
            counts = write_unit_probes(layouts, path/'bindings.jsonl', path/'pairs.jsonl', kernel_sha256=PROFILE)
            self.assertEqual(counts['unitRecords'], 2); self.assertEqual(counts['explicitUnitPairs'], 4)
            self.assertEqual(counts['declaredUnitBindings'], 1); self.assertEqual(counts['unit-probe-no-declared-unit'], 1)
            self.assertEqual(counts['success'], 36)
            rows = [json.loads(x) for x in (path/'bindings.jsonl').read_text().splitlines()]
            self.assertTrue(rows[0]['binding']['staticIndexTargetMatched'])
            self.assertFalse(rows[0]['runtimeUnitSelectionVerified'])

    def test_writer_wrong_record_hash_type_and_duplicate_sources_rejected(self):
        parameter, dop = fixture(); source = unit(); dop['sourceOffset'] = 200
        ref = parameter['objectReference']; ref['indexLookup']['target'].update(recordType=0x102, recordSha256='f'*64)
        dop['layout']['unitReference'] = ref
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory); layouts = path/'layouts.jsonl'
            layouts.write_text(''.join(json.dumps(r)+'\n' for r in (source, dop)))
            counts = write_unit_probes(layouts, path/'bindings.jsonl', path/'pairs.jsonl', kernel_sha256=PROFILE)
            self.assertNotIn('declaredUnitBindings', counts)
            layouts.write_text((json.dumps(source)+'\n')*2)
            with self.assertRaisesRegex(ValueError, 'duplicate-layout-record'):
                write_unit_probes(layouts, path/'b2.jsonl', path/'p2.jsonl', kernel_sha256=PROFILE)

    def test_cli_option_requires_layouts_and_key_indexes(self):
        with self.assertRaisesRegex(ValueError, 'unit-probes-require-layouts-key-indexes'):
            audit([], [], Path('not-created'), unit_probes=True)
