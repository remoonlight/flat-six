import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from scripts.diagnostics.piwis_constraint_flow import (
    probe_dop_constraint_flow, probe_response_constraint_flow, write_constraint_flow_probes)
from scripts.diagnostics.piwis_constraint_candidates import CONFIG_FIELDS
from scripts.diagnostics.piwis_database import REQUEST_KERNEL_SHA256 as PROFILE, audit
from scripts.diagnostics.tests.test_piwis_constraint_candidates import constraint, interval, scale
from scripts.diagnostics.tests.test_piwis_fixed_values import fixed_dop
from scripts.diagnostics.tests.test_piwis_numeric import dop as numeric_dop
from scripts.diagnostics.tests.test_piwis_response_numeric import fixture
from scripts.diagnostics.tests.test_piwis_texttable import enum_dop


def flow_fixture():
    parameter, dop = fixture()
    dop['layout'].update(internalConstraintReference=None, physicalConstraintReference=None)
    return parameter, dop, {}


def attach(dop, rows, field, row):
    row = copy.deepcopy(row)
    row.update(sourceOffset=110 + 60 * len(rows), record=4 + len(rows), recordSha256=str(4 + len(rows))*64)
    rows[(row['source']['file'], row['sourceOffset'])] = row
    reference = copy.deepcopy(fixture()[0]['objectReference'])
    target = reference['indexLookup']['target']
    target.update({k: row[k] for k in ('sourceOffset', 'compressedBytes', 'recordSha256', 'record')})
    target['recordType'] = 0x203
    dop['layout'][field] = reference
    return row


class PiwisConstraintFlowTests(unittest.TestCase):
    def policy(self, **changes):
        options = {'kernel_sha256': PROFILE, 'configuration': dict(zip(CONFIG_FIELDS, (True, False, True, True))),
            'native_property_mode': 1, 'native_parameter_data_type': 11, 'initial_native_range_info': 0x606}
        options.update(changes)
        return options

    def run_flow(self, dop, rows, payload=b'\x00\x32', **changes):
        return probe_dop_constraint_flow(dop, rows, payload, 0, **self.policy(**changes))

    def test_both_checks_use_different_values_and_order(self):
        _, dop, rows = flow_fixture(); dop['layout'].update(numeric_dop())
        attach(dop, rows, 'internalConstraintReference', constraint())
        physical = attach(dop, rows, 'physicalConstraintReference', constraint(5))
        physical['layout']['interval'] = interval(-40, -30, 5)
        result = self.run_flow(dop, rows)
        self.assertTrue(result['ok'], result)
        self.assertEqual([s['stage'] for s in result['stages']], ['field-decode', 'pre-check', 'conversion', 'post-check'])
        self.assertEqual(result['stages'][1]['classification']['inputValue'], 50)
        self.assertEqual(result['stages'][3]['classification']['inputValue'], -35.0)
        self.assertEqual(result['lastNativeRangeInfo'], 0x602)

    def test_pre_exception_stops_before_conversion_or_post_resolution(self):
        _, dop, rows = flow_fixture()
        attach(dop, rows, 'internalConstraintReference', constraint())['layout']['interval'] = interval(0, 10)
        dop['layout']['physicalConstraintReference'] = {'unresolved': True}
        with patch('scripts.diagnostics.piwis_constraint_flow.probe_dop_numeric', side_effect=AssertionError('must not convert')):
            result = self.run_flow(dop, rows)
        self.assertEqual(result['reason'], 'constraint-flow-pre-check-native-exception')
        self.assertNotIn('candidateValue', result)
        self.assertEqual(result['lastNativeRangeInfo'], 0x605)
        self.assertFalse(result['callerLocalCatchCoversConstraintCall'])
        self.assertFalse(result['higherCallerExceptionHandlingVerified'])

    def test_mode_zero_continues_after_invalid_pre_then_post_overwrites(self):
        _, dop, rows = flow_fixture()
        attach(dop, rows, 'internalConstraintReference', constraint())['layout']['interval'] = interval(0, 10)
        attach(dop, rows, 'physicalConstraintReference', constraint())
        result = self.run_flow(dop, rows, native_property_mode=0)
        self.assertTrue(result['ok'], result)
        self.assertFalse(result['stages'][1]['classification']['candidatePolicyValid'])
        self.assertEqual(result['lastNativeRangeInfo'], 0x602)

    def test_post_exception_does_not_publish_final_candidate(self):
        _, dop, rows = flow_fixture()
        attach(dop, rows, 'physicalConstraintReference', constraint())['layout']['interval'] = interval(0, 10)
        result = self.run_flow(dop, rows)
        self.assertEqual(result['reason'], 'constraint-flow-post-check-native-exception')
        self.assertEqual(result['stages'][2]['result']['candidateValue'], 50)
        self.assertNotIn('candidateValue', result)

    def test_computed_bypass_is_lazy_but_exact_record_still_required(self):
        _, dop, rows = flow_fixture()
        pre = attach(dop, rows, 'internalConstraintReference', constraint())
        pre['layout'].update(nativeComputedRaw=1, interval=None, scaleConstraints=None)
        result = self.run_flow(dop, rows)
        self.assertTrue(result['ok'], result)
        self.assertEqual(result['stages'][1]['reason'], 'declared-computed-constraint')
        self.assertEqual(result['lastNativeRangeInfo'], 0x606)
        pre['recordSha256'] = 'f'*64
        self.assertEqual(self.run_flow(dop, rows)['reason'], 'response-probe-dop-record-identity-mismatch')

    def test_mode_three_and_explicit_type_twenty_skip_unresolved_references(self):
        _, dop, rows = flow_fixture()
        dop['layout'].update(internalConstraintReference={'bad': True}, physicalConstraintReference={'bad': True})
        for changes in ({'native_property_mode': 3}, {'native_parameter_data_type': 20}):
            result = self.run_flow(dop, rows, **changes)
            self.assertTrue(result['ok'], result)
            self.assertEqual(result['lastNativeRangeInfo'], 0x606)
            self.assertFalse(result['parameterDataTypeResolutionVerified'])
        self.assertFalse(self.run_flow(dop, rows)['ok'])

    def test_wrong_input_limit_type_and_unqualified_computed_flag_block(self):
        _, dop, rows = flow_fixture()
        row = attach(dop, rows, 'internalConstraintReference', constraint(5))
        self.assertEqual(self.run_flow(dop, rows)['reason'], 'constraint-probe-limit-type-unqualified')
        for flag in (2, True, None):
            row['layout']['nativeComputedRaw'] = flag
            self.assertEqual(self.run_flow(dop, rows)['reason'], 'constraint-flow-computed-flag-unqualified')

    def test_byte_text_and_enum_checks_do_not_guess_input_types(self):
        _, dop, rows = flow_fixture(); dop['layout'].update(fixed_dop(16))
        self.assertEqual(self.run_flow(dop, rows, b'A\x00')['candidateValue'], 'A\x00')
        pre = attach(dop, rows, 'internalConstraintReference', constraint())
        self.assertEqual(self.run_flow(dop, rows, b'A\x00')['reason'], 'constraint-flow-non-numeric-pre-check-unqualified')
        pre['layout']['nativeComputedRaw'] = 1
        self.assertTrue(self.run_flow(dop, rows, b'A\x00')['ok'])
        attach(dop, rows, 'physicalConstraintReference', constraint())
        self.assertEqual(self.run_flow(dop, rows, b'A\x00')['reason'], 'constraint-flow-non-numeric-post-check-unqualified')
        dop['layout'].update(enum_dop()); dop['layout']['internalConstraintReference'] = None
        self.assertEqual(self.run_flow(dop, rows, b'\x01')['reason'], 'constraint-flow-non-numeric-post-check-unqualified')
        list(rows.values())[-1]['layout']['nativeComputedRaw'] = 1
        self.assertEqual(self.run_flow(dop, rows, b'\x01')['candidateValue'], '关')

    def test_signed_integer_conversion_uses_actual_result_type(self):
        _, dop, rows = flow_fixture(); dop['layout'].update(numeric_dop(target=1))
        dop['layout']['codedType']['nativeEncodingFieldsRaw'][:2] = [0, 3]
        attach(dop, rows, 'internalConstraintReference', constraint(7))['layout']['interval'] = interval(-100, 100, 7)
        attach(dop, rows, 'physicalConstraintReference', constraint(7))['layout']['interval'] = interval(-100, 100, 7)
        result = self.run_flow(dop, rows, b'\xff\xff')
        self.assertTrue(result['ok'], result)
        self.assertEqual(result['stages'][3]['classification']['nativeDataType'], 7)
        self.assertEqual(result['conversion']['nativeResultDataType'], 7)

    def test_initial_state_preservation_scale_policy_and_corrupt_policy(self):
        _, dop, rows = flow_fixture()
        row = attach(dop, rows, 'internalConstraintReference', constraint(scales=[scale(0x604, 0, 100)]))
        result = self.run_flow(dop, rows, native_property_mode=0)
        self.assertTrue(result['ok'], result)
        self.assertEqual(result['lastNativeRangeInfo'], 0x604)
        for policy in ({'native_property_mode': True}, {'native_parameter_data_type': True},
                       {'initial_native_range_info': 99}, {'configuration': {}}, {'kernel_sha256': 'a'*64}):
            self.assertFalse(self.run_flow(dop, rows, **policy)['ok'])
        row['layout']['nativeComputedRaw'] = 1
        self.assertEqual(self.run_flow(dop, rows, initial_native_range_info=0x603)['lastNativeRangeInfo'], 0x603)

    def test_response_declared_bit_position_and_truncation_keep_scope_false(self):
        parameter, dop, rows = flow_fixture(); parameter['bitPosition'] = 3
        payload = b'\xa7'*3 + (50 << 3).to_bytes(3, 'big')
        result = probe_response_constraint_flow(parameter, dop, rows, payload, **self.policy())
        self.assertTrue(result['ok'], result)
        self.assertEqual(result['candidateValue'], 50)
        self.assertEqual(result['declaredBytePosition'], 3)
        for field in ('executionEnabled', 'actualVehicleResponse', 'physicalConversionQualified',
                      'parameterBindingVerified', 'responseEnvelopeVerified', 'runtimeConfigurationVerified'):
            self.assertFalse(result[field])
        result = probe_response_constraint_flow(parameter, dop, rows, payload[:-1], **self.policy())
        self.assertEqual(result['reason'], 'numeric-probe-truncated-field')

    def test_writer_rejoins_declared_constraints_and_cli_flag_is_explicit(self):
        parameter, dop, rows = flow_fixture()
        attach(dop, rows, 'internalConstraintReference', constraint())
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder); layouts = path / 'layouts.jsonl'; responses = path / 'responses.jsonl'
            layouts.write_text('\n'.join(json.dumps(r) for r in [dop, *rows.values()]) + '\n', encoding='utf8')
            responses.write_text(json.dumps({'source': dop['source'], 'sourceOffset': 10,
                'recordSha256': 'd'*64, 'parameters': {'parsedParameters': [parameter]}}) + '\n', encoding='utf8')
            counts = write_constraint_flow_probes(layouts, path / 'flow.jsonl', kernel_sha256=PROFILE,
                response_path=responses, response_output_path=path / 'response-flow.jsonl')
            self.assertEqual(counts['dopDefinitions'], 1)
            self.assertEqual(counts['responseParameters'], 1)
            self.assertEqual(len(json.loads((path / 'flow.jsonl').read_text())['probes']), 72)
            self.assertGreater(counts['blocked:constraint-flow-pre-check-native-exception'], 0)
        with self.assertRaisesRegex(ValueError, 'constraint-flow-probes-require-layouts-key-indexes'):
            audit([], [], Path('unused'), constraint_flow_probes=True)


if __name__ == '__main__':
    unittest.main()
