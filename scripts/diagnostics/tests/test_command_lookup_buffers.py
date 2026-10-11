import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from scripts.diagnostics.command_lookup_buffers import audit_command_lookup_buffers
from scripts.diagnostics.x431_command_origin import audit_source_command_context, main
from scripts.diagnostics.x431_version_response import VERSION_GUARDS, audit_communication_pair_context


class LookupBufferTests(unittest.TestCase):
    def row(self, **changes):
        return dict(decodedPayloadHex='010322f19e', lookupCapacity=512,
                    recordFound=True, payloadReadReturnedBytes=5) | changes

    def observed(self, **changes):
        return self.row(searchBufferHex='010322f19e00', idSearchReturnValue=5,
                        scratchAtLookupReturnHex='010322f19e00', callerAfterCopyHex='010322f19e00') | changes

    def test_full_payload_does_not_prove_heap_termination(self):
        r = audit_command_lookup_buffers(self.row())
        self.assertTrue(r['payloadReadCompleteCandidate'])
        self.assertEqual(r['status'], 'command-postprocessing-unqualified')
        self.assertIsNone(r['knownSourceCallerPrefixHex'])
        self.assertFalse(r['lookupBufferChainCompleteCandidate'])

    def test_observed_bypass_and_copy_establish_only_candidate_prefix(self):
        r = audit_command_lookup_buffers(self.observed())
        self.assertTrue(r['lookupBufferChainCompleteCandidate'])
        self.assertEqual(r['callerUnqualifiedTailBytes'], 507)
        self.assertEqual(r['knownSourceCallerPrefixHex'], '010322f19e')
        self.assertEqual(r['sourceCallerCopyBytes'], 512)
        self.assertEqual({key:r[key] for key in VERSION_GUARDS}, VERSION_GUARDS)
        self.assertFalse(r['observationsProveActualRuntimeOrigin'])

    def test_source_payload_null_is_separate_from_caller_copy_observation(self):
        r = audit_command_lookup_buffers(self.row(decodedPayloadHex='00023e00', payloadReadReturnedBytes=4))
        self.assertEqual(r['knownSourceCallerPrefixHex'], '00023e00')
        self.assertFalse(r['lookupBufferChainCompleteCandidate'])
        self.assertEqual(r['callerUnqualifiedTailBytes'], 508)

    def test_partial_or_unobserved_payload_read_does_not_decrypt_an_invented_prefix(self):
        for count in (None, 0, 4):
            r = audit_command_lookup_buffers(self.observed(payloadReadReturnedBytes=count))
            self.assertIsNone(r['postprocessingAudit'])
            self.assertIsNone(r['knownSourceCallerPrefixHex'])
            self.assertFalse(r['lookupBufferChainCompleteCandidate'])

    def test_negative_lookup_return_is_hidden_by_outer_zero_return(self):
        for code in (-3, -17):
            r = audit_command_lookup_buffers(self.observed(idSearchReturnValue=code))
            self.assertEqual(r['status'], 'lookup-error-hidden-by-caller')
            self.assertIsNone(r['postprocessingAudit'])
            self.assertEqual(r['nativeSourceAudit']['sourceFromCmdNormalReturnCandidate'], 0)

    def test_missing_and_empty_records_never_invent_scratch_zeroes(self):
        for found in (False, True):
            r = audit_command_lookup_buffers(self.row(recordFound=found, decodedPayloadHex='',
                payloadReadReturnedBytes=None, idSearchReturnValue=0,
                scratchAtLookupReturnHex='abcd', callerAfterCopyHex='abcd'))
            self.assertEqual(r['callerUnqualifiedTailBytes'], 512)
            self.assertEqual(r['callerHeaderZeroBytesOverwrittenByCopy'], 2)
            self.assertIsNone(r['knownSourceCallerPrefixHex'])
            self.assertFalse(r['lookupBufferChainCompleteCandidate'])

    def test_observed_copy_mismatch_is_rejected_even_after_lookup_error(self):
        with self.assertRaisesRegex(ValueError, 'caller-copy-mismatch'):
            audit_command_lookup_buffers(self.observed(idSearchReturnValue=-3, callerAfterCopyHex='00'))

    def test_observed_prefix_return_and_postprocessor_entry_must_agree(self):
        for changes in ({'idSearchReturnValue':4}, {'searchBufferHex':'000322f19e00'},
                        {'scratchAtLookupReturnHex':'abcd', 'callerAfterCopyHex':'abcd'}):
            with self.assertRaises(ValueError):
                audit_command_lookup_buffers(self.observed(**changes))

    def test_partial_caller_observation_cannot_complete_chain(self):
        r = audit_command_lookup_buffers(self.observed(callerAfterCopyHex='0103'))
        self.assertFalse(r['lookupBufferChainCompleteCandidate'])

    def test_recursive_text_lookup_never_gets_invented_return_bytes(self):
        body = b'SubIndexNum=1,1234567890\0'
        r = audit_command_lookup_buffers(self.row(decodedPayloadHex=body.hex(), payloadReadReturnedBytes=len(body)))
        self.assertTrue(r['postprocessingAudit']['sourceRecursiveLookupRequiredCandidate'])
        self.assertFalse(r['lookupBufferChainCompleteCandidate'])

    def test_capacity_clamp_uses_requested_count_without_padding(self):
        r = audit_command_lookup_buffers(self.row(lookupCapacity=4, decodedPayloadHex='00023e001122',
            payloadReadReturnedBytes=4, idSearchReturnValue=4,
            scratchAtLookupReturnHex='00023e00', callerAfterCopyHex='00023e00'))
        self.assertTrue(r['lookupBufferChainCompleteCandidate'])
        self.assertEqual(r['knownSourceCallerPrefixHex'], '00023e00')
        self.assertEqual(r['callerUnqualifiedTailBytes'], 0)

    def test_schema_counts_and_types_are_strict(self):
        changes = ({'lookupCapacity':True}, {'lookupCapacity':32766}, {'recordFound':1},
            {'recordFound':False}, {'payloadReadReturnedBytes':True}, {'payloadReadReturnedBytes':6},
            {'payloadReadReturnedBytes':-1}, {'idSearchReturnValue':True}, {'idSearchReturnValue':0x80000000},
            {'scratchAtLookupReturnHex':'00'*515}, {'callerAfterCopyHex':'00'*513},
            {'decodedPayloadHex':'zz'}, {'actualHeapBufferQualified':True},
            {'decodedPayloadHex':'', 'payloadReadReturnedBytes':0})
        for change in changes:
            with self.subTest(change=change), self.assertRaises(ValueError):
                audit_command_lookup_buffers(self.row(**change))

    def test_cli_bootstrap_audits_observations_and_rejects_unused_sources(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)/'input.json'
            path.write_text(json.dumps(self.observed()))
            output = io.StringIO()
            with redirect_stdout(output):
                self.assertEqual(main(['--kind','lookup-buffers','--observation-json',str(path)]), 0)
            self.assertTrue(json.loads(output.getvalue())['lookupBufferChainCompleteCandidate'])
            with self.assertRaises(SystemExit):
                main(['--kind','lookup-buffers','--observation-json',str(path),'--logical-sys-data',str(path)])


class SourceContextBufferTests(unittest.TestCase):
    record = '100301e0ea000020f800000305a70310000200200001002000030020000a00200000000020000100400e00000000'
    selection = dict(familyId=13, systemKey=0xd40, linkIndex=0, pairIndex=0, requestIndex=0)

    def audit(self, observation, found=True):
        source = audit_communication_pair_context(self.record, 0)
        source['modeContextCandidate'] = dict(communicationRecordHex=self.record, pairIndex=0)
        lookup = dict(recordFound=found, decodedPayloadHex='00023e00' if found else None)
        with patch('scripts.diagnostics.x431_command_origin.audit_source_communication_context', return_value=source), \
             patch('scripts.diagnostics.x431_command_origin._lookup_command', return_value=lookup):
            return audit_source_command_context(b'', b'', self.selection | observation)

    def test_short_or_error_read_prevents_native_descriptor_without_losing_raw_source(self):
        for obs in ({'payloadReadReturnedBytes':3}, {'payloadReadReturnedBytes':4,'idSearchReturnValue':-17}):
            r = self.audit({'lookupBufferObservation':obs})
            self.assertTrue(r['rawDescriptorComplete'])
            self.assertEqual(r['requestHexCandidate'],'3e00')
            self.assertFalse(r['nativeDescriptorCompleteCandidate'])

    def test_full_observed_buffer_chain_remains_offline_candidate(self):
        r = self.audit({'lookupBufferObservation':dict(payloadReadReturnedBytes=4, idSearchReturnValue=4,
            scratchAtLookupReturnHex='00023e00', callerAfterCopyHex='00023e00')})
        self.assertTrue(r['nativeDescriptorCompleteCandidate'])
        self.assertEqual({key:r[key] for key in VERSION_GUARDS}, VERSION_GUARDS)

    def test_missing_record_keeps_lookup_error_audit(self):
        r = self.audit({'lookupBufferObservation':dict(payloadReadReturnedBytes=None,idSearchReturnValue=-17)}, found=False)
        self.assertEqual(r['status'],'source-command-id-not-found')
        self.assertEqual(r['nativeLookupBufferAudit']['status'],'lookup-error-hidden-by-caller')

    def test_cached_loader_failure_blocks_complete_source_descriptor(self):
        from .test_x431_native_binding import initial, opened, cached
        sequence=initial(events=[opened(symbolLookupResults=[True]*6+[False],observedReturn=-9),cached()])
        r=self.audit({'lookupBufferObservation':dict(payloadReadReturnedBytes=4,idSearchReturnValue=4,
            scratchAtLookupReturnHex='00023e00',callerAfterCopyHex='00023e00',loaderSequenceObservation=sequence)})
        self.assertTrue(r['rawDescriptorComplete'])
        self.assertFalse(r['nativeDescriptorCompleteCandidate'])
        self.assertEqual(r['nativeLookupBufferAudit']['status'],'native-loader-chain-unqualified')

    def test_injected_source_fields_and_duplicate_observations_are_rejected(self):
        for obs in ({'lookupBufferObservation':{'payloadReadReturnedBytes':4,'recordFound':True}},
                    {'lookupBufferObservation':{'payloadReadReturnedBytes':4},'searchBufferHex':'00023e00'}):
            with self.assertRaises(ValueError):
                self.audit(obs)


if __name__ == '__main__':
    unittest.main()
