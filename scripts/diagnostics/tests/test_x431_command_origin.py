import hashlib,io,json,tempfile,unittest
from contextlib import redirect_stdout, contextmanager, ExitStack
from pathlib import Path
from unittest.mock import patch
from scripts.diagnostics.x431_command_origin import (
    audit_command_postprocessing,audit_command_staging,audit_source_command_context,audit_source_command_response,
    audit_source_command_version_observations,audit_gag_name_selection,audit_gag_cache_reuse,
    audit_enter_sys_command_caller,decode_command_catalog,audit_command_route_catalog,main)
from scripts.diagnostics.x431_version_response import audit_communication_pair_context,VERSION_GUARDS


class GagFileSelectionTests(unittest.TestCase):
    def rows(self,*names):
        return [dict(i=i,name_ascii=name,offset=100+i*20,size=20) for i,name in enumerate(names)]

    def test_exact_name_has_separate_native_and_project_evidence(self):
        r=audit_gag_name_selection(self.rows('LONG_TEXT.GAG','COMMAND.GAG'),'COMMAND.GAG')
        self.assertEqual(r['sourceFirstPrefixIndexCandidate'],1)
        self.assertTrue(r['sourceExactBindingCandidate'])
        self.assertEqual(r['qualifiedSourceEntryCandidate']['offset'],120)
        self.assertFalse(r['actualGgpCacheContextQualified'])
        self.assertFalse(r['actualNameMappingTableQualified'])

    def test_prefix_and_empty_name_never_become_exact_bindings(self):
        for name in ('COMMAND',''):
            r=audit_gag_name_selection(self.rows('COMMAND.GAG'),name)
            self.assertEqual(r['sourceNativeOffsetCandidate'],100)
            self.assertFalse(r['sourceExactBindingCandidate'])
            self.assertIsNone(r['qualifiedSourceEntryCandidate'])

    def test_prefix_collision_and_duplicate_keep_first_candidate_unqualified(self):
        for names in [('COMMAND.GAG.EXTRA','COMMAND.GAG'),('COMMAND.GAG','COMMAND.GAG.EXTRA'),('COMMAND.GAG','COMMAND.GAG')]:
            r=audit_gag_name_selection(self.rows(*names),'COMMAND.GAG')
            self.assertEqual(r['sourceFirstPrefixIndexCandidate'],0)
            self.assertFalse(r['sourceExactBindingCandidate'])

    def test_shorter_prior_allocation_cannot_be_repaired_by_later_exact_name(self):
        r=audit_gag_name_selection(self.rows('T','COMMAND.GAG'),'COMMAND.GAG')
        self.assertEqual(r['sourceLogicalPrefixMatchIndexes'],[1])
        self.assertEqual(r['sourceUnknownComparisonRowIndexes'],[0])
        self.assertFalse(r['sourceKnownNameComparisonRangesCompleteCandidate'])
        self.assertIsNone(r['sourceFirstPrefixIndexCandidate'])
        self.assertIsNone(r['sourceNativeOffsetCandidate'])
        self.assertIsNone(r['sourceNativeSizeCandidate'])
        self.assertFalse(r['sourceExactBindingCandidate'])

    def test_case_and_native_length_boundary_are_explicit(self):
        r=audit_gag_name_selection(self.rows('COMMAND.GAG'),'command.gag')
        self.assertEqual(r['sourceNativeOffsetCandidate'],0)
        self.assertFalse(r['sourceExactBindingCandidate'])
        self.assertTrue(audit_gag_name_selection(self.rows('A'*64),'A'*64)['sourceExactBindingCandidate'])
        r=audit_gag_name_selection(self.rows('A'*64),'A'*65)
        self.assertFalse(r['sourceNameLengthAllowedCandidate'])
        self.assertIsNone(r['sourceFirstPrefixIndexCandidate'])

    def test_directory_count_indexes_and_fields_cannot_be_guessed(self):
        for rows in [self.rows(*(['A']*256)),[dict(i=True,name_ascii='A',offset=0,size=1)],
                     [dict(i=1,name_ascii='A',offset=0,size=1)],[dict(i=0,name_ascii='A',offset=True,size=1)]]:
            with self.assertRaises(ValueError):audit_gag_name_selection(rows,'A')
        for name in ('A\0','中文'):
            with self.assertRaises(ValueError):audit_gag_name_selection(self.rows('A'),name)

    def test_same_path_reuses_header_candidate_without_proving_contents(self):
        r=audit_gag_cache_reuse(dict(requestedGgpPath='/p/PORSCHE_CN.GGP',cachedGgpPath='/p/PORSCHE_CN.GGP',cachedHeaderPointerPresent=True))
        self.assertTrue(r['sourceCacheReuseCandidate'])
        self.assertFalse(r['sourceReloadCandidate'])
        self.assertFalse(r['samePathProvesSameContent'])
        self.assertFalse(r['actualFileIdentityQualified'])
        self.assertFalse(r['freshReadProven'])

    def test_missing_pointer_or_different_case_requires_source_reload_candidate(self):
        for present,cached in [(False,'/p/PORSCHE_CN.GGP'),(True,'/p/porsche_cn.ggp'),(True,'/old/PORSCHE_CN.GGP')]:
            r=audit_gag_cache_reuse(dict(requestedGgpPath='/p/PORSCHE_CN.GGP',cachedGgpPath=cached,cachedHeaderPointerPresent=present))
            self.assertFalse(r['sourceCacheReuseCandidate'])
            self.assertTrue(r['sourceReloadCandidate'])
            self.assertFalse(r['actualGgpCacheContextQualified'])
        r=audit_gag_cache_reuse(dict(requestedGgpPath='/p/PORSCHE_CN.GGP',cachedGgpPath=None,cachedHeaderPointerPresent=True))
        self.assertIsNone(r['sourceCacheReuseCandidate'])
        self.assertIsNone(r['sourceReloadCandidate'])

    def test_review_path_bound_is_not_a_native_safety_claim(self):
        row=dict(requestedGgpPath='A'*255,cachedGgpPath=None,cachedHeaderPointerPresent=False)
        r=audit_gag_cache_reuse(row)
        self.assertFalse(r['reviewInputPathBoundIsNativeBoundsCheck'])
        for bad in [row|{'requestedGgpPath':'A'*256},row|{'requestedGgpPath':'A\0'},row|{'cachedHeaderPointerPresent':1}]:
            with self.assertRaises(ValueError):audit_gag_cache_reuse(bad)


class CommandPostprocessingTests(unittest.TestCase):
    def audit(self,body,capacity=512,buffer=None):
        return audit_command_postprocessing({'decodedPayloadHex':body.hex(),'lookupCapacity':capacity,
            **({'searchBufferHex':buffer.hex()} if buffer is not None else {})})

    def test_nonterminated_binary_record_does_not_borrow_a_zero_from_allocation(self):
        r=self.audit(bytes.fromhex('010322f19e'))
        self.assertFalse(r['sourcePostprocessingComplete']);self.assertIsNone(r['nativeLookupReturnCandidate'])
        self.assertFalse(r['sourceMarkerPrefixMatchesCandidate'])
        self.assertEqual(r['callerUnqualifiedTailBytes'],507)
        self.assertIsNone(r['sourceWrittenPrefixHex'])

    def test_nul_in_binary_record_does_not_truncate_its_command_body(self):
        body=bytes.fromhex('00021003');r=self.audit(body)
        self.assertTrue(r['sourcePostprocessingComplete'])
        self.assertEqual(r['sourceStringLengthCandidate'],0)
        self.assertEqual(r['sourceWrittenPrefixHex'],body.hex())
        self.assertEqual(r['nativeLookupReturnCandidate'],4)

    def test_explicit_terminator_qualifies_only_body_prefix_not_full_caller_copy(self):
        body=bytes.fromhex('010322f19e');r=self.audit(body,buffer=body+b'\0')
        self.assertTrue(r['sourcePostprocessingComplete'])
        self.assertEqual((r['nativeLookupReturnCandidate'],r['knownBufferBytes']),(5,6))
        self.assertEqual(r['sourceCallerCopyBytes'],512)
        self.assertEqual(r['sourceWrittenPrefixHex'],body.hex())
        self.assertFalse(r['actualHeapBufferQualified'])

    def test_clamped_lookup_uses_only_clamped_prefix_and_its_observed_terminator(self):
        body=b'\x01\x03abcdef';r=self.audit(body,capacity=4,buffer=body[:4]+b'\0')
        self.assertEqual(r['sourceLookupCopiedBytesCandidate'],4)
        self.assertEqual(r['sourceWrittenPrefixHex'],body[:4].hex())
        self.assertEqual(r['nativeLookupReturnCandidate'],4)

    def test_indirect_marker_needs_recursive_source_and_is_not_accepted_as_plain_command(self):
        r=self.audit(b'SubIndexNum=#123456789#\0')
        self.assertTrue(r['sourceRecursiveLookupRequiredCandidate'])
        self.assertFalse(r['sourcePostprocessingComplete'])
        self.assertIsNone(r['sourceWrittenPrefixHex'])

    def test_unsigned_length_gate_exact_boundaries(self):
        for length,complete in ((21,True),(22,False),(544,False),(545,True)):
            body=b'SubIndexNum='+b'A'*(length-12)+b'\0'
            r=self.audit(body,capacity=1000)
            self.assertEqual(r['sourcePostprocessingComplete'],complete)
            self.assertEqual(r['sourceStringLengthCandidate'],length)

    def test_known_buffer_must_match_decoded_prefix_and_fit_allocation(self):
        for buffer in (b'xyz\0',b'abc\0\0\0',b'ab'):
            with self.assertRaises(ValueError):self.audit(b'abc',capacity=3,buffer=buffer)

    def test_empty_record_never_calls_string_postprocessor_or_supplies_tail(self):
        r=self.audit(b'')
        self.assertFalse(r['sourcePostprocessorCalledCandidate'])
        self.assertEqual(r['nativeLookupReturnCandidate'],0)
        self.assertEqual(r['sourceWrittenPrefixHex'],'')
        self.assertEqual(r['callerUnqualifiedTailBytes'],512)

    def test_strict_schema_capacity_and_hex_reject_overrides(self):
        for capacity in (True,0,-1,32766,1.0):
            with self.assertRaises(ValueError):self.audit(b'x',capacity=capacity)
        for value in ({'decodedPayloadHex':'0','lookupCapacity':512},
                      {'decodedPayloadHex':'zz','lookupCapacity':512},
                      {'decodedPayloadHex':'','lookupCapacity':512,'executionEnabled':True}):
            with self.assertRaises(ValueError):audit_command_postprocessing(value)

    def test_cli_does_not_execute_or_qualify_vehicle_data(self):
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'body.json';path.write_text(json.dumps({'decodedPayloadHex':'00021003','lookupCapacity':512}))
            out=io.StringIO()
            with redirect_stdout(out):self.assertEqual(main(['--kind','postprocessing','--observation-json',str(path)]),0)
            r=json.loads(out.getvalue())
            for key,value in VERSION_GUARDS.items():self.assertEqual(r[key],value)


class CommandStagingTests(unittest.TestCase):
    def test_calculated_extension_uses_source_255_divisor_and_258_threshold(self):
        for body_size,expected in ((0,0),(255,0),(256,1),(509,1),(510,2),(4095,16)):
            # Explicit synthetic descriptors, not packets or source commands.
            descriptor=bytes((1,body_size&255))+b'A'*body_size
            r=audit_command_staging(dict(entry='PorscheSendCommandByIDEX',descriptorHex=descriptor.hex()))
            self.assertEqual(r['sourceExtendedByteCandidate'],expected)
            self.assertFalse(r['requestPacketBytesConstructed'])
            self.assertFalse(r['mode10ActuallySelectedQualified'])

    def test_header_length_mismatch_is_visible_and_cleared_tail_does_not_repair_it(self):
        r=audit_command_staging(dict(entry='PorscheSendCommandByIDEX',descriptorHex='010522f19e'))
        self.assertFalse(r['copiedBodyCoversMode10LengthCandidate'])
        self.assertEqual(r['sourceTailClearBytesCandidate'],4101)
        self.assertFalse(r['sourceClearedTailIsOriginalCommandData'])

    def test_send_only_requires_explicit_retained_extension_and_reports_normal_reset_separately(self):
        entry='PorscheSendCommandByIDEXSendOnlyAndOne'
        with self.assertRaises(ValueError):audit_command_staging(dict(entry=entry,descriptorHex='010322f19e'))
        r=audit_command_staging(dict(entry=entry,descriptorHex='010322f19e',retainedExtendedByte=1))
        self.assertEqual(r['sourceMode10BodyBytesCandidate'],259)
        self.assertFalse(r['copiedBodyCoversMode10LengthCandidate'])
        self.assertEqual(r['sourceSendOnlyAndOneAfterNormalReturnCandidate'],0)
        self.assertFalse(r['normalCleanupRuntimeQualified'])

    def test_explicit_argument_stores_low_byte_and_resets_only_as_normal_source_candidate(self):
        r=audit_command_staging(dict(entry='PorscheSendCommandByIDEX1',descriptorHex='010322f19e',extendedArgument=256))
        self.assertEqual(r['sourceExtendedByteCandidate'],0)
        self.assertEqual(r['sourceExtensionAfterNormalReturnCandidate'],0)
        self.assertEqual(r['sourceTailClearBytesCandidate'],0)
        self.assertTrue(r['copiedBodyExactlyMatchesMode10LengthCandidate'])
        self.assertFalse(r['normalCleanupRuntimeQualified'])

    def test_pro_ex_is_not_relabelled_as_reviewed_pro_mode10(self):
        r=audit_command_staging(dict(entry='PorscheSendCommandByIDEX2',descriptorHex='010322f19e',extendedArgument=0))
        self.assertEqual(r['callee'],'PorscheCommunicationProEx')
        self.assertTrue(r['mode10RequestPackingSourceReviewedCandidate'])
        self.assertTrue(r['mode10ResponseAuditImplemented'])
        self.assertEqual(r['mode10ResponseWrapperCandidate'],'mode10-subtype3-proex-0x11678')
        self.assertFalse(r['versionMapProducerSourceQualifiedForCallee'])
        self.assertFalse(r['sourceRetryPolicySameAsProCandidate'])
        self.assertEqual(r['mode10PackingCandidate']['providerCallSiteCandidate'],0x119c4)
        self.assertIsNotNone(r['commSourceSha256'])

    def test_pro_ex_uses_its_own_large_provider_control_and_extension_mutation_sites(self):
        descriptor=bytes((1,0))+b'A'*4096
        r=audit_command_staging(dict(entry='PorscheSendCommandByIDEX2',descriptorHex=descriptor.hex(),extendedArgument=16))
        p=r['mode10PackingCandidate']
        self.assertEqual(p['sourceRequestEntryCandidate'],0x11678)
        self.assertEqual(p['providerCallSiteCandidate'],0x1198a)
        self.assertEqual(p['sourceFrameControlCallSites'],[0x1197a,0x1199a])
        self.assertEqual(p['requestLengthByteMutationCandidates'],[
            {'callSite':0x1178a,'value':15},{'callSite':0x11878,'value':0}])
        self.assertTrue(p['responseRetryAuditImplemented'])
        self.assertFalse(r['mode10ActuallySelectedQualified'])

    def test_can_initialization_caller_does_not_infer_its_extension_from_descriptor(self):
        r=audit_command_staging(dict(entry='PorscheSendCommandByIDEX_COMM_INFO',descriptorHex='010322f19e',retainedExtendedByte=17))
        self.assertTrue(r['sourceCallerRunsCanInitialization'])
        self.assertEqual(r['sourceExtendedByteOrigin'],'retained')
        self.assertFalse(r['mode10PackingCandidate']['extendedLengthLayoutQualified'])

    def test_bounds_and_unknown_state_overrides_are_rejected(self):
        entry='PorscheSendCommandByIDEX1'
        for value in (-1,True,0x100000000,1.5):
            with self.assertRaises(ValueError):audit_command_staging(dict(entry=entry,descriptorHex='0100',extendedArgument=value))
        for value in ({'entry':'PorscheSendCommandByID','descriptorHex':'0100'},
                      {'entry':entry,'descriptorHex':'01','extendedArgument':0},
                      {'entry':entry,'descriptorHex':'00'*4107,'extendedArgument':0},
                      {'entry':entry,'descriptorHex':'0100','extendedArgument':0,'actualTokenBindingQualified':True}):
            with self.assertRaises(ValueError):audit_command_staging(value)


class ProjectCommandCatalogTests(unittest.TestCase):
    @contextmanager
    def catalog(self, bodies, ids=None):
        ids = list(range(1, len(bodies)+1)) if ids is None else ids
        keys = [i.to_bytes(4, 'big') for i in ids]
        state = {'count': len(bodies), 'ctx': bytearray(512)}
        with patch('scripts.diagnostics.x431_command_origin._parse_command_catalog',
                return_value=({'offset': 123}, b'catalog', state, {}, keys)), \
             patch('scripts.diagnostics.x431_command_origin.decode_record',
                side_effect=lambda state, i: (keys[i], bodies[i])):
            yield

    def test_length_owned_decoder_preserves_zero_bytes_flags_and_write_services(self):
        bodies = [bytes.fromhex(hx) for hx in ('010322F19E', '00043B010000', '03021089', '0200')]
        with self.catalog(bodies):
            r = decode_command_catalog(b'fixture')
        self.assertEqual(r['recordCount'], 4)
        self.assertEqual([v['sourceRequestHex'] for v in r['records']], ['22F19E', '3B010000', '1089', ''])
        self.assertEqual(r['flagCounts'], {0: 1, 1: 1, 2: 1, 3: 1})
        self.assertTrue(r['projectDescriptorDecoderComplete'])
        for key in ('nativeStringTerminationAssumed', 'actualCommandLookupRuntimeQualified',
                    'commandExecutionAuthorized', 'executionEnabled', 'vehicleVerified'):
            self.assertFalse(r[key])
        self.assertIsNone(r['writePayload'])

    def test_truncated_extra_and_indirect_records_do_not_produce_partial_catalog(self):
        for body in (b'', b'\x01', bytes.fromhex('010322F1'), bytes.fromhex('010222F19E'),
                     b'SubIndexNum=#123456789#\0'):
            with self.catalog([bytes.fromhex('010322F19E'), body]):
                with self.assertRaises(ValueError): decode_command_catalog(b'fixture')

    def test_duplicate_plain_ids_and_changed_stored_keys_are_rejected(self):
        with self.catalog([bytes.fromhex('010322F19E')]*2, [1, 1]):
            with self.assertRaisesRegex(ValueError, 'duplicate-id'): decode_command_catalog(b'fixture')
        with self.catalog([bytes.fromhex('010322F19E')]), \
             patch('scripts.diagnostics.x431_command_origin.decode_record', return_value=(b'xxxx', b'\x01\x00')):
            with self.assertRaisesRegex(ValueError, 'index-key'): decode_command_catalog(b'fixture')

    def test_unreviewed_language_is_rejected_before_decoding(self):
        with patch('scripts.diagnostics.x431_command_origin.decode_record') as decoder:
            with self.assertRaisesRegex(ValueError, 'source-unreviewed'): decode_command_catalog(b'edited original')
        decoder.assert_not_called()

    def system(self, *, complete=True, ids=(1, 1, 2)):
        return {'familyId': 13, 'systemKey': 0xD01, 'ecuId': 1, 'links': [
            {'index': 0, 'commOff': 10, 'serviceOff': 20,
             'comm': {'class': 'static-source-confirmed', 'consumedEnd': 20 if complete else 19,
                      'protocolKind': 16, 'pairs': [
                          {'storedTx': 0xEA00, 'storedRx': 0xF800, 'mode': 3, 'params': list(ids)},
                          {'storedTx': 0xEA01, 'storedRx': 0xF801, 'mode': 3, 'params': [1]}]},
             'service': {'variants': [{'name': 'first'}, {'name': 'second'}]}}]}

    @contextmanager
    def source(self, system=None, failure=None):
        parsed = {'ok': True, 'familyIdQueried': 13, 'systems': [self.system() if system is None else system]}
        with patch('scripts.diagnostics.x431_command_origin.SYS_DATA_LOGICAL_SHA256', hashlib.sha256(b'logical').hexdigest()), \
             patch('scripts.x431_re.sysdata.parse_family_index', return_value=[{'familyId': 13}, {'familyId': 14}]), \
             patch('scripts.x431_re.sysdata.parse_payload', side_effect=failure, return_value=parsed), \
             self.catalog([bytes.fromhex('010322F19E'), bytes.fromhex('03021089')]):
            yield

    def test_full_reference_join_preserves_duplicate_ids_all_pairs_and_variant_names(self):
        with self.source(): r = audit_command_route_catalog(b'logical', b'fixture', [13])
        self.assertTrue(r['sourceReferenceJoinComplete'])
        self.assertEqual(r['counts']['commandReferences'], 4)
        self.assertEqual(r['uniqueResolvedCommandIds'], 2)
        link = r['families'][0]['systems'][0]['links'][0]
        self.assertEqual(link['sourceVariantNames'], ['first', 'second'])
        self.assertEqual([v['commandId'] for v in link['pairs'][0]['commands']], [1, 1, 2])
        self.assertEqual(r['otherSourceFamilyIdsNotQueried'], [14])
        for key in ('familyIdsProveVehicleGeneration', 'runtimePairOrCommandSequenceSelected',
                    'commandExecutionAuthorized', 'executionEnabled', 'ecuVersionQualified'):
            self.assertFalse(r[key])

    def test_missing_id_keeps_its_order_and_does_not_substitute_a_matching_request(self):
        with self.source(self.system(ids=(1, 99, 2))):
            r = audit_command_route_catalog(b'logical', b'fixture', [13])
        self.assertFalse(r['sourceReferenceJoinComplete'])
        self.assertEqual(r['missingCommandIds'], ['00000063'])
        missing = r['families'][0]['systems'][0]['links'][0]['pairs'][0]['commands'][1]
        self.assertFalse(missing['recordFound'])
        self.assertEqual(missing['requestIndex'], 1)
        self.assertIsNone(missing['sourceRequestHex'])

    def test_unknown_layout_never_promotes_partial_pair_parameters(self):
        with self.source(self.system(complete=False)):
            r = audit_command_route_catalog(b'logical', b'fixture', [13])
        self.assertFalse(r['sourceReferenceJoinComplete'])
        self.assertEqual(r['counts']['unresolvedLinks'], 1)
        self.assertEqual(r['families'][0]['systems'][0]['links'][0]['pairs'], [])
        self.assertEqual(r['uniqueResolvedCommandIds'], 0)

    def test_family_parse_failure_is_retained_and_does_not_claim_complete_scope(self):
        with self.source(failure=ValueError('unknown family format')):
            r = audit_command_route_catalog(b'logical', b'fixture', [13])
        self.assertFalse(r['sourceReferenceJoinComplete'])
        self.assertEqual(r['families'][0]['reason'], 'unknown family format')
        self.assertEqual(r['counts']['unresolvedFamilies'], 1)

    def test_explicit_selection_and_original_hash_are_required_before_catalog_decode(self):
        with self.source(), patch('scripts.diagnostics.x431_command_origin.decode_command_catalog') as decoder:
            for families in ([], [True], [13, 13], [256], [99], (13,)):
                with self.assertRaises(ValueError): audit_command_route_catalog(b'logical', b'fixture', families)
            with self.assertRaisesRegex(ValueError, 'source-unreviewed'):
                audit_command_route_catalog(b'changed logical', b'fixture', [13])
        decoder.assert_not_called()

    def test_catalog_cli_uses_explicit_original_and_cannot_accept_runtime_overrides(self):
        with tempfile.TemporaryDirectory() as temp:
            p = Path(temp); (p/'language').write_bytes(b'fixture'); (p/'selection').write_text('{}')
            args = ['--kind', 'command-catalog', '--observation-json', str(p/'selection'),
                    '--language-ggp', str(p/'language')]
            out = io.StringIO()
            with self.catalog([bytes.fromhex('010322F19E')]), redirect_stdout(out):
                self.assertEqual(main(args), 0)
            self.assertEqual(json.loads(out.getvalue())['records'][0]['sourceRequestHex'], '22F19E')
            (p/'selection').write_text('{"executionEnabled":true}')
            with self.assertRaises(SystemExit): main(args)

    def test_route_cli_passes_the_exact_selected_families(self):
        with tempfile.TemporaryDirectory() as temp:
            p = Path(temp); (p/'language').write_bytes(b'fixture'); (p/'logical').write_bytes(b'logical')
            (p/'selection').write_text('{"familyIds":[13]}'); out = io.StringIO()
            with self.source(), redirect_stdout(out):
                self.assertEqual(main(['--kind', 'route-catalog', '--observation-json', str(p/'selection'),
                    '--language-ggp', str(p/'language'), '--logical-sys-data', str(p/'logical')]), 0)
            self.assertEqual(json.loads(out.getvalue())['selectedSourceFamilyIds'], [13])
            self.assertFalse(json.loads(out.getvalue())['runtimePairOrCommandSequenceSelected'])


class CommandSourceBindingTests(unittest.TestCase):
    record='100301e0ea000020f800000305a70310000200200001002000030020000a00200000000020000100400e00000000'

    def command(self,complete=True):
        source=audit_communication_pair_context(self.record,0)
        source['modeContextCandidate']={'communicationRecordHex':self.record,'pairIndex':0}
        return dict(rawDescriptorComplete=True,nativeDescriptorCompleteCandidate=complete,
            enterSysCallerSourceAudit=audit_enter_sys_command_caller(0x10, 3),
            requestHexCandidate='22f19e',requestFlagByteCandidate=1,filterIdWordCandidate=0xeae0f820,
            explicitSourceSelection=dict(familyId=13,systemKey=0xd40,linkIndex=0,pairIndex=0,requestIndex=1),
            communicationSourceAudit=source)

    def response(self):
        frame=b'\0\x01'+bytes(6)+b'\x03\x62\xf1\x9e'
        return dict(requestHex='22f19e',producerCallSite=0x19fa2,communicationStatusByte=0x7e,
            communication=dict(wrapper='mode10-subtype3-sequence-0xf8d4',requestBytes=3,extendedRequestByte=0,
              retryContext=dict(filterIdWord=0xeae0f820,sendOnlyAndOne=0,requestFlagByte=1,initialCommunicationStatusByte=0x7e),
              receives=[dict(path='direct',capacity=4116,transportReturn=len(frame)+1,frameHex=(b'\0'+frame).hex())]))

    def audit(self,response=None,complete=True):
        with patch('scripts.diagnostics.x431_command_origin.audit_source_command_context',return_value=self.command(complete)):
            return audit_source_command_response(b'',b'',dict(commandSelection={},responseObservation=self.response() if response is None else response))

    def test_complete_source_bridge_binds_response_to_exact_request_and_source_mode(self):
        r=self.audit()
        self.assertEqual(r['responseOriginAudit']['nativeMapVectorHexCandidate'],'f19e')
        self.assertEqual(r['responseOriginAudit']['modeContextAudit']['communicationRecordSha256'],self.command()['communicationSourceAudit']['communicationRecordSha256'])
        self.assertFalse(r['ecuVersionQualified'])

    def test_positive_response_does_not_repair_missing_command_string_termination(self):
        r=self.audit(complete=False)
        self.assertIsNone(r['responseOriginAudit'])
        self.assertEqual(r['status'],'command-native-descriptor-source-incomplete')

    def test_response_request_bytes_must_match_command_source(self):
        for hx in ('22f1a2','22f19e00'):
            response=self.response();response['requestHex']=hx
            with self.assertRaisesRegex(ValueError,'request-mismatch'):self.audit(response)

    def test_invalid_request_is_rejected_even_when_command_descriptor_is_unresolved(self):
        for hx in (None,'22 f19e','22f19','zz','01'):
            response=self.response();response['requestHex']=hx
            with self.assertRaises(ValueError):self.audit(response,complete=False)

    def test_unresolved_command_does_not_hide_duplicate_or_outside_definition_requests(self):
        report={'responseOriginAudit':None,'commandContextAudit':self.command(False)}
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'language';path.write_bytes(b'provider fixture')
            with patch('scripts.diagnostics.x431_command_origin.audit_source_command_response',return_value=report), \
                 patch.multiple('scripts.diagnostics.x431_command_origin',
                     SYS_DATA_LOGICAL_SHA256=hashlib.sha256(b'').hexdigest(),
                     REVIEWED_LANGUAGE_SHA256=hashlib.sha256(b'provider fixture').hexdigest()), \
                 patch('scripts.diagnostics.x431_variant_selection.audit_version_vectors',return_value={
                     'requestGroups':[{'requestCandidateHex':'22F19E'}]}):
                row=dict(commandSelection={},responseObservation=self.response())
                with self.assertRaisesRegex(ValueError,'duplicate-request'):
                    audit_source_command_version_observations({},[row,row],b'',path)
                row['responseObservation']['requestHex']='22f1a2'
                with self.assertRaisesRegex(ValueError,'outside-definition'):
                    audit_source_command_version_observations({},[row],b'',path)

    def test_unresolved_command_is_omitted_from_formula_inputs_but_retained_in_report(self):
        report={'responseOriginAudit':None,'commandContextAudit':self.command(False)}
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'language';path.write_bytes(b'provider fixture')
            with patch('scripts.diagnostics.x431_command_origin.audit_source_command_response',return_value=report), \
                 patch.multiple('scripts.diagnostics.x431_command_origin',
                     SYS_DATA_LOGICAL_SHA256=hashlib.sha256(b'').hexdigest(),
                     REVIEWED_LANGUAGE_SHA256=hashlib.sha256(b'provider fixture').hexdigest()), \
                 patch('scripts.diagnostics.x431_variant_selection.audit_version_vectors',return_value={
                     'requestGroups':[{'requestCandidateHex':'22F19E'}]}), \
                 patch('scripts.diagnostics.x431_variant_selection.audit_version_observations',return_value={
                     'calculationComplete':False}) as calculate:
                row=dict(commandSelection={},responseObservation=self.response())
                result=audit_source_command_version_observations({},[row],b'',path)
                calculate.assert_called_once_with({},[],path)
                self.assertFalse(result['calculationComplete'])
                self.assertEqual(result['commandOriginAudits'],[report])
                self.assertFalse(result['actualCommandLookupRuntimeQualified'])

    def test_empty_observations_cannot_bypass_either_source_hash(self):
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'language';path.write_bytes(b'unknown source')
            with self.assertRaisesRegex(ValueError,'logical-source-unreviewed'):
                audit_source_command_version_observations({},[],b'',path)
            with patch('scripts.diagnostics.x431_command_origin.SYS_DATA_LOGICAL_SHA256',hashlib.sha256(b'').hexdigest()):
                with self.assertRaisesRegex(ValueError,'language-source-unreviewed'):
                    audit_source_command_version_observations({},[],b'',path)

    @contextmanager
    def version_source_fixture(self, selections):
        with tempfile.TemporaryDirectory() as directory, ExitStack() as stack:
            path=Path(directory)/'language';path.write_bytes(b'provider fixture')
            reports=[];observations=[]
            for index,selection in enumerate(selections):
                command=self.command();command['explicitSourceSelection']=selection
                reports.append({'responseOriginAudit':{},'commandContextAudit':command})
                response=self.response();response['requestHex']=('22f19e','22f1a2')[index]
                observations.append(dict(commandSelection=selection,responseObservation=response))
            stack.enter_context(patch('scripts.diagnostics.x431_command_origin.audit_source_command_response',side_effect=reports))
            stack.enter_context(patch.multiple('scripts.diagnostics.x431_command_origin',
                SYS_DATA_LOGICAL_SHA256=hashlib.sha256(b'').hexdigest(),
                REVIEWED_LANGUAGE_SHA256=hashlib.sha256(b'provider fixture').hexdigest()))
            stack.enter_context(patch('scripts.diagnostics.x431_variant_selection.audit_version_vectors',return_value={
                'requestGroups':[{'requestCandidateHex':'22F19E'},{'requestCandidateHex':'22F1A2'}]}))
            calculate=stack.enter_context(patch('scripts.diagnostics.x431_variant_selection.audit_version_observations',
                return_value={'calculationComplete':False}))
            yield path,observations,calculate

    def test_version_request_indices_may_vary_with_one_source_selection(self):
        selection=self.command()['explicitSourceSelection']
        with self.version_source_fixture([selection,selection|{'requestIndex':2}]) as (path,rows,calculate):
            result=audit_source_command_version_observations({},rows,b'',path)
            self.assertEqual(result['sourceSelectionCohort'],{k:selection[k] for k in ('familyId','systemKey','linkIndex','pairIndex')})
            self.assertTrue(result['sourceSelectionCohortConsistentCandidate'])
            self.assertFalse(result['actualTransportContinuityQualified'])
            self.assertEqual(len(calculate.call_args.args[1]),2)

    def test_individually_successful_observations_cannot_mix_any_source_selector(self):
        selection=self.command()['explicitSourceSelection']
        for key,value in (('familyId',14),('systemKey',0xd41),('linkIndex',1),('pairIndex',1)):
            with self.version_source_fixture([selection,selection|{key:value}]) as (path,rows,calculate):
                with self.assertRaisesRegex(ValueError,'mixed-source-selection'):
                    audit_source_command_version_observations({},rows,b'',path)
                calculate.assert_not_called()

    def test_missing_or_noninteger_source_metadata_cannot_be_repaired_by_positive_response(self):
        selection=self.command()['explicitSourceSelection']
        for invalid in ({k:v for k,v in selection.items() if k!='pairIndex'},selection|{'linkIndex':True}):
            with self.version_source_fixture([invalid]) as (path,rows,calculate):
                with self.assertRaisesRegex(ValueError,'source-selection-required'):
                    audit_source_command_version_observations({},rows,b'',path)
                calculate.assert_not_called()

    def test_empty_source_observations_never_prove_a_transport_cohort(self):
        with self.version_source_fixture([]) as (path,rows,calculate):
            result=audit_source_command_version_observations({},rows,b'',path)
            self.assertIsNone(result['sourceSelectionCohort'])
            self.assertFalse(result['sourceSelectionCohortConsistentCandidate'])
            self.assertFalse(result['actualTransportContinuityQualified'])

    def test_request_flag_and_filter_word_cannot_be_replaced_by_unrelated_context(self):
        for key,value in (('requestFlagByte',0),('filterIdWord',0)):
            response=self.response();response['communication']['retryContext'][key]=value
            with self.assertRaisesRegex(ValueError,'retry-context-mismatch'):self.audit(response)

    def test_matching_filter_number_does_not_bind_a_different_source_record(self):
        response=self.response();changed='100301e1'+self.record[8:]
        response['communication']['modeContext']={'communicationRecordHex':changed,'pairIndex':0}
        with self.assertRaisesRegex(ValueError,'mode-context-mismatch'):self.audit(response)

    def test_explicit_mode_fields_are_checked_and_then_linked_to_actual_source_record(self):
        response=self.response();response['communication']['modeContext']={'modeByte':0x10,'subtypeByte':3}
        self.assertIsNotNone(self.audit(response)['responseOriginAudit'])
        response['communication']['modeContext']={'modeByte':0x30,'subtypeByte':3}
        with self.assertRaises(ValueError):self.audit(response)

    def test_response_schema_and_source_selection_reject_unknown_fields_and_unpinned_sources(self):
        with self.assertRaises(ValueError):self.audit(self.response()|{'nativeReturn':5})
        for value in ({},{'familyId':13,'systemKey':3392,'linkIndex':0,'pairIndex':0,'requestIndex':True},
                      {'familyId':13,'systemKey':3392,'linkIndex':0,'pairIndex':0,'requestIndex':1}):
            with self.assertRaises(ValueError):audit_source_command_context(b'unknown',b'unknown',value)

    def test_other_source_lookup_modes_cannot_reuse_mode10_response_association(self):
        for mode, subtype in ((0x30, 1), (0x30, 4), (0x50, 5)):
            command = self.command(False)
            command['enterSysCallerSourceAudit'] = audit_enter_sys_command_caller(mode, subtype)
            from scripts.diagnostics.x431_version_response import audit_communication_mode_context
            command['communicationSourceAudit'].update(audit_communication_mode_context({'modeByte': mode, 'subtypeByte': subtype}))
            with patch('scripts.diagnostics.x431_command_origin.audit_source_command_context',return_value=command):
                with self.assertRaisesRegex(ValueError, 'wrapper-mismatch'):
                    audit_source_command_response(b'', b'', dict(commandSelection={}, responseObservation=self.response()))

    def test_pinned_enter_sys_paths_distinguish_source_lookup_from_response_and_bounds(self):
        for mode, subtype, site, comm_site in ((0x10, 3, 0x19e20, 0x19f4e),
                (0x30, 1, 0x19c40, 0x19d70), (0x30, 4, 0x19c40, 0x19d70),
                (0x50, 5, 0x19e20, 0x19f4e)):
            r = audit_enter_sys_command_caller(mode, subtype)
            self.assertEqual((r['lookupCallSite'], r['communicationCallSite']), (site, comm_site))
            self.assertEqual(r['lookupObjectSymbol'], 'FromCmdIdToCmdBuff')
            self.assertEqual(r['lookupArgumentCapacity'], 512)
            self.assertFalse(r['actualLookupCallQualified']);self.assertFalse(r['nativeScratchBoundsQualified'])
            self.assertTrue(r['commandResponseAssociationAuditImplemented'])
        for mode, subtype in ((0x60, 3), (0x30, 3), (0x50, 3), (0x10, 5), (True, 3), (0x10, 256)):
            with self.assertRaises(ValueError):audit_enter_sys_command_caller(mode, subtype)


if __name__=='__main__':unittest.main()
