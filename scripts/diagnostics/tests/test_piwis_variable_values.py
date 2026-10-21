import unittest

from scripts.diagnostics.decoder_reference import reference_piwis_variable_value
from scripts.diagnostics.piwis_database import REQUEST_KERNEL_SHA256 as PROFILE
from scripts.diagnostics.piwis_numeric import probe_dop_numeric, synthetic_dop_probes
from scripts.diagnostics.piwis_response_numeric import synthetic_response_parameter_probes
from scripts.diagnostics.piwis_recursive_numeric import synthetic_recursive_structure_probes
from scripts.diagnostics.tests.test_piwis_fixed_values import fixed_dop
from scripts.diagnostics.tests.test_piwis_response_numeric import fixture as response_fixture
from scripts.diagnostics.tests.test_piwis_recursive_numeric import fixture as recursive_fixture


def variable_dop(minimum=1, maximum=8, text=False, order=1):
    layout = fixed_dop(text=text, order=order)
    layout['codedType'] = {'present': True, 'recordType': 0x23, 'nativeKind': 1,
        'minLength': minimum, 'maxLength': maximum, 'nativeTerminationRaw': 0,
        'nativeEncodingFieldsRaw': [4, 8, order, 0] if text else [7, 11, order, 0]}
    return layout


class PiwisVariableValueTests(unittest.TestCase):
    def test_byte_minimum_and_maximum_match_counter_reference(self):
        for minimum, maximum in ((1, 1), (1, 8), (3, 5), (1, 4091), (1, 65535)):
            for text in (True, False):
                for order in (0, 1):
                    layout = variable_dop(minimum, maximum, text, order)
                    for size in sorted({0, minimum-1, minimum, maximum-1, maximum, maximum+1, 4093}):
                        if not 0 <= size <= 4093:
                            continue
                        data = b'pp' + bytes((i*17+128) % 256 for i in range(size))
                        result = probe_dop_numeric(layout, data, 2, kernel_sha256=PROFILE)
                        if size < minimum:
                            self.assertEqual(result['reason'], 'variable-value-probe-below-minimum-input')
                            continue
                        self.assertTrue(result['ok'], result['reason'])
                        for key, value in reference_piwis_variable_value(layout['codedType'], data, 2).items():
                            self.assertEqual(result[key], value)
                        self.assertFalse(result['nativeCursorVerified'])
                        self.assertFalse(result['executionEnabled'])

    def test_extra_input_is_capped_instead_of_rejected(self):
        result = probe_dop_numeric(variable_dop(1, 2), b'\x81\x00\xff', 0, kernel_sha256=PROFILE)
        self.assertTrue(result['ok'], result['reason'])
        self.assertEqual(result['candidateValue'], '8100')
        self.assertEqual(result['availableInputBytes'], 3)
        self.assertEqual(result['extractedFieldBytes'], 2)

    def test_variable_ascii_zero_is_trimmed_after_minimum_and_maximum(self):
        layout = variable_dop(3, 5, text=True)
        result = probe_dop_numeric(layout, b'\x00ABC\xffZ', 0, kernel_sha256=PROFILE)
        self.assertTrue(result['ok'], result['reason'])
        self.assertEqual(result['candidateValue'], '')
        self.assertEqual(result['fieldRawHex'], '00414243ff')
        self.assertEqual(result['decodedFieldRawHex'], '')
        self.assertTrue(result['asciiZeroTruncated'])
        result = probe_dop_numeric(variable_dop(1, 2, text=True), b'A\x80\x00', 0, kernel_sha256=PROFILE)
        self.assertEqual(result['candidateValue'], 'A\x80')
        self.assertFalse(result['asciiZeroTruncated'])
        fixed = probe_dop_numeric(fixed_dop(32), b'A\x00\x80\xff', 0, kernel_sha256=PROFILE)
        variable = probe_dop_numeric(variable_dop(1, 4, text=True), b'A\x00\x80\xff', 0, kernel_sha256=PROFILE)
        self.assertEqual(fixed['candidateValue'], 'A\x00\x80\xff')
        self.assertEqual(variable['candidateValue'], 'A')

    def test_unknown_termination_lengths_masks_and_other_encodings_rejected(self):
        changes = ({'minLength': -1}, {'minLength': True}, {'maxLength': True},
            {'minLength': 9, 'maxLength': 8}, {'maxLength': 1 << 32},
            {'nativeTerminationRaw': 3}, {'nativeTerminationRaw': -1}, {'nativeTerminationRaw': False},
            {'nativeKind': True}, {'bitMaskPresent': True}, {'bitMaskPresent': 0}, {'bitMaskRawHex': 'ff'},
            {'nativeEncodingFieldsRaw': [6, 6, 1, 0]}, {'nativeEncodingFieldsRaw': [4, 8, True, 0]})
        for change in changes:
            layout = variable_dop(); layout['codedType'].update(change)
            self.assertFalse(probe_dop_numeric(layout, b'A'*16, 0, kernel_sha256=PROFILE)['ok'], change)
        layout = variable_dop(); layout['compuPrefix']['compuMethod']['nativeCategory'] = 1
        self.assertFalse(probe_dop_numeric(layout, b'A', 0, kernel_sha256=PROFILE)['ok'])

    def test_zero_minimum_and_maximum_follow_empty_assignments(self):
        for text in (False, True):
            for maximum in (0, 3, 4094):
                layout = variable_dop(0, maximum, text)
                for data in (b'', b'A\x00\xff'):
                    result = probe_dop_numeric(layout, data, 0, kernel_sha256=PROFILE)
                    self.assertTrue(result['ok'], result['reason'])
                    for key, value in reference_piwis_variable_value(layout['codedType'], data, 0).items():
                        self.assertEqual(result[key], value)
                    self.assertFalse(result['nativeCursorVerified'])
                self.assertTrue(all(p['result']['ok'] for p in synthetic_dop_probes(layout, kernel_sha256=PROFILE)))

    def test_empty_root_and_recursive_field_are_valid_at_declared_input_end(self):
        parameter, dop = response_fixture(); dop['layout'] = variable_dop(0, 10)
        row = synthetic_response_parameter_probes(parameter, dop, kernel_sha256=PROFILE)[0]
        self.assertEqual(row['pduHex'], 'a7a7a7')
        self.assertTrue(row['result']['ok'], row['result']['reason'])
        self.assertEqual(row['result']['candidateValue'], '')
        values = recursive_fixture(); values[3]['layout'] = variable_dop(0, 10)
        row = synthetic_recursive_structure_probes(*values, kernel_sha256=PROFILE)[0]
        self.assertTrue(row['result']['ok'], row['result']['reason'])
        self.assertEqual(len(bytes.fromhex(row['pduHex'])), 12)
        self.assertEqual(row['result']['extractedFieldBytes'], 0)

    def test_position_and_pdu_guards_remain(self):
        layout = variable_dop()
        for offset in (-1, True, 4095):
            self.assertFalse(probe_dop_numeric(layout, b'A', offset, kernel_sha256=PROFILE)['ok'])
        for bit in (True, 1, 7):
            self.assertFalse(probe_dop_numeric(layout, b'A', 0, bit, kernel_sha256=PROFILE)['ok'])
        self.assertFalse(probe_dop_numeric(layout, b'A'*4096, 0, kernel_sha256=PROFILE)['ok'])
        self.assertFalse(probe_dop_numeric(layout, b'A', 0, kernel_sha256='0'*64)['ok'])

    def test_marker_at_each_position_and_missing_marker_match_native_cap(self):
        for termination, marker in ((1, 0), (2, 255)):
            for text in (True, False):
                for order in (0, 1):
                    layout = variable_dop(1, 8, text, order)
                    layout['codedType']['nativeTerminationRaw'] = termination
                    for position in range(10):
                        data = b'pp' + b'A'*position + bytes((marker,)) + b'B'*3
                        result = probe_dop_numeric(layout, data, 2, kernel_sha256=PROFILE)
                        self.assertTrue(result['ok'], result['reason'])
                        for key, value in reference_piwis_variable_value(layout['codedType'], data, 2).items():
                            self.assertEqual(result[key], value)
                        self.assertEqual(result['terminationFound'], position < 8)
                        self.assertFalse(result['nativeCursorVerified'])
                    result = probe_dop_numeric(layout, b'AB', 0, kernel_sha256=PROFILE)
                    self.assertTrue(result['ok'])
                    self.assertFalse(result['terminationFound'])
                    self.assertEqual(result['extractedFieldBytes'], 2)

    def test_marker_minimum_is_available_input_and_ascii_zero_is_separate(self):
        layout = variable_dop(6, 31, text=True); layout['codedType']['nativeTerminationRaw'] = 1
        result = probe_dop_numeric(layout, b'\x00ABCDE', 0, kernel_sha256=PROFILE)
        self.assertTrue(result['ok'], result['reason'])
        self.assertEqual(result['candidateValue'], '')
        self.assertFalse(result['asciiZeroTruncated'])
        self.assertTrue(result['terminationFound'])
        self.assertFalse(probe_dop_numeric(layout, b'\x00ABCD', 0, kernel_sha256=PROFILE)['ok'])
        layout['codedType']['nativeTerminationRaw'] = 2
        result = probe_dop_numeric(layout, b'A\x00BCD\xffZ', 0, kernel_sha256=PROFILE)
        self.assertEqual(result['fieldRawHex'], '4100424344')
        self.assertEqual(result['candidateValue'], 'A')
        self.assertTrue(result['asciiZeroTruncated'])
        self.assertTrue(result['terminationFound'])

    def test_root_fixtures_keep_actual_end_without_suffix_padding(self):
        parameter, dop = response_fixture(); dop['layout'] = variable_dop(1, 2)
        rows = synthetic_response_parameter_probes(parameter, dop, kernel_sha256=PROFILE)
        for row in rows:
            result = row['result']; data = bytes.fromhex(row['pduHex'])
            if row['case'] == 'below-minimum':
                self.assertEqual(len(data), 3)
                self.assertFalse(result['ok'])
            else:
                self.assertTrue(result['ok'], result['reason'])
                for key, value in reference_piwis_variable_value(dop['layout']['codedType'], data, 3).items():
                    self.assertEqual(result[key], value)
        maximum = next(row for row in rows if row['case'] == 'maximum-plus-one')
        self.assertEqual(maximum['pduHex'], 'a7a7a75a5a51')
        self.assertEqual(maximum['result']['candidateValue'], '5a5a')

    def test_recursive_variable_field_keeps_absolute_source_position(self):
        values = recursive_fixture(); values[3]['layout'] = variable_dop(1, 4, text=True)
        for row in synthetic_recursive_structure_probes(*values, kernel_sha256=PROFILE):
            result = row['result']; data = bytes.fromhex(row['pduHex'])
            if row['case'] == 'below-minimum':
                self.assertFalse(result['ok'])
            else:
                self.assertTrue(result['ok'], result['reason'])
                self.assertEqual(result['absoluteBytePosition'], 12)
                self.assertEqual(result['candidateValue'], reference_piwis_variable_value(values[3]['layout']['codedType'], data, 12)['candidateValue'])
                self.assertFalse(result['structureEnvelopeVerified'])

    def test_synthetic_resource_guard_keeps_explicit_blocked_result(self):
        rows = synthetic_dop_probes(variable_dop(4096, 4097), kernel_sha256=PROFILE)
        self.assertEqual(len(rows), 1)
        self.assertFalse(rows[0]['result']['ok'])
        self.assertEqual(rows[0]['result']['reason'], 'variable-value-probe-below-minimum-input')


if __name__ == '__main__':
    unittest.main()
