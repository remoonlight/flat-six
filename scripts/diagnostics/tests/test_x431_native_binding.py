import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from scripts.diagnostics.x431_native_binding import (
    SYMBOL_NAMES, audit_loader_sequence, audit_std_loader_sources, main)
from scripts.diagnostics.command_lookup_buffers import audit_command_lookup_buffers
from scripts.diagnostics.x431_version_response import VERSION_GUARDS


def initial(**changes):
    return dict(pStdDllPresent=False, pStdSearchIDHandlePresent=False,
                symbolSlots={name:None for name in SYMBOL_NAMES}, events=[]) | changes


def opened(**changes):
    return dict(operation='open-search', firstDlopenSucceeded=True,
                fallbackDlopenSucceeded=None, symbolLookupResults=[True]*16, observedReturn=0) | changes


def cached(**changes):
    return opened(firstDlopenSucceeded=None, symbolLookupResults=[]) | changes


def closed(**changes):
    return dict(operation='close-std', observedDlcloseReturn=0, observedReturn=0) | changes


class NativeLoaderSequenceTests(unittest.TestCase):
    def test_all_symbols_resolved_proves_source_sequence_only(self):
        r = audit_loader_sequence(initial(events=[opened()]))
        self.assertTrue(r['completeLoaderChainCandidate'])
        self.assertEqual(r['steps'][0]['sourceResolvedSymbols'], list(SYMBOL_NAMES))
        self.assertFalse(r['actualDynamicLookupBindingQualified'])
        self.assertFalse(r['actualProcessLibraryFingerprintQualified'])
        self.assertEqual({key:r[key] for key in VERSION_GUARDS}, VERSION_GUARDS)

    def test_source_fallback_is_only_attempted_when_first_open_fails(self):
        r = audit_loader_sequence(initial(events=[opened(firstDlopenSucceeded=False, fallbackDlopenSucceeded=True)]))
        self.assertTrue(r['completeLoaderChainCandidate'])
        with self.assertRaises(ValueError):
            audit_loader_sequence(initial(events=[opened(fallbackDlopenSucceeded=True)]))

    def test_null_library_never_calls_dlsym(self):
        r = audit_loader_sequence(initial(events=[opened(firstDlopenSucceeded=False,
            fallbackDlopenSucceeded=False, symbolLookupResults=[], observedReturn=-1)]))
        self.assertFalse(r['completeLoaderChainCandidate'])
        self.assertEqual(r['steps'][0]['sourceReturnCandidate'],-1)
        self.assertFalse(r['steps'][0]['pStdSearchIDHandlePresentCandidate'])

    def test_cached_std_handle_still_requires_all_symbol_resolutions(self):
        r = audit_loader_sequence(initial(pStdDllPresent=True, events=[opened(firstDlopenSucceeded=None)]))
        self.assertTrue(r['completeLoaderChainCandidate'])
        with self.assertRaises(ValueError):
            audit_loader_sequence(initial(pStdDllPresent=True, events=[opened()]))

    def test_every_missing_symbol_has_its_own_error_and_retains_search_handle(self):
        for index,name in enumerate(SYMBOL_NAMES):
            with self.subTest(name=name):
                r = audit_loader_sequence(initial(events=[opened(symbolLookupResults=[True]*index+[False],
                    observedReturn=-(index+3)), cached()]))
                first,second = r['steps']
                self.assertEqual(first['sourceReturnCandidate'], -(index+3))
                self.assertFalse(first['symbolSlotsCandidate'][name])
                self.assertTrue(first['pStdSearchIDHandlePresentCandidate'])
                self.assertEqual(second['sourceReturnCandidate'],0)
                self.assertTrue(second['loaderReturnMasksIncompleteBindingsCandidate'])
                self.assertFalse(r['completeLoaderChainCandidate'])

    def test_unreached_symbol_slots_are_preserved_rather_than_invented_nulls(self):
        slots = {name:None for name in SYMBOL_NAMES}; slots['IdSearch'] = True
        r = audit_loader_sequence(initial(symbolSlots=slots, events=[opened(symbolLookupResults=[False],observedReturn=-3)]))
        self.assertTrue(r['steps'][0]['symbolSlotsCandidate']['IdSearch'])
        self.assertIsNone(r['steps'][0]['symbolSlotsCandidate']['SearchIdOpenFile'])
        self.assertFalse(r['completeLoaderChainCandidate'])

    def test_cached_nonnull_slots_without_fresh_binding_are_not_qualified(self):
        r = audit_loader_sequence(initial(pStdDllPresent=True, pStdSearchIDHandlePresent=True,
            symbolSlots={name:True for name in SYMBOL_NAMES}, events=[cached()]))
        self.assertTrue(r['steps'][0]['allSlotsObservedNonNullCandidate'])
        self.assertFalse(r['completeLoaderChainCandidate'])
        self.assertTrue(r['steps'][0]['loaderReturnMasksIncompleteBindingsCandidate'])

    def test_cached_call_after_success_preserves_same_sequence_candidate(self):
        r = audit_loader_sequence(initial(events=[opened(),cached()]))
        self.assertTrue(r['completeLoaderChainCandidate'])
        self.assertEqual(r['steps'][1]['sourceResolvedSymbols'],[])

    def test_close_ignores_return_but_invalidates_lifetime_and_keeps_cached_slots(self):
        for result in (0,-1,None):
            r = audit_loader_sequence(initial(events=[opened(),closed(observedDlcloseReturn=result),cached()]))
            closing,next_call = r['steps'][1:]
            self.assertFalse(closing['pStdDllPresentCandidate'])
            self.assertTrue(closing['pStdSearchIDHandlePresentCandidate'])
            self.assertTrue(closing['allSlotsObservedNonNullCandidate'])
            self.assertFalse(next_call['slotLifetimeCandidate'])
            self.assertTrue(next_call['loaderReturnMasksIncompleteBindingsCandidate'])
            self.assertFalse(r['completeLoaderChainCandidate'])

    def test_close_with_null_std_handle_has_no_dlclose_result(self):
        r = audit_loader_sequence(initial(events=[closed(observedDlcloseReturn=None)]))
        self.assertFalse(r['completeLoaderChainCandidate'])
        with self.assertRaises(ValueError):
            audit_loader_sequence(initial(events=[closed()]))

    def test_observations_cannot_execute_symbols_after_failure_or_during_cache_bypass(self):
        for event in (opened(symbolLookupResults=[True]), opened(symbolLookupResults=[False,True]),
                      opened(symbolLookupResults=[]), opened(symbolLookupResults=[True]*17),
                      opened(symbolLookupResults=[True]*15+[None]), opened(observedReturn=-9)):
            with self.assertRaises(ValueError):
                audit_loader_sequence(initial(events=[event]))
        for event in (opened(),cached(firstDlopenSucceeded=False),cached(observedReturn=True)):
            with self.assertRaises(ValueError):
                audit_loader_sequence(initial(pStdSearchIDHandlePresent=True,events=[event]))

    def test_strict_state_fields_and_operations(self):
        changes = ({'pStdDllPresent':1}, {'symbolSlots':{}},
            {'symbolSlots':{name:1 for name in SYMBOL_NAMES}}, {'events':[]},
            {'events':[opened()]*257}, {'events':[{}]}, {'events':[closed(observedDlcloseReturn=True)]},
            {'events':[opened()|{'actualDynamicLookupBindingQualified':True}]},
            {'actualDynamicLookupBindingQualified':True})
        for change in changes:
            with self.subTest(change=change), self.assertRaises(ValueError):
                audit_loader_sequence(initial(events=[opened()]) | change)

    def test_wrong_source_hashes_rejected_before_elf_parse(self):
        with self.assertRaisesRegex(ValueError,'file-source-unreviewed'):
            audit_std_loader_sources(b'not ELF', b'not ELF')
        from scripts.diagnostics.x431_native_binding import FILE_SHA256
        with patch('scripts.diagnostics.x431_native_binding.hashlib.sha256') as sha:
            sha.return_value.hexdigest.return_value = FILE_SHA256
            with self.assertRaisesRegex(ValueError,'std-source-unreviewed'):
                audit_std_loader_sources(b'not ELF',b'not ELF')

    def test_cli_does_not_skip_source_check_for_observation_json(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); lib = root/'file.so'; lib.write_bytes(b'unknown ELF')
            observations = root/'observations.json'; observations.write_text(json.dumps(initial(events=[opened()])))
            with self.assertRaises(SystemExit) as error:
                main(['--file-library',str(lib),'--std-library',str(lib),'--observation-json',str(observations)])
            self.assertEqual(error.exception.code,2)


class LookupLoaderBindingTests(unittest.TestCase):
    def row(self, sequence):
        return dict(decodedPayloadHex='00023e00', lookupCapacity=512, recordFound=True,
            payloadReadReturnedBytes=4, idSearchReturnValue=4,
            scratchAtLookupReturnHex='00023e00', callerAfterCopyHex='00023e00',
            loaderSequenceObservation=sequence)

    def test_complete_buffer_cannot_repair_loader_failure_or_close(self):
        for sequence in (initial(events=[opened(symbolLookupResults=[True]*6+[False],observedReturn=-9),cached()]),
                         initial(events=[opened(),closed(),cached()])):
            r = audit_command_lookup_buffers(self.row(sequence))
            self.assertEqual(r['knownSourceCallerPrefixHex'],'00023e00')
            self.assertFalse(r['lookupBufferChainCompleteCandidate'])
            self.assertEqual(r['status'],'native-loader-chain-unqualified')

    def test_same_sequence_complete_binding_remains_offline_candidate(self):
        r = audit_command_lookup_buffers(self.row(initial(events=[opened()])))
        self.assertTrue(r['lookupBufferChainCompleteCandidate'])
        self.assertFalse(r['nativeLoaderSequenceAudit']['actualDynamicLookupBindingQualified'])
        self.assertFalse(r['observationsProveActualRuntimeOrigin'])


if __name__=='__main__':
    unittest.main()
