import copy
import unittest

from scripts.diagnostics.decoder_reference import reference_piwis_texttable, reference_piwis_unsigned
from scripts.diagnostics.piwis_database import REQUEST_KERNEL_SHA256 as PROFILE
from scripts.diagnostics.piwis_numeric import probe_dop_numeric, synthetic_dop_probes
from scripts.diagnostics.piwis_texttable import texttable_profile


def text(value):
    return {'nativeType': 14, 'nativeValueDefined': True, 'string': {'pool': 'UStringData',
        'resolved': True, 'nullReference': False, 'text': value, 'token': 123}}


def enum_dop(default=None):
    rows = []
    for value, label in ((1, '关'), (4, '开'), (128, '条件未满足')):
        bound = {'recordType': 0x37, 'nativeInterval': 0x6d02,
            'value': {'nativeType': 11, 'nativeValueDefined': True, 'rawHex': value.to_bytes(4, 'little').hex()}}
        rows.append({'recordType': 0x19, 'lowerLimitCoded': copy.deepcopy(bound),
            'upperLimitCoded': copy.deepcopy(bound), 'nativeField88': text(label),
            'nativeFielda8': text('unrelated inverse'), 'nativeFieldc8': text('unrelated inverse')})
    return {'recordType': 0x2c, 'fieldLayoutComplete': True,
        'codedType': {'present': True, 'recordType': 0x23, 'nativeKind': 2, 'bitLength': 8,
            'nativeEncodingFieldsRaw': [1, 11, 1, 0], 'bitMaskPresent': False},
        'physicalType': {'nativeBaseDataType': 4},
        'codedToPhysicalIndexMap': [{'key': 1, 'value': 0}, {'key': 4, 'value': 1}, {'key': 128, 'value': 2}],
        'physicalToCodedIndexMap': [{'key': 4, 'value': 0}],
        'compuPrefix': {'compuMethod': {'nativeCategory': 3,
            'nativeField18': {'default': text('wrong direction')},
            'nativeField28': {'recordType': 5, 'scales': {'declaredScales': 3, 'scales': rows},
                'default': text(default) if default is not None else {'nativeType': 255, 'nativeValueDefined': False}}}}}


class PiwisTexttableTests(unittest.TestCase):
    def test_ordinary_mask_feeds_enum_and_synthetic_points_are_reachable(self):
        layout = enum_dop()
        layout['codedType'].update(bitMaskPresent=True, bitMaskRawHex='01')
        for raw in range(256):
            data = bytes([raw])
            masked = reference_piwis_unsigned(layout['codedType'], data, 0)
            expected = reference_piwis_texttable(layout, masked)
            actual = probe_dop_numeric(layout, data, 0, kernel_sha256=PROFILE)
            self.assertEqual(actual['ok'], expected['ok'])
            if actual['ok']:
                self.assertEqual(actual['rawValue'], masked)
                self.assertEqual(actual['candidateValue'], expected['text'])
        probes = synthetic_dop_probes(layout, kernel_sha256=PROFILE)
        points = [p['case'] for p in probes if p['case'].startswith('enum-point-')]
        self.assertEqual(points, ['enum-point-1'])
        self.assertEqual(next(p['result']['reason'] for p in probes if p['case']=='enum-unmapped'),
                         'numeric-probe-texttable-value-unmapped')

    def test_declared_points_match_independent_limit_scan(self):
        layout = enum_dop()
        for raw in range(256):
            actual = probe_dop_numeric(layout, bytes([raw]), 0, kernel_sha256=PROFILE)
            expected = reference_piwis_texttable(layout, raw)
            self.assertEqual(actual['ok'], expected['ok'])
            if expected['ok']:
                self.assertEqual(actual['candidateValue'], expected['text'])
                self.assertEqual(actual['enumSelection']['scaleIndex'], expected['scaleIndex'])
                self.assertEqual(actual['probeKind'], 'texttable-point-enum')
            else:
                self.assertEqual(actual['reason'], 'numeric-probe-texttable-value-unmapped')
                self.assertNotIn('candidateValue', actual)

    def test_default_uses_forward_base_and_preserves_unicode_and_empty_text(self):
        for default in ('未知 / 未提供 🚗', ''):
            layout = enum_dop(default)
            for raw in range(256):
                actual = probe_dop_numeric(layout, bytes([raw]), 0, kernel_sha256=PROFILE)
                expected = reference_piwis_texttable(layout, raw)
                self.assertEqual(actual['candidateValue'], expected['text'])
                self.assertEqual(actual['enumSelection']['kind'], expected['kind'])
        layout = enum_dop();layout['compuPrefix']['compuMethod']['nativeField28'] = None
        result = probe_dop_numeric(layout, b'\x01', 0, kernel_sha256=PROFILE)
        self.assertFalse(result['ok'])
        self.assertEqual(result['reason'], 'numeric-probe-texttable-forward-base-required')

    def test_complete_index_is_checked_before_a_matching_value(self):
        for change in ([], [{'key': 1, 'value': 0}],
            [{'key': 1, 'value': 1}, {'key': 4, 'value': 0}, {'key': 128, 'value': 2}],
            [{'key': 1, 'value': 0}, {'key': 4, 'value': True}, {'key': 128, 'value': 2}],
            [{'key': 1, 'value': 0}, {'key': 1, 'value': 1}, {'key': 128, 'value': 2}],
            [{'key': 1, 'value': 0}, {'key': 4, 'value': 1}, {'key': 128, 'value': 3}]):
            layout = enum_dop();layout['codedToPhysicalIndexMap'] = change
            result = probe_dop_numeric(layout, b'\x01', 0, kernel_sha256=PROFILE)
            self.assertFalse(result['ok'])
            self.assertNotIn('candidateValue', result)

    def test_open_range_duplicate_and_unknown_value_types_are_rejected(self):
        for field, value in (('nativeInterval', 0x6d01), ('nativeInterval', 0x6d03),
            ('value', {'nativeType': 3, 'nativeValueDefined': True, 'rawHex': '0100'}),
            ('value', {'nativeType': 11, 'nativeValueDefined': True, 'rawHex': '00 01 00'}),
            ('value', {'nativeType': 11, 'nativeValueDefined': True, 'rawHex': '04000000'})):
            layout = enum_dop()
            layout['compuPrefix']['compuMethod']['nativeField28']['scales']['scales'][0]['lowerLimitCoded'][field] = value
            self.assertFalse(probe_dop_numeric(layout, b'\x04', 0, kernel_sha256=PROFILE)['ok'])
        layout = enum_dop();rows = layout['compuPrefix']['compuMethod']['nativeField28']['scales']['scales']
        rows[1] = copy.deepcopy(rows[0])
        with self.assertRaisesRegex(ValueError, 'point-ambiguous'):
            texttable_profile(layout)

    def test_missing_null_and_unresolved_output_or_default_is_rejected(self):
        for value in (None, {'nativeType': 255, 'nativeValueDefined': False},
            {'nativeType': 1, 'nativeValueDefined': True, 'string': text('x')['string']},
            {**text('x'), 'string': {**text('x')['string'], 'resolved': False}},
            {**text('x'), 'string': {**text('x')['string'], 'nullReference': True}}):
            layout = enum_dop();layout['compuPrefix']['compuMethod']['nativeField28']['scales']['scales'][1]['nativeField88'] = value
            self.assertFalse(probe_dop_numeric(layout, b'\x01', 0, kernel_sha256=PROFILE)['ok'])
        layout = enum_dop();layout['compuPrefix']['compuMethod']['nativeField28']['default'] = {'nativeType': 11, 'nativeValueDefined': True, 'rawHex': '00000000'}
        self.assertFalse(probe_dop_numeric(layout, b'\x01', 0, kernel_sha256=PROFILE)['ok'])

    def test_bit_position_order_truncation_and_source_pin_apply_to_enum(self):
        for order in (0, 1):
            layout = enum_dop();layout['codedType']['nativeEncodingFieldsRaw'][2] = order
            for bit in range(8):
                data = b'PREFIX' + (128 << bit).to_bytes((8 + bit + 7)//8, 'big' if order else 'little')
                actual = probe_dop_numeric(layout, data, 6, bit, kernel_sha256=PROFILE)
                self.assertEqual(actual['candidateValue'], '条件未满足')
                self.assertFalse(probe_dop_numeric(layout, data[:-1], 6, bit, kernel_sha256=PROFILE)['ok'])
        self.assertFalse(probe_dop_numeric(enum_dop(), b'\x01', 0, kernel_sha256='0'*64)['ok'])

    def test_synthetic_cases_include_all_reachable_keys_even_without_zero(self):
        probes = synthetic_dop_probes(enum_dop(), kernel_sha256=PROFILE)
        self.assertTrue(probes[0]['result']['ok'])
        self.assertEqual([p['result']['rawValue'] for p in probes if p['result']['ok']], [1, 4, 128])
        self.assertEqual(probes[-1]['result']['reason'], 'numeric-probe-truncated-field')
        self.assertEqual(probes[-2]['result']['reason'], 'numeric-probe-texttable-value-unmapped')
        for p in probes:
            self.assertFalse(p['actualVehicleResponse'])
            self.assertFalse(p['result']['physicalConversionQualified'])
            self.assertFalse(p['result']['parameterBindingVerified'])
            self.assertFalse(p['result']['executionEnabled'])

    def test_unreachable_points_are_not_truncated_into_another_state(self):
        layout = enum_dop();layout['codedType']['bitLength'] = 3
        probes = synthetic_dop_probes(layout, kernel_sha256=PROFILE)
        self.assertEqual([p['case'] for p in probes if p['case'].startswith('enum-point')], ['enum-point-1', 'enum-point-4'])
        self.assertFalse(probe_dop_numeric(layout, b'\x80', 0, kernel_sha256=PROFILE)['ok'])

    def test_incomplete_scales_and_physical_destination_are_rejected(self):
        for change in (None, {'declaredScales': True, 'scales': []}, {'declaredScales': 0, 'scales': []},
            {'declaredScales': 1, 'scales': [None]}, {'declaredScales': 1, 'scales': []}):
            layout = enum_dop();layout['compuPrefix']['compuMethod']['nativeField28']['scales'] = change
            self.assertFalse(probe_dop_numeric(layout, b'\x01', 0, kernel_sha256=PROFILE)['ok'])
        layout = enum_dop();layout['physicalType']['nativeBaseDataType'] = 5
        self.assertFalse(probe_dop_numeric(layout, b'\x01', 0, kernel_sha256=PROFILE)['ok'])
