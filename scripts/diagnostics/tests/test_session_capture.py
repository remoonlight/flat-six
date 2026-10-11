from copy import deepcopy
import hashlib
import unittest
from unittest.mock import patch

from scripts.diagnostics.session_capture import (
    GUARDS, application_request, command_source, observed_commands,
    profile_sequences, response_status, sequence_matches,
    mode30_source, mode30_entry_source, entry_pair_context, transport_context, segmented_context, build,
    exit_source, exit_record_index, matched_exit_record, exit_command_context, exit_source_context,
    subsystem_source, subsystem_profile_context,
)
from scripts.diagnostics.tests.test_x431_capture import fix


class SessionCaptureTests(unittest.TestCase):
    def frames(self, request='1089', pdu='5089', padding='4210000000'):
        req=bytes.fromhex(request);data=bytes.fromhex(pdu)
        q=bytearray.fromhex('55aaf0f80016012701640001ff020d610108e3c0')+bytes([len(req)])+req+bytes(7-len(req))+b'\0'
        r=bytearray.fromhex('55aaf8f0001601670100000155aa0b08f100')+bytes([len(data)])+data+bytes.fromhex(padding)+b'\x12\x34\0'
        out=[]
        for i,(raw,direction) in enumerate([(q,'host_to_controller'),(r,'controller_to_host')]):
            b=fix(raw);out.append(dict(frameId='f'+str(i),frameHex=b.hex(),frameSha256=hashlib.sha256(b).hexdigest(),
                stream=[2,direction,2],length=len(b),sequenceByte=b[6],commandByte=b[7],startRecord=10+i))
        return out

    def sequence(self):
        return [dict(observationId='q'+str(i),hostCommandIndex=i,requestHex=hx,txId=0x71E,rxId=0x788,
                     adapterStream=[2,2],capturePhase='coding-workflow',requestRecord=10+i,
                     status='positive-session-candidate' if hx=='1089' else 'positive-read-candidate')
                for i,hx in enumerate(['1089','1A9F','1089'])]

    def test_padding_never_extends_session_pdu_or_becomes_identity_data(self):
        frames=self.frames();rows=observed_commands(frames,{0x71E:0x788},0)
        self.assertEqual(len(rows),1);row=rows[0]
        self.assertEqual(row['pduHex'],'5089');self.assertEqual(row['paddingHex'],'4210000000')
        self.assertEqual(row['uninterpretedSessionDataHex'],'')
        self.assertEqual(row['pduSha256'],hashlib.sha256(bytes.fromhex('5089')).hexdigest())
        self.assertEqual(row['status'],'positive-session-candidate')
        for key,value in GUARDS.items():self.assertEqual(row[key],value)

    def test_session_negative_pending_echo_address_and_dtc_contract_separate(self):
        for pdu,status in [('7F1012','negative'),('7F1078','pending'),('5086','echo-mismatch')]:
            rows=observed_commands(self.frames(pdu=pdu,padding='00000000' if len(pdu)==6 else '0000000000'),{0x71E:0x788},0)
            self.assertEqual(rows[0]['status'],status)
        ret=dict(rxId=0x788,pduHex='5089')
        self.assertEqual(response_status('1089',ret,0x77A),'address-unqualified')
        self.assertEqual(response_status('1800FF00',dict(rxId=0x788,pduHex='5800'),0x788),'dtc-response-contract-unqualified')
        self.assertEqual(response_status('1902FF',dict(rxId=0x788,pduHex='590200'),0x788),'dtc-response-contract-unqualified')

    def test_unknown_host_commands_are_boundaries_not_filtered_out(self):
        frames=self.frames();frames[0]['frameHex']=fix(bytearray.fromhex(frames[0]['frameHex'])[:21]+bytearray.fromhex('2E')+bytearray.fromhex(frames[0]['frameHex'])[22:]).hex()
        rows=observed_commands(frames,{0x71E:0x788},0)
        self.assertEqual(rows[0]['status'],'boundary-unreviewed-host-command')
        self.assertIsNone(rows[0]['requestHex'])
        self.assertIsNone(application_request(bytes.fromhex(frames[0]['frameHex'])))

    def test_malformed_checksum_request_length_and_stream_pairing_fail(self):
        frames=self.frames();raw=bytes.fromhex(frames[0]['frameHex'])
        self.assertIsNone(application_request(raw[:-1]+bytes([raw[-1]^1])))
        changed=bytearray(raw);changed[20]=3
        self.assertIsNone(application_request(fix(changed)))
        frames[1]['stream'][2]=3
        self.assertEqual(observed_commands(frames,{0x71E:0x788},0)[0]['status'],'unpaired')

    def test_consecutive_positive_source_order_is_candidate_only(self):
        rows=self.sequence();matched=sequence_matches(['1089','1A9F','1089'],rows,0x71E,0x788)
        self.assertEqual(len(matched),1);self.assertEqual(matched[0]['observations'],['q0','q1','q2'])
        self.assertFalse(matched[0]['actualInitializationQualified'])
        self.assertFalse(matched[0]['diagnosticSequenceQualified'])

    def test_boundary_phase_stream_address_negative_and_filtered_gaps_break_sequence(self):
        for mutation in ['boundary','phase','stream','address','negative','gap']:
            with self.subTest(mutation=mutation):
                rows=self.sequence()
                if mutation=='boundary':rows.insert(1,dict(rows[0],requestHex=None,hostCommandIndex=1))
                if mutation=='phase':rows[1]['capturePhase']='before-coding'
                if mutation=='stream':rows[1]['adapterStream']=[2,3]
                if mutation=='address':rows[1]['rxId']=0x77A
                if mutation=='negative':rows[1]['status']='negative'
                if mutation=='gap':rows[1]['hostCommandIndex']=99
                self.assertEqual(sequence_matches(['1089','1A9F','1089'],rows,0x71E,0x788),[])
        self.assertEqual(sequence_matches(['1089',None,'1089'],self.sequence(),0x71E,0x788),[])

    def test_command_descriptor_length_unreviewed_service_and_long_vendor_request_are_gaps(self):
        for raw,reason in [('01021089',None),('00023E00',None),('01072EC00010020301','source-service-byte-unreviewed'),
                           ('010722C00010040301','single-application-request-shape-unreviewed'),
                           ('01031089','source-descriptor-length-unreviewed')]:
            with self.subTest(raw=raw),patch('scripts.diagnostics.session_capture._lookup_command',return_value=dict(recordFound=True,
                decodedPayloadHex=raw,decodedPayloadSha256='a'*64,commandRecordIndex=1)):
                result=command_source(b'',0x200007);self.assertEqual(result['reason'],reason)
                self.assertFalse(result['postprocessedRuntimeBufferQualified'])
                if reason:self.assertIsNone(result['requestCandidateHex'])

    def fixture_profile(self):
        link=dict(linkIndex=0,serviceVariantIndex=6,communicationSourceOffset=76517,
            communication=dict(modeByte=0x10,pairs=[dict(pairIndex=0,storedTx=0x71E<<5,storedRx=0x788<<5,
                subtypeByte=3,parameterIds=[7,6,7])]),dtc=dict(readCommandSlots=[dict(index=0,sourceOffset=1,
                role='sessionPrecondition',class_='static-source-confirmed',wireCandidateHex='1089')]))
        link['dtc']['readCommandSlots'][0]['class']=link['dtc']['readCommandSlots'][0].pop('class_')
        profile=dict(profileId='pdk',module='Getriebesteuerung_PDK',variant='PDK',diagnosticSources=[dict(requestedEcuId=2,
            bindingStatus='exact-source-name-match',links=[link])])
        return dict(generation='981',ecuId=2),profile,{2:dict(txId=0x71E,rxId=0x788)}, {7:dict(requestCandidateHex='1089'),6:dict(requestCandidateHex='1A9F')}

    def test_generation_route_and_every_source_pair_remain_separate(self):
        unit,profile,routes,commands=self.fixture_profile()
        result=profile_sequences(unit,profile,self.sequence(),routes,commands)
        self.assertEqual(len(result['links'][0]['pairs'][0]['historicalSequences']),1)
        self.assertFalse(result['links'][0]['actualRuntimeSequenceSelected'])
        self.assertEqual(result['links'][0]['diagnosticReadSlots'][0]['observationStatuses'],{'positive-session-candidate':2})
        result=profile_sequences(dict(unit,generation='982'),profile,self.sequence(),routes,commands)
        self.assertIsNone(result['addressReference'])
        self.assertEqual(result['links'][0]['pairs'][0]['historicalSequences'],[])
        profile['diagnosticSources'][0]['links'][0]['communication']['pairs'][0]['storedRx']+=1
        result=profile_sequences(unit,profile,self.sequence(),routes,commands)
        self.assertFalse(result['links'][0]['pairs'][0]['literalAddressReferenceMatches'])

    def test_transport_context_requires_source_proof_and_exact_mode(self):
        self.assertIsNone(transport_context({},0x30,{'subtypeByte':1},[],None))
        self.assertIsNone(transport_context({},0x50,{'subtypeByte':1},[],{}))
        self.assertIsNone(transport_context({},0x30,{'subtypeByte':4},[],{}))
        self.assertIsNone(transport_context({'commandId':1},0x30,{'subtypeByte':1},[],{}))

    def transport_command(self,identifier,descriptor):
        return dict(commandId=identifier,commandIdHex=f'{identifier:08X}',decodedDescriptorHex=descriptor,
                    descriptorSha256=hashlib.sha256(bytes.fromhex(descriptor)).hexdigest(),descriptorComplete=True)

    def test_transport_setup_preserves_offer_and_distinct_later_address(self):
        for identifier,destination,offered,later_rx in [(0x3001B2,0x26,0x302,0x302),
                (0x3001B3,0x35,0x303,0x303),(0x3001B4,0x22,0x304,0x304),
                (0x3001B5,0x23,0x305,0x304)]:
            with self.subTest(identifier=identifier):
                descriptor=(bytes([1,7,destination,0xC0,0,0x10])+offered.to_bytes(2,'little')+b'\x01').hex()
                pair=dict(subtypeByte=1,storedTx=0x200<<5,storedRx=(0x200+destination)<<5)
                later=[dict(pairIndex=2,storedTx=0x4c3<<5,storedRx=later_rx<<5)]
                original=deepcopy(later)
                result=transport_context(self.transport_command(identifier,descriptor),0x30,pair,later,{})
                self.assertEqual(result['role'],'transport-channel-setup-source-candidate')
                self.assertEqual(result['offeredReceiveChannel'],offered)
                self.assertEqual(result['laterSourcePairs'][0]['sourceRxShiftCandidate'],later_rx)
                self.assertEqual(result['offerDiffersFromLaterReference'],offered!=later_rx)
                self.assertEqual(later,original)
                self.assertFalse(result['firstByteIsQualifiedEcuService'])
                self.assertFalse(result['actualNegotiatedChannelQualified'])
                self.assertTrue(result['needsActualNegotiationResponse'])
                for key,value in GUARDS.items():self.assertEqual(result[key],value)

    def test_transport_parameters_do_not_qualify_zero_timing(self):
        for identifier in [0x1A71,0x1A82]:
            result=transport_context(self.transport_command(identifier,'0106A00F00FF00FF'),
                0x30,dict(subtypeByte=1),[],{})
            self.assertEqual(result['sourceBlockSizeByte'],15)
            self.assertEqual(result['sourceAckTimingByte'],0)
            self.assertEqual(result['sourceSeparationTimingByte'],0)
            self.assertFalse(result['actualTimingQualified'])
            self.assertFalse(result['zeroTimingMeaningQualified'])

    def test_transport_modified_or_incomplete_descriptor_is_rejected(self):
        command=self.transport_command(0x3001B4,'010722C00010040301')
        for changes in [dict(decodedDescriptorHex='010722C00010050301'),dict(descriptorComplete=False)]:
            with self.subTest(changes=changes),self.assertRaisesRegex(ValueError,'descriptor-source-changed'):
                transport_context(dict(command,**changes),0x30,dict(subtypeByte=1),[],{})

    def test_transport_native_original_rejected_before_elf_parse(self):
        with patch('scripts.diagnostics.x431_native_binding._Elf') as elf:
            for source in [b'',b'changed',bytearray(b'changed')]:
                with self.assertRaisesRegex(ValueError,'original-unreviewed'):mode30_source(source)
            elf.assert_not_called()

    def test_entry_original_is_checked_before_elf_parse(self):
        with patch('scripts.diagnostics.x431_native_binding._Elf') as elf:
            for source in [b'',b'changed',bytearray(b'changed')]:
                with self.assertRaisesRegex(ValueError,'entry-original-unreviewed'):mode30_entry_source(source)
            elf.assert_not_called()

    def test_entry_requires_comm_original_before_any_catalog_read(self):
        with patch('scripts.diagnostics.session_capture.source_profiles') as catalog:
            with self.assertRaisesRegex(ValueError,'entry-comm-source-required'):
                build(None,None,None,None,None,sysp_library='source')
            catalog.assert_not_called()

    def test_pair_source_requires_exact_mode_and_caller_proof(self):
        for mode,proof in [(0x10,{'entryCallerSource':{}}),(0x30,None),(0x30,{}),(0x30,{'entryCallerSource':None})]:
            self.assertIsNone(entry_pair_context(mode,{},3,proof))

    def test_pair_mutation_dependencies_do_not_select_static_words_or_addresses(self):
        proof={'entryCallerSource':{'entry':0x19920}}
        for index in range(4):
            pair=dict(pairIndex=index,storedTx=0xdead1234,storedRx=0x87654321)
            original=deepcopy(pair);row=entry_pair_context(0x30,pair,4,proof)
            self.assertEqual(row['sourceStaticFilterWord'],0x12344321)
            self.assertEqual(row['initializesAtThisPair'],index==0)
            self.assertEqual(row['dependsOnPriorResponse'],index in (1,2))
            self.assertEqual(row['wordOverwrittenBeforeUse'],index in (1,2))
            self.assertEqual(row['updateBeforeThisPair'],index==1)
            self.assertFalse(row['staticWordIsRuntimeSelection'])
            self.assertFalse(row['actualNegotiatedChannelQualified'])
            self.assertEqual(pair,original)

    def test_unrelated_source_profile_does_not_inherit_entry_context(self):
        unit,profile,routes,commands=self.fixture_profile()
        row=profile_sequences(unit,profile,self.sequence(),routes,commands,{'entryCallerSource':{'entry':0x19920}})
        self.assertIsNone(row['links'][0]['pairs'][0]['entryPairSource'])

    def test_segmented_context_requires_mode_subtype_and_source(self):
        for mode,pair,proof in [(0x10,{},None),(0x30,{'subtypeByte':1},{}),
                (0x30,{'subtypeByte':4},None),(0x30,{'subtypeByte':4},{})]:
            self.assertIsNone(segmented_context({},mode,pair,proof))

    def test_segmented_short_source_keeps_completion_and_close_separate(self):
        for identifier,raw in [(0x1A72,'010322F1A2'),(0x1A79,'01021089')]:
            command=self.transport_command(identifier,raw)
            row=segmented_context(command,0x30,{'subtypeByte':4},{'segmentedSource':{'entry':0x10da4}})
            self.assertTrue(row['sourceRequestPacking']['sourceLengthArithmeticComplete'])
            self.assertEqual(row['sourceRequestPacking']['fullChunkCountCandidate'],0)
            self.assertEqual(row['sourceCounterAdvanceIfAggregateNonnegative'],1)
            self.assertEqual(row['completionPayloadHex'],'A3')
            self.assertFalse(row['completionNonnegativeIsChannelCloseProof'])
            self.assertFalse(row['actualFinalSequenceCounterQualified'])
            self.assertIn('channel-close-contract',row['missingEvidence'])
            self.assertIsNone(row['writePayload'])

    def test_segmented_long_source_never_assumes_length_stability(self):
        for length in [6,7,13,255]:
            command=self.transport_command(1,(bytes([1,length])+bytes(length)).hex())
            row=segmented_context(command,0x30,{'subtypeByte':4},{'segmentedSource':{'entry':0x10da4}})
            packing=row['sourceRequestPacking']
            self.assertFalse(packing['sourceLengthArithmeticComplete'])
            self.assertIsNone(packing['finalCommandBytesCandidate'])
            self.assertIsNone(packing['rereadRequestBytes'])
            self.assertFalse(row['actualRequestLengthStableAcrossCallbacks'])

    def test_segmented_corrupt_descriptor_is_rejected_and_other_flag_not_promoted(self):
        proof={'segmentedSource':{'entry':0x10da4}};pair={'subtypeByte':4}
        command=self.transport_command(0x1A72,'010322F1A2')
        with self.assertRaisesRegex(ValueError,'segment-descriptor-source-changed'):
            segmented_context(dict(command,decodedDescriptorHex='010322F189'),0x30,pair,proof)
        self.assertIsNone(segmented_context(dict(command,descriptorComplete=False),0x30,pair,proof))
        self.assertIsNone(segmented_context(self.transport_command(1,'00021089'),0x30,pair,proof))

    def test_exit_optional_sources_are_required_before_catalog_read(self):
        for options,message in [
            ({'file_library':'file'},'file-and-logical'),
            ({'logical_sys_data':'logical'},'file-and-logical'),
            ({'file_library':'file','logical_sys_data':'logical'},'comm-and-sysp'),
            ({'comm_library':'comm','file_library':'file','logical_sys_data':'logical'},'comm-and-sysp')]:
            with self.subTest(options=options),patch('scripts.diagnostics.session_capture.source_profiles') as catalog:
                with self.assertRaisesRegex(ValueError,message):build(None,None,None,None,None,**options)
                catalog.assert_not_called()

    def test_exit_originals_are_checked_before_native_or_logical_parse(self):
        with patch('scripts.diagnostics.x431_native_binding._Elf') as elf:
            with self.assertRaisesRegex(ValueError,'exit-comm-original-unreviewed'):exit_source(b'',b'',b'')
            elf.assert_not_called()
        with patch('scripts.x431_re.sysdata.parse_payload') as parser:
            for blob in [b'',b'changed',bytearray(b'changed')]:
                with self.assertRaisesRegex(ValueError,'exit-logical-original-unreviewed'):exit_record_index(blob)
            parser.assert_not_called()

    def exit_fixture(self,trail=(0x1a76,)):
        unit,profile,routes,commands=self.fixture_profile();source=profile['diagnosticSources'][0];link=source['links'][0]
        source['variantName']='PDK';link['serviceSourceOffset']=80000;link['communication']['class']='static-source-confirmed'
        native=dict(protocolKind=0x10,consumedEnd=80000,trail=list(trail),pairs=[dict(storedTx=0x71e<<5,
            storedRx=0x788<<5,mode=3,params=[7,6,7])]);native['class']='static-source-confirmed'
        index={(2,76517,80000):dict(comm=native,service={'variants':[{'name':'other'}]*6+[{'name':'PDK'}]})}
        exits={0x1a76:self.transport_command(0x1a76,'0101A8')}
        return unit,profile,source,link,index,exits,routes,commands

    def test_exit_context_absent_without_explicit_source_proof(self):
        self.assertIsNone(exit_source_context(None,None,None,None,None,None))

    def test_exit_a8_mode_flag_and_close_qualification_are_separate(self):
        command=self.transport_command(0x1a76,'0101A8');before=deepcopy(command)
        for mode in [0x10,0x20,0x30,0x50]:
            row=exit_command_context(command,mode)
            self.assertEqual(row['sourceDescriptorFlagBeforeCommunication'],1 if mode==0x20 else 0)
            self.assertEqual(bool(row['transportCloseShapeCandidate']),mode==0x30)
            self.assertEqual(row['sourceSelectedSubtypeOverride'],1 if mode==0x30 else None)
            self.assertFalse(row['actualChannelCloseQualified']);self.assertFalse(row['actualDeviceReleaseQualified'])
            self.assertFalse(row['sourceIsQualifiedApplicationRead']);self.assertIsNone(row['writePayload'])
        self.assertEqual(command,before)
        self.assertIsNone(exit_command_context(self.transport_command(9,'0101A8'),0x30)['transportCloseShapeCandidate'])

    def test_exit_descriptor_hash_completeness_and_known_command_are_required(self):
        command=self.transport_command(0x1a76,'0101A8')
        with self.assertRaisesRegex(ValueError,'descriptor-source-changed'):
            exit_command_context(dict(command,decodedDescriptorHex='0101A3'),0x30)
        with self.assertRaisesRegex(ValueError,'exit-command-unreviewed'):
            exit_command_context(self.transport_command(0x1a76,'0101A3'),0x30)
        row=exit_command_context(dict(command,descriptorComplete=False),0x30)
        self.assertIsNone(row['transportCloseShapeCandidate']);self.assertIsNone(row['originalSourceDescriptorFlag'])

    def test_exit_source_name_address_params_boundary_and_lookup_mismatch_rejected(self):
        for mutation in ['key','name','mode','class','address','params','end']:
            unit,profile,source,link,index,exits,routes,commands=self.exit_fixture()
            raw=index[(2,76517,80000)]
            if mutation=='key':index={}
            if mutation=='name':raw['service']['variants'][6]={'name':'other'}
            if mutation=='mode':raw['comm']['protocolKind']=0x30
            if mutation=='class':raw['comm']['class']='unsupported-structured'
            if mutation=='address':raw['comm']['pairs'][0]['storedRx']+=1
            if mutation=='params':raw['comm']['pairs'][0]['params']=[7]
            if mutation=='end':raw['comm']['consumedEnd']+=1
            with self.subTest(mutation=mutation),self.assertRaisesRegex(ValueError,'session-exit-'):
                matched_exit_record(unit,source,link,index)

    def test_empty_exit_table_and_missing_profile_link_never_imply_close(self):
        unit,profile,source,link,index,exits,routes,commands=self.exit_fixture(trail=())
        context=exit_source_context(unit,source,link,index,exits,{'reviewed':True})
        self.assertEqual(context['sourceExitCount'],0);self.assertFalse(context['sourceHasExitCommands'])
        self.assertFalse(context['emptyTableIsCloseProof']);self.assertFalse(context['keepStopReturnIsSuccessProof'])
        profile['diagnosticSources']=[]
        row=profile_sequences(unit,profile,[],routes,commands,exit_proof={'reviewed':True},exit_index=index,exit_commands=exits)
        self.assertEqual(row['exitSourceStatus'],'missing-diagnostic-source-link');self.assertEqual(row['links'],[])
        self.assertFalse(row['actualSessionQualified'])

    def test_unresolved_exit_layout_is_unknown_instead_of_empty(self):
        unit,profile,source,link,index,exits,routes,commands=self.exit_fixture()
        link['communication']['class']=index[(2,76517,80000)]['comm']['class']='unsupported-structured'
        context=exit_source_context(unit,source,link,index,exits,{'reviewed':True})
        self.assertIsNone(context['sourceExitCount']);self.assertIsNone(context['sourceHasExitCommands'])
        self.assertEqual(context['sourceStatus'],'communication-layout-unresolved')

    def subsystem_fixture(self, exact=False):
        unit=dict(generation='981',ecuId=47)
        variants=[dict(name='SWSG_BaseSys',slots=[1,2,3,4,5,6]),
                  dict(name='SWSG_VR15',slots=[11,12,13,14,15,16])]
        records={(47,700,740):dict(index=0,service=dict(variants=variants))}
        links=[dict(linkIndex=0,communicationSourceOffset=700,serviceSourceOffset=740,
            serviceVariantIndex=1,sourceSlots=variants[1]['slots'][:])] if exact else []
        source=dict(requestedEcuId=47,variantName='SWSG_VR15' if exact else 'SWSG_other',
            systemKey=0x0d2f,fallbackUsed=False,linkMatchCount=int(exact),links=links,
            bindingStatus='exact-source-name-match' if exact else 'source-name-missing')
        profile=dict(profileId='swsg',module='SWSG',variant=source['variantName'],diagnosticSources=[source])
        return unit,profile,records

    def test_subsystem_sources_checked_together_before_elf_parse(self):
        with patch('scripts.diagnostics.x431_native_binding._Elf') as elf:
            for blob in [b'',b'changed',bytearray(b'changed')]:
                with self.assertRaisesRegex(ValueError,'subsystem-sysp-original-unreviewed'):
                    subsystem_source(blob,b'')
            with patch('scripts.diagnostics.x431_version_response.SYSP_SHA256',hashlib.sha256(b'checked').hexdigest()):
                with self.assertRaisesRegex(ValueError,'subsystem-file-original-unreviewed'):
                    subsystem_source(b'checked',b'changed')
            elf.assert_not_called()

    def test_subsystem_context_requires_explicit_proof(self):
        self.assertIsNone(subsystem_profile_context(None,None,None,None))
        unit,profile,routes,commands=self.fixture_profile()
        row=profile_sequences(unit,profile,[],routes,commands)
        self.assertIsNone(row['subsystemSourceContext'])

    def test_default_source_never_repairs_missing_original_version(self):
        unit,profile,records=self.subsystem_fixture();before=deepcopy((profile,records))
        for generation in ('981','982'):
            row=subsystem_profile_context(dict(unit,generation=generation),profile,records,{'reviewed':True})
            self.assertEqual(row['sourceBindingStatus'],'source-name-missing')
            self.assertEqual(row['exactNameMatchCount'],0);self.assertEqual(row['defaultNameMatchCount'],1)
            self.assertEqual(row['defaultVariantNameCandidate'],'SWSG_BaseSys')
            self.assertFalse(row['fallbackUsed']);self.assertFalse(row['originalVersionQualifiedByDefault'])
            self.assertFalse(row['actualVersionSelectionQualified']);self.assertFalse(row['actualDefaultBranchSelected'])
            for key,value in GUARDS.items():self.assertEqual(row[key],value)
        self.assertEqual((profile,records),before)

    def test_subsystem_nonmatch_last_slots_and_empty_prior_slots_stay_distinct(self):
        unit,profile,records=self.subsystem_fixture()
        row=subsystem_profile_context(unit,profile,records,{})
        attempt=row['sourceLinkAttempts'][0]
        self.assertEqual(attempt['slotsAfterCompleteNameMismatchCandidate'],[11,12,13,14,15,16])
        self.assertFalse(attempt['sourceReturnIsNameMatchProof']);self.assertFalse(attempt['actualLinkSelected'])
        records[(47,700,740)]['service']['variants']=[]
        empty=subsystem_profile_context(unit,profile,records,{})['sourceLinkAttempts'][0]
        self.assertIsNone(empty['slotsAfterCompleteNameMismatchCandidate'])
        self.assertEqual(empty['mismatchSlotsOrigin'],'prior-runtime-slots-unresolved')

    def test_subsystem_exact_link_and_six_slots_are_rechecked(self):
        unit,profile,records=self.subsystem_fixture(exact=True)
        row=subsystem_profile_context(unit,profile,records,{})
        self.assertEqual(row['exactNameMatchCount'],1)
        self.assertEqual(row['exactNameSources'][0]['sourceSlots'],[11,12,13,14,15,16])
        profile['diagnosticSources'][0]['links'][0]['sourceSlots'][2]=999
        with self.assertRaisesRegex(ValueError,'exact-source-association-mismatch'):
            subsystem_profile_context(unit,profile,records,{})

    def test_subsystem_catalog_cannot_claim_missing_native_match_or_wrong_status(self):
        for mutation in ('missing','count','status','link-key','variant-index'):
            unit,profile,records=self.subsystem_fixture(exact=True);source=profile['diagnosticSources'][0]
            if mutation=='missing':source.update(links=[],linkMatchCount=0,bindingStatus='source-name-missing')
            if mutation=='count':source['linkMatchCount']=2
            if mutation=='status':source['bindingStatus']='source-name-missing'
            if mutation=='link-key':source['links'][0]['serviceSourceOffset']+=1
            if mutation=='variant-index':source['links'][0]['serviceVariantIndex']=0
            with self.subTest(mutation=mutation),self.assertRaisesRegex(ValueError,'exact-source-association-mismatch'):
                subsystem_profile_context(unit,profile,records,{})

    def test_subsystem_rejects_other_unit_family_name_or_adopted_fallback(self):
        for field,value in [('requestedEcuId',48),('systemKey',0x0b2f),('variantName','other'),('fallbackUsed',True)]:
            unit,profile,records=self.subsystem_fixture();profile['diagnosticSources'][0][field]=value
            with self.subTest(field=field),self.assertRaisesRegex(ValueError,'session-subsystem-'):
                subsystem_profile_context(unit,profile,records,{})
        unit,profile,records=self.subsystem_fixture()
        with self.assertRaisesRegex(ValueError,'unit-source-missing'):
            subsystem_profile_context(unit,profile,{},{} )

    def test_subsystem_name_comparison_is_case_sensitive(self):
        unit,profile,records=self.subsystem_fixture()
        profile['variant']=profile['diagnosticSources'][0]['variantName']='SWSG_vr15'
        row=subsystem_profile_context(unit,profile,records,{})
        self.assertEqual(row['exactNameMatchCount'],0)
        self.assertEqual(row['sourceLinkAttempts'][0]['exactNameVariantIndices'],[])

    def test_subsystem_duplicate_exact_links_are_preserved_without_selection(self):
        unit,profile,records=self.subsystem_fixture(exact=True)
        records[(47,800,840)]=deepcopy(records[(47,700,740)]);records[(47,800,840)]['index']=1
        source=profile['diagnosticSources'][0]
        source['links'].append(dict(source['links'][0],linkIndex=1,communicationSourceOffset=800,serviceSourceOffset=840))
        source.update(bindingStatus='source-link-ambiguous',linkMatchCount=2)
        row=subsystem_profile_context(unit,profile,records,{})
        self.assertEqual(row['exactNameMatchCount'],2);self.assertEqual(len(row['sourceLinkAttempts']),2)
        self.assertFalse(row['actualSourceLinkSelected'])

    def test_exit_profile_context_does_not_inherit_other_generation_route(self):
        unit,profile,source,link,index,exits,routes,commands=self.exit_fixture()
        before=deepcopy((profile,index,exits))
        for generation in ['981','982']:
            row=profile_sequences(dict(unit,generation=generation),profile,self.sequence(),routes,commands,
                exit_proof={'reviewed':True},exit_index=index,exit_commands=exits)
            context=row['links'][0]['exitSourceContext']
            self.assertEqual(context['sourceExitCommandIds'],['00001A76'])
            self.assertFalse(context['actualChannelCloseQualified']);self.assertFalse(row['links'][0]['actualRuntimeSequenceSelected'])
            self.assertFalse(row['actualSessionQualified'])
            self.assertEqual(row['addressReference'] is None,generation=='982')
        self.assertEqual((profile,index,exits),before)


if __name__=='__main__':unittest.main()
