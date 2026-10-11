import io
import json
from pathlib import Path
import tempfile
import unittest
from contextlib import redirect_stdout

from scripts.diagnostics.x431_response_contract import audit_internal_response, audit_porsche_mode30_wrapper, GUARDS, main


def direct(frame, capacity=1024, count=None):
    return audit_internal_response({'path': 'direct', 'capacity': capacity,
        'transportReturn': len(bytes.fromhex(frame)) if count is None else count, 'frameHex': frame})


class InternalResponseContractTests(unittest.TestCase):
    def assert_no_data(self, report):
        self.assertIsNone(report['internalBufferHexCandidate'])
        self.assertFalse(report['internalBufferComplete'])
        self.assertIsNone(report['ecuPayloadHex'])
        for key, value in GUARDS.items():
            self.assertEqual(report[key], value)

    def test_status_zero_strips_exactly_one_byte_without_claiming_ecu(self):
        report = direct('0062f1904142')
        self.assertEqual(report['internalBufferHexCandidate'], '62f1904142')
        self.assertEqual(report['nativeReturnCandidate'], 5)
        self.assertEqual(report['nativeCopyBytesCandidate'], 5)
        self.assertTrue(report['internalBufferComplete'])
        self.assertIsNone(report['ecuPayloadHex'])
        for key, value in GUARDS.items():
            self.assertEqual(report[key], value)

    def test_copy_boundary_keeps_complete_count_separate_from_truncated_copy(self):
        for capacity, count, copied in [(1, 2, 1), (1, 3, 1), (2, 3, 2), (2, 4, 2)]:
            with self.subTest(capacity=capacity, count=count):
                report = direct('00'+'42'*(count-1), capacity)
                self.assertEqual(report['nativeReturnCandidate'], count-1)
                self.assertEqual(report['nativeCopyBytesCandidate'], copied)
                self.assertEqual(report['internalBufferComplete'], count-1 <= capacity)
                if count-1 > capacity:
                    self.assertEqual(report['status'], 'internal-buffer-truncated')
                    self.assert_no_data(report)

    def test_positive_internal_codes_are_never_payload_lengths(self):
        for code in (128, 129):
            report = direct(f'ff{code:02x}62f190')
            self.assertEqual(report['nativeReturnCandidate'], code)
            self.assertEqual(report['nativeCopyBytesCandidate'], 0)
            self.assertEqual(report['status'], 'positive-internal-status-only')
            self.assert_no_data(report)

    def test_internal_failure_no_data_retry_and_unknown_status(self):
        for hx, expected in [('ff00', 0), ('ff01', -14), ('ff02', -70), ('ff03', -60), ('0100', -60)]:
            report = direct(hx)
            self.assertEqual(report['nativeReturnCandidate'], expected)
            self.assertFalse(report['retryPerformed'])
            self.assert_no_data(report)

    def test_transport_retry_does_not_use_stale_frame(self):
        report = direct('0062f190', count=-14)
        self.assertEqual(report['status'], 'internal-retry-indicated')
        self.assert_no_data(report)

    def test_negative_zero_and_one_counts_do_not_decode_memory_tail(self):
        for count in (-70, -1, 0, 1):
            report = direct('0062f190', count=count)
            self.assertEqual(report['status'], 'no-complete-internal-frame')
            self.assert_no_data(report)

    def test_signed16_wrap_is_reported_without_promoting_truncated_length(self):
        for count in (32768, 65535, 65536, 65538, -65534, 2**31-1, -(2**31)):
            report = direct('0062', count=count)
            self.assertEqual(report['status'], 'transport-return-wrapped-unqualified')
            self.assertIsNone(report['nativeReturnCandidate'])
            self.assert_no_data(report)

    def test_exact_observed_frame_extent_required(self):
        for count in (2, 4):
            report = direct('0062f1', count=count)
            self.assertEqual(report['status'], 'observed-frame-length-mismatch')
            self.assert_no_data(report)

    def test_cached_path_returns_zero_without_claiming_fresh_bytes(self):
        report = audit_internal_response({'path': 'cached', 'capacity': 1024})
        self.assertEqual(report['nativeReturnCandidate'], 0)
        self.assertEqual(report['status'], 'cached-destination-recorded-no-response')
        self.assert_no_data(report)
        with self.assertRaisesRegex(ValueError, 'cached-path-has-no-direct-frame'):
            audit_internal_response({'path': 'cached', 'capacity': 1024, 'frameHex': ''})

    def test_invalid_inputs_cannot_claim_a_qualified_source_or_accept_transport(self):
        for observation in [[], {}, {'path':'live','capacity':1}, {'path':'direct','capacity':True},
            {'path':'cached','capacity':32768}, {'path':'cached','capacity':0},
            {'path':'cached','capacity':1,'port':lambda: None},
            {'path':'direct','capacity':1,'transportReturn':True,'frameHex':'0000'},
            {'path':'direct','capacity':1,'transportReturn':2**31,'frameHex':'0000'},
            {'path':'direct','capacity':1,'transportReturn':2,'frameHex':'00 00'},
            {'path':'direct','capacity':1,'transportReturn':2,'frameHex':'000'},
            {'path':'direct','capacity':1,'transportReturn':2,'frameHex':'zz00'},
            {'path':'direct','capacity':1,'transportReturn':2,'frameHex':'00'*32769}]:
            with self.subTest(observation=str(observation)[:100]), self.assertRaises(ValueError):
                audit_internal_response(observation)

    def test_cli_offline_observation_retains_guards(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'observation.json'
            path.write_text(json.dumps({'path':'direct','capacity':1024,'transportReturn':3,'frameHex':'0062f1'}))
            output=io.StringIO()
            with redirect_stdout(output):
                self.assertEqual(main(['--observation-json',str(path)]),0)
            self.assertEqual(json.loads(output.getvalue())['internalBufferHexCandidate'],'62f1')


class PorscheWrapperContractTests(unittest.TestCase):
    def wrapper(self, buffer, count=None, capacity=1024):
        return audit_porsche_mode30_wrapper({'wrapper':'mode30-non-subtype4-0x11208',
            'internal':{'path':'direct','capacity':capacity,'transportReturn':len(buffer)+1 if count is None else count,
                        'frameHex':(b'\x00'+buffer).hex()}})

    def test_count_and_declared_body_length_are_separate(self):
        report=self.wrapper(bytes.fromhex('120000000003000062f190'))
        self.assertEqual(report['lowerResponse']['nativeReturnCandidate'],11)
        self.assertEqual(report['declaredBodyBytes'],3)
        self.assertEqual(report['declaredBodyHexCandidate'],'62f190')
        self.assertEqual(report['wrapperStatusByte'],18)
        self.assertIsNone(report['ecuPayloadHex'])
        for key,value in GUARDS.items():
            self.assertEqual(report[key],value)

    def test_zeroed_scratch_tail_cannot_complete_a_truncated_body(self):
        report=self.wrapper(bytes.fromhex('000000000005000062f1'))
        self.assertEqual(report['status'],'wrapper-body-exceeds-fresh-extent')
        self.assertIsNone(report['declaredBodyHexCandidate'])

    def test_partial_header_is_not_replaced_with_initial_zeroes(self):
        for size in range(1,8):
            report=self.wrapper(bytes(size))
            self.assertEqual(report['status'],'wrapper-header-not-fresh-complete')
            self.assertFalse(report['declaredBodyComplete'])

    def test_empty_body_and_extra_bytes_are_preserved_as_internal_facts(self):
        empty=self.wrapper(bytes(8))
        self.assertTrue(empty['declaredBodyComplete'])
        self.assertEqual(empty['declaredBodyHexCandidate'],'')
        extra=self.wrapper(bytes.fromhex('000000000001000062aabb'))
        self.assertEqual(extra['declaredBodyHexCandidate'],'62')
        self.assertEqual(extra['extraFreshBytes'],2)

    def test_wrong_route_capacity_and_override_fields_are_rejected(self):
        for observation in [None,{}, {'wrapper':'subtype4','internal':{'capacity':1024}},
            {'wrapper':'mode30-non-subtype4-0x11208','internal':{'capacity':1}},
            {'wrapper':'mode30-non-subtype4-0x11208','internal':{},'ecuQualified':True}]:
            with self.assertRaises(ValueError):
                audit_porsche_mode30_wrapper(observation)

    def test_lower_failure_cache_and_positive_status_never_form_a_wrapper(self):
        for lower in [{'path':'cached','capacity':1024},
                      {'path':'direct','capacity':1024,'transportReturn':2,'frameHex':'ff80'},
                      {'path':'direct','capacity':1024,'transportReturn':9,'frameHex':'0000'}]:
            report=audit_porsche_mode30_wrapper({'wrapper':'mode30-non-subtype4-0x11208','internal':lower})
            self.assertEqual(report['status'],'lower-response-unqualified')
            self.assertIsNone(report['declaredBodyHexCandidate'])

    def test_cli_selects_only_explicit_wrapper_observation(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'wrapper.json'
            path.write_text(json.dumps({'wrapper':'mode30-non-subtype4-0x11208',
                'internal':{'path':'direct','capacity':1024,'transportReturn':10,
                            'frameHex':'00000000000001000062'}}))
            output=io.StringIO()
            with redirect_stdout(output):
                self.assertEqual(main(['--observation-json',str(path),'--porsche-mode30-wrapper']),0)
            report=json.loads(output.getvalue())
            self.assertEqual(report['declaredBodyHexCandidate'],'62')
            self.assertFalse(report['actualResponseLengthContractQualified'])


if __name__ == '__main__':
    unittest.main()
