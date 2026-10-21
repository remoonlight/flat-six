"""RAW-reassembled PDU metadata keeps native ownership checks; no live IO."""
import unittest
from scripts.diagnostics.coding_capture import valid_context,native_result_error


class Mode50CaptureContextTests(unittest.TestCase):
    def context(self):
        return dict(requestHex='22F100',timestampUs=1000,timestampSource='host-pdu-observed',
            simulation=False,duringCleanup=False,targetProfileId='source-mode50',runId='owned-run',
            transportKind='d-pdu-mode50',observation='reassembled-complete-pdu',primitive=1,
            expectedPrimitive=1,uniqueId=1,flagsHex='00000000')

    def test_owned_raw_reassembly_has_explicit_transport_and_valid_native_context(self):
        meta=self.context();self.assertTrue(valid_context(meta,1000));self.assertIsNone(native_result_error(meta))

    def test_other_observation_or_unrecognized_transport_is_not_a_valid_context(self):
        for update in [dict(observation='native-result-data'),dict(transportKind='d-pdu-raw'),dict(primitive=True)]:
            self.assertFalse(valid_context({**self.context(),**update},1000))

    def test_primitive_mismatch_missing_context_and_flags_remain_unqualified(self):
        for update,reason in [(dict(expectedPrimitive=2),'native-primitive-mismatch'),
                              (dict(uniqueId=2),'native-result-unqualified'),(dict(flagsHex='00000001'),'native-result-unqualified')]:
            meta={**self.context(),**update};self.assertTrue(valid_context(meta,1000));self.assertEqual(native_result_error(meta),reason)
        meta=self.context();meta.pop('expectedPrimitive');self.assertEqual(native_result_error(meta),'native-primitive-context-missing')
