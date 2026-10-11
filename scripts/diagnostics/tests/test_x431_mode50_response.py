import unittest, io, json, tempfile, struct
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from scripts.diagnostics.x431_mode50_response import (
    audit_mode50_response, audit_mode50_receive_sequence, audit_mode50_request_packing,
    audit_mode50_request_packet, audit_mode50_request_source,
    audit_mode50_request_source_context, audit_mode50_initialization, WRAPPERS)
from scripts.diagnostics.x431_version_response import audit_version_map_origin, audit_communication_mode_context, VERSION_GUARDS
from scripts.diagnostics.x431_command_origin import audit_source_command_response, audit_enter_sys_command_caller
from scripts.diagnostics.x431_version_response import main


def internal(frame):
    b = b'\0'+bytes.fromhex(frame)
    return {'path': 'direct', 'capacity': 2048, 'transportReturn': len(b), 'frameHex': b.hex()}


def positive(body='f19e'):
    return internal('0001'+'00'*7+f'{1+len(bytes.fromhex(body)):02x}'+'62'+body)


def negative(code):
    return internal('0001'+'00'*7+'037f22'+f'{code:02x}')


def observation(receive=None):
    return {'requestBytes': 3, 'extendedRequestByte': 0, 'internal': positive() if receive is None else receive}


def sequence(*receives, flag=1, initial=0):
    return {'requestBytes': 3, 'extendedRequestByte': 0, 'receives': list(receives),
            'retryContext': {'requestFlagByte': flag, 'initialCommunicationStatusByte': initial}}


def request_packet(length, **changes):
    area=bytes([length&255])+bytes(i&255 for i in range(1,length+1))
    return {'requestAreaHex':area.hex(),'extendedRequestByte':length>>8,
            'filterWord':0x12ab34cd,'channelWord':0x12345678}|changes


CURRENT_MODE50_RECORD = '50030180ea0000c0f70000055500000005a70310000200200001002000030020000a00200000000020000100580e00000000'


def source_record(pairs, mode=0x50):
    raw=bytes((mode,3,len(pairs)))
    for tx,rx,subtype,extra in pairs:
        raw+=struct.pack('<IIB',tx,rx,subtype)
        if subtype==5:raw+=struct.pack('<I',extra)
        raw+=b'\0'  # no source parameter IDs
    return (raw+bytes(13)).hex()  # zero trail count and three words


def source_request(record=CURRENT_MODE50_RECORD, pair=0, length=3):
    p=request_packet(length)
    return {k:p[k] for k in ('requestAreaHex','extendedRequestByte')}|{
        'communicationRecordHex':record,'pairIndex':pair}


INITIALIZATION_SITES = [0x11c76,0x11c92,0x11ca6,0x11d12,0x11d26,0x11d36,
    0x11d46,0x11d58,0x11d6c,0x11dbe,0x11dc8,0x11e8a,0x11fda,0x1220a]


def initialization_observation():
    config=bytearray(0x2358);config[0]=2;config[1]=0x50;config[5]=5
    config[0x12]=1;config[0x119]=5
    struct.pack_into('<I',config,0x20,0xea80f7c0);struct.pack_into('<I',config,0x2328,85)
    return dict(configurationHex=config.hex(),mainTokenFields=dict(systemModeWord=0,protocolKindByte=0x50,ecuSelectorHalfword=0),
        calls=[dict(callSite=site,returnWord=2 if site==0x11ca6 else 0) for site in INITIALIZATION_SITES])


class Mode50InitializationTests(unittest.TestCase):
    def test_literal_packets_and_source_success_with_failed_link_filter(self):
        r=audit_mode50_initialization(initialization_observation())
        self.assertEqual(r['sourceEnterFrameHexCandidate'],'55AA0D610108EA8055301005AAAAAAAA7F')
        self.assertEqual(r['sourceLinkFilterCommandHexCandidate'],'55AA18601900020000027E0000FFFFFF000000017E0000FFFF00009F')
        self.assertEqual(r['sourceNormalParameterWrittenPrefixHexCandidate'],'00000000000000000501F7C0')
        self.assertEqual(r['sourceInitializationReturnCandidate'],1)
        self.assertEqual(r['sourceLinkFilterReturnCandidate'],0);self.assertTrue(r['sourceLinkFilterFailureSuppressed'])
        self.assertEqual(len(r['calls']),14)
        self.assertFalse(r['sourceSuccessImpliesCompleteDriverConfiguration'])
        for k,v in VERSION_GUARDS.items():self.assertEqual(r[k],v)
        for k in ('headConfigurationQualified','runtimeCallbacksQualified','nativeOriginalInstructionsExecuted','actualDestinationPointerAliasProven','mainTokenAndConfigurationAliasProven'):
            self.assertFalse(r[k])

    def test_nested_provider_signed_positive_check_and_ignored_parent_result(self):
        for word,expected in [(0,0),(1,1),(0x7fffffff,1),(0x80000000,0),(0xffffffff,0)]:
            p=initialization_observation();p['calls'][-1]['returnWord']=word;r=audit_mode50_initialization(p)
            self.assertEqual(r['sourceLinkFilterReturnCandidate'],expected)
            self.assertEqual(r['sourceInitializationReturnCandidate'],1)
            self.assertEqual(r['sourceLinkFilterFailureSuppressed'],expected==0)

    def test_checked_failures_stop_at_exact_stage_and_keep_prior_filter_assignment(self):
        for site,expected,assigned in [(0x11c92,-1,False),(0x11d12,-2,False),(0x11e8a,-3,True),(0x11fda,-5,True)]:
            p=initialization_observation();index=INITIALIZATION_SITES.index(site);p['calls']=p['calls'][:index+1];p['calls'][-1]['returnWord']=1
            r=audit_mode50_initialization(p);self.assertEqual(r['sourceInitializationReturnCandidate'],expected)
            self.assertEqual(r['sourceFilterAssignedBeforeNormalParameterReturn'],assigned)
            self.assertIsNone(r['sourceLinkFilterReturnCandidate'])

    def test_enter_frame_masks_high_half_and_unchecked_driver_results_are_not_success(self):
        for word in (0x10000,0xffff0000):
            p=initialization_observation();p['calls'][-2]['returnWord']=word
            for index in (0,4,5,6,7,8,9,10):p['calls'][index]['returnWord']=0xffffffff
            r=audit_mode50_initialization(p);self.assertEqual(r['sourceInitializationReturnCandidate'],1)
            self.assertFalse(r['sourceAllDriverReturnsChecked'])
            for index in (0,4,5,6,7,8,9,10):self.assertFalse(r['calls'][index]['sourceReturnChecked'])

    def test_dbs_type_is_read_twice_when_first_low_half_is_not_two(self):
        for first,second,pin in [(0xffff0002,None,0x21),(1,10,0x21),(1,2,3)]:
            p=initialization_observation();p['calls'][2]['returnWord']=first
            if second is not None:p['calls'].insert(3,dict(callSite=0x11cb8,returnWord=second))
            r=audit_mode50_initialization(p);second_pin=next(c for c in r['calls'] if c['callSite']==0x11d12)
            self.assertEqual(second_pin['nonPointerArgumentWordCandidates']['1'],pin)
            self.assertEqual(len(r['calls']),14 if second is None else 15)

    def test_initializer_filter_index_and_pin_configuration_are_explicit(self):
        p=initialization_observation();config=bytearray.fromhex(p['configurationHex']);config[0x12]=2;config[5]=3
        struct.pack_into('<I',config,0x24,0x12345678);p['configurationHex']=config.hex();r=audit_mode50_initialization(p)
        self.assertEqual(r['sourceFilterConfigOffset'],0x24);self.assertEqual(r['sourceInitialFilterWordCandidate'],0x12345678)
        self.assertFalse(r['sourceFilterIndexIsSelectedPairProven'])
        self.assertEqual(next(c for c in r['calls'] if c['callSite']==0x11d12)['nonPointerArgumentWordCandidates'],{'0':2,'1':0x21,'2':0,'3':6,'4':14})

    def test_partial_observations_never_invent_completion(self):
        for count in range(14):
            p=initialization_observation();p['calls']=p['calls'][:count];r=audit_mode50_initialization(p)
            self.assertFalse(r['sourceCallSequenceComplete']);self.assertIsNone(r['sourceInitializationReturnCandidate'])
            self.assertEqual(r['nextExpectedCallSite'],INITIALIZATION_SITES[count])

    def test_main_fields_do_not_inherit_configuration_and_timing_bits_are_preserved(self):
        for mode,kind,selector,expected in [(7,0x20,0x301,15),(7,0x20,0x401,5),(8,0x20,0x301,50),(7,0x50,0x301,50)]:
            p=initialization_observation();p['mainTokenFields']=dict(systemModeWord=mode,protocolKindByte=kind,ecuSelectorHalfword=selector)
            r=audit_mode50_initialization(p);interval=next(c for c in r['calls'] if c['callSite']==0x11dbe)
            self.assertEqual(interval['nonPointerArgumentWordCandidates'],{'0':0,'1':expected,'2':1600})
            self.assertEqual(next(c for c in r['calls'] if c['callSite']==0x11d58)['nonPointerArgumentWordCandidates'],{'0':0x47610000})

    def test_invalid_extent_mode_fields_return_words_order_and_terminal_tail_rejected(self):
        bad=[]
        for field,value in [('configurationHex','00'),('mainTokenFields',{}),('calls',[dict(callSite=True,returnWord=0)]),('calls',[dict(callSite=0x11c76,returnWord=-1)]),('calls',[dict(callSite=0x11c76,returnWord=True)]),('calls',[dict(callSite=0x11c92,returnWord=0)])]:
            bad.append(initialization_observation()|{field:value})
        p=initialization_observation();p['calls'].append(dict(callSite=0x120ca,returnWord=0));bad.append(p)
        p=initialization_observation();p['calls'][1]['returnWord']=1;bad.append(p)
        p=initialization_observation();p['mainTokenFields']['systemModeWord']=True;bad.append(p)
        p=initialization_observation();config=bytearray.fromhex(p['configurationHex']);config[1]=0x10;p['configurationHex']=config.hex();bad.append(p)
        for p in bad:
            with self.assertRaises(ValueError):audit_mode50_initialization(p)

    def test_cli_initialization_audit_has_no_runtime_qualification(self):
        with tempfile.TemporaryDirectory() as temp:
            p=Path(temp)/'observation.json';p.write_text(json.dumps(initialization_observation()));out=io.StringIO()
            with redirect_stdout(out):self.assertEqual(main(['--kind','mode50-initialization','--observation-json',str(p)]),0)
            r=json.loads(out.getvalue());self.assertTrue(r['sourceLinkFilterFailureSuppressed']);self.assertFalse(r['headConfigurationQualified'])


class Mode50RequestSourceContextTests(unittest.TestCase):
    def test_current_source_literal_fields_and_packet(self):
        p=source_request();p['requestAreaHex']='0322f19e'
        r=audit_mode50_request_source_context(p)
        self.assertEqual(r['sourceFilterWordCandidate'],0xea80f7c0)
        self.assertEqual(r['sourceChannelWordCandidate'],0x55)
        self.assertEqual(r['sourceChannelWriterPairIndices'],[0])
        self.assertEqual(r['requestPacketAudit']['providerCommandHexCandidate'],'01FF030D610108EA80550322F19EAAAAAA')
        for k,v in VERSION_GUARDS.items():self.assertEqual(r[k],v)
        for k in ('actualFilterSetterCallProven','actualTokenPointersAliasProven','runtimeSourceWordsUnchangedProven','fullInitializationConfigurationReconstructed','initializerSideEffectsQualified','providerRuntimeBindingQualified','canFrameLayoutQualified'):
            self.assertFalse(r[k])

    def test_last_mode5_pair_writes_shared_channel_even_when_earlier_pair_selected(self):
        record=source_record([(0xaabb1234,0xdead5678,5,0x11223344),(0xcccc9876,0xbbbb4321,5,0xabcdef55),(0x11110001,0x22220002,3,None)])
        a=audit_mode50_request_source_context(source_request(record,0));b=audit_mode50_request_source_context(source_request(record,1))
        self.assertEqual((a['sourceFilterWordCandidate'],b['sourceFilterWordCandidate']),(0x12345678,0x98764321))
        for r in (a,b):
            self.assertEqual(r['sourceChannelWordCandidate'],0xabcdef55)
            self.assertEqual(r['sourceChannelWriterPairIndex'],1)
            self.assertEqual(r['sourceChannelWriterPairIndices'],[0,1])
            self.assertFalse(r['sourceChannelStoreHasPairIndex'])
        with self.assertRaisesRegex(ValueError,'wrapper-mismatch'):audit_mode50_request_source_context(source_request(record,2))

    def test_config_and_main_token_bases_are_distinct(self):
        r=audit_mode50_request_source_context(source_request())
        for config,token in [('sourceRequestLengthConfigOffset','sourceRequestLengthTokenOffset'),('sourceExtendedLengthConfigOffset','sourceExtendedLengthTokenOffset'),('sourceChannelConfigOffset','sourceChannelTokenOffset')]:
            self.assertEqual(r[config]+0x63c,r[token])
        self.assertEqual(r['sourceFilterTokenOffset'],0x65c)
        self.assertEqual(r['sourceChannelTokenOffset'],0x2964)

    def test_truncated_trailing_and_other_mode_records_rejected(self):
        for record in (CURRENT_MODE50_RECORD[:-2],CURRENT_MODE50_RECORD+'00',source_record([(1,2,5,85)],mode=0x10),'01'):
            with self.subTest(record=record),self.assertRaises(ValueError):audit_mode50_request_source_context(source_request(record))

    def test_explicit_pair_and_strict_fields_required(self):
        for key,value in [('pairIndex',True),('pairIndex',-1),('pairIndex',1),('extendedRequestByte',True),('requestAreaHex','0301')]:
            with self.subTest(key=key,value=value),self.assertRaises(ValueError):audit_mode50_request_source_context(source_request()|{key:value})
        for p in ({k:v for k,v in source_request().items() if k!='pairIndex'},source_request()|{'filterWord':0}):
            with self.assertRaises(ValueError):audit_mode50_request_source_context(p)

    def test_source_context_does_not_bypass_unsafe_packing(self):
        for n in (7,42,918,1530):
            r=audit_mode50_request_source_context(source_request(length=n))
            self.assertIsNone(r['requestPacketAudit']['providerCommandHexCandidate'])
            self.assertFalse(r['requestPacketAudit']['sourcePayloadFullyRepresented'])

    def test_cli_source_context_is_offline(self):
        with tempfile.TemporaryDirectory() as temp:
            p=Path(temp)/'observation.json';p.write_text(json.dumps(source_request()))
            out=io.StringIO()
            with redirect_stdout(out):self.assertEqual(main(['--kind','mode50-request-source-context','--observation-json',str(p)]),0)
            r=json.loads(out.getvalue());self.assertEqual(r['sourceChannelWordCandidate'],85)
            self.assertFalse(r['vehicleIO']);self.assertFalse(r['executionEnabled'])


class Mode50RequestPacketTests(unittest.TestCase):
    def test_literal_short_and_multi_packet_layouts(self):
        for n,expected in (
            (3,'01FF030D61010812AB7803010203AAAAAA'),
            (6,'01FF030D61010812AB7806010203040506'),
            (8,'02FF030D61010812AB78100801020304050D61010812AB7821060708000000'),
            (12,'03FF030D61010812AB78100C01020304050D61010812AB7821060708090A0B0D61010812AB78220C0000000000')):
            with self.subTest(n=n):
                r=audit_mode50_request_packet(request_packet(n))
                self.assertEqual(r['providerCommandHexCandidate'],expected)
                self.assertEqual(r['sourcePayloadBytesPreservedAfterClear'],n)
                self.assertTrue(r['sourcePayloadFullyRepresented'])
                for k,v in VERSION_GUARDS.items():self.assertEqual(r[k],v)
                self.assertFalse(r['providerRuntimeBindingQualified'])
                self.assertFalse(r['canFrameLayoutQualified'])
                self.assertFalse(r['nativeOriginalInstructionsExecuted'])

    def test_seven_byte_source_copy_exceeds_provider_length(self):
        r=audit_mode50_request_packet(request_packet(7))
        self.assertEqual(r['sourceCommandBeforeReceiveClearHex'],'01FF030D61010812AB7807010203040506')
        self.assertEqual(r['sourcePayloadBytesRepresentedBeforeClear'],6)
        self.assertEqual(r['status'],'source-provider-length-truncates-payload')
        self.assertFalse(r['sourcePayloadFullyRepresented']);self.assertIsNone(r['providerCommandHexCandidate'])

    def test_receive_clear_boundary_and_freshness_even_when_input_is_zero(self):
        a=audit_mode50_request_packet(request_packet(41))
        self.assertEqual(a['sourceReceiveClearOverwritesCommandBytes'],0)
        b=audit_mode50_request_packet(request_packet(42))
        self.assertEqual(b['sourceReceiveClearOverwritesCommandBytes'],11)
        before=bytes.fromhex(b['sourceCommandBeforeReceiveClearHex']);after=bytes.fromhex(b['sourceCommandAfterReceiveClearHex'])
        self.assertEqual(len(before),115);self.assertEqual(before[:104],after[:104]);self.assertEqual(after[104:],bytes(11))
        self.assertEqual(b['sourcePayloadBytesPreservedAfterClear'],41)
        self.assertEqual(b['status'],'source-receive-clear-overwrites-command');self.assertIsNone(b['providerCommandHexCandidate'])
        z=audit_mode50_request_packet(request_packet(42,requestAreaHex='2a'+'00'*42))
        self.assertFalse(z['sourcePayloadFullyRepresented'])

    def test_stack_guard_and_uint8_chunk_wrap_have_no_packet_candidate(self):
        self.assertFalse(audit_mode50_request_packet(request_packet(917))['sourceCommandOverwritesStackGuard'])
        r=audit_mode50_request_packet(request_packet(918))
        self.assertTrue(r['sourceCommandOverwritesStackGuard']);self.assertIsNone(r['sourceCommandAfterReceiveClearHex'])
        for n,represented,prefix in ((1530,0,'00FF03'),(1536,5,'01FF03')):
            r=audit_mode50_request_packet(request_packet(n));self.assertEqual(r['status'],'source-chunk-counter-wraps')
            self.assertEqual(r['sourcePayloadBytesRepresentedBeforeClear'],represented)
            self.assertTrue(r['sourceCommandBeforeReceiveClearHex'].startswith(prefix))
            self.assertIsNone(r['providerCommandHexCandidate'])

    def test_filter_channel_words_and_source_tail_are_explicit(self):
        a=audit_mode50_request_packet(request_packet(3))
        b=audit_mode50_request_packet(request_packet(3,filterWord=0x12abffff,channelWord=0xeeeeee78))
        self.assertEqual(a['providerCommandHexCandidate'],b['providerCommandHexCandidate'])
        c=audit_mode50_request_packet(request_packet(3,requestAreaHex='03010203deadbeef'))
        self.assertEqual(c['sourceTrailingBytesIgnored'],4);self.assertEqual(a['providerCommandHexCandidate'],c['providerCommandHexCandidate'])
        self.assertFalse(c['filterWordOriginQualified']);self.assertFalse(c['channelWordOriginQualified'])

    def test_missing_conflicting_or_malformed_source_never_pads(self):
        for changes in ({'requestAreaHex':'03'},{'requestAreaHex':'03 010203'},
                        {'filterWord':True},{'filterWord':-1},{'channelWord':1<<32},
                        {'extendedRequestByte':True},{'extendedRequestByte':256},
                        {'executionEnabled':True}):
            with self.subTest(changes=changes),self.assertRaises(ValueError):
                audit_mode50_request_packet(request_packet(3,**changes))
        r=request_packet(3);del r['filterWord']
        with self.assertRaises(ValueError):audit_mode50_request_packet(r)

    def test_all_uint16_lengths_match_original_umull_division_and_byte_count(self):
        for n in range(65536):
            product=(n+1)*0xaaaaaaab
            quotient=(product>>32)>>2
            original=quotient+(n+1-quotient*6!=0) if n>7 else 0
            r=audit_mode50_request_packing(n&255,n>>8)
            self.assertEqual(r['sourceChunkCountCandidate'],original)
            self.assertEqual(r['providerCommandBytesCandidate'],(original&255)*14+3 if n>7 else 17)

    def test_unknown_native_source_rejected_before_elf_or_execution(self):
        with patch('scripts.diagnostics.x431_native_binding._Elf') as elf:
            with self.assertRaisesRegex(ValueError,'source-unreviewed'):audit_mode50_request_source(b'unknown')
            elf.assert_not_called()

    def test_existing_cli_dispatches_packet_audit_without_transport(self):
        with tempfile.TemporaryDirectory() as temp:
            p=Path(temp)/'observation.json';p.write_text(json.dumps(request_packet(8)))
            out=io.StringIO()
            with redirect_stdout(out):self.assertEqual(main(['--kind','mode50-request-packet','--observation-json',str(p)]),0)
            result=json.loads(out.getvalue());self.assertEqual(result['sourcePayloadBytes'],8)
            self.assertFalse(result['vehicleIO']);self.assertFalse(result['executionEnabled'])


class Mode50Tests(unittest.TestCase):
    def test_mode50_offsets_remove_service_but_preserve_identifier(self):
        r = audit_mode50_response(observation())
        self.assertEqual(r['sourceWrittenPrefixHex'], 'f19e')
        self.assertEqual((r['nativeReturnCandidate'], r['sourceBodyOffset']), (2, 11))
        for k, v in VERSION_GUARDS.items():self.assertEqual(r[k], v)
        # A mode10 envelope lacks mode50's extra byte and cannot supply a body.
        r = audit_mode50_response(observation(internal('0001'+'00'*6+'0362f19e')))
        self.assertFalse(r['sourceOutputComplete'])

    def test_multiple_frame_length_uses_12_bit_count_and_next_offset(self):
        r = audit_mode50_response(observation(internal('0002'+'00'*7+'100562'+'aabbccdd')))
        self.assertEqual(r['sourceWrittenPrefixHex'], 'aabbccdd')
        self.assertEqual(r['sourceBodyOffset'], 12)
        self.assertEqual(r['nativeReturnCandidate'], 4)

    def test_headers_declared_extent_and_negative_code_must_be_fresh(self):
        for hx in ('0001', '0001'+'00'*7+'037f', '0001'+'00'*7+'0462f19e', '0000'+'00'*7+'0162'):
            self.assertFalse(audit_mode50_response(observation(internal(hx)))['sourceOutputComplete'])

    def test_request_chunks_have_six_data_bytes_and_a_byte_counter(self):
        for low, high, chunks, size in ((3, 0, 0, 17), (8, 0, 2, 31), (11, 0, 2, 31), (12, 0, 3, 45)):
            r = audit_mode50_request_packing(low, high)
            self.assertEqual((r['sourceChunkCountCandidate'],r['providerCommandBytesCandidate']), (chunks,size))
        self.assertTrue(audit_mode50_request_packing(255,255)['sourceChunkCounterWrapped'])
        for args in ((True,0),(-1,0),(1,256)):
            with self.assertRaises(ValueError):audit_mode50_request_packing(*args)

    def test_single_negative_never_claims_complete_receive_sequence(self):
        for code in (0x21,0x23,0x33,0x78):
            r = audit_mode50_response(observation(negative(code)))
            self.assertTrue(r['receiveAttemptsRequired']);self.assertFalse(r['sourceOutputComplete'])

    def test_busy_retries_stay_in_inner_loop_and_other_negatives_restart_outer(self):
        r = audit_mode50_receive_sequence(sequence(negative(0x21),negative(0x23),positive()))
        self.assertEqual([(s['outerAttemptIndex'],s['innerAttemptIndex']) for s in r['receives']],[(0,0),(0,1),(0,2)])
        self.assertEqual(r['sourceWrittenPrefixHex'],'f19e')
        r = audit_mode50_receive_sequence(sequence(negative(0x78),positive()))
        self.assertEqual(r['receives'][1]['outerAttemptIndex'],1)
        self.assertTrue(r['sourceReceiveSequenceComplete'])

    def test_pending_exhaustion_returns_request_count_without_fresh_vector(self):
        s = sequence(negative(0x78),negative(0x78));r = audit_mode50_receive_sequence(s)
        self.assertEqual((r['nativeReturnCandidate'],r['sourceWrittenPrefixHex']), (17,''))
        result = audit_version_map_origin({'requestHex':'22f19e','producerCallSite':0x19fa2,
            'communicationStatusByte':0x7e,'communication':{'wrapper':WRAPPERS[1],**s}})
        self.assertEqual(result['status'],'producer-count-exceeds-source-written-extent')
        self.assertIsNone(result['nativeMapVectorHexCandidate'])

    def test_ten_busy_receives_complete_but_outer_status_suppresses_count(self):
        s = sequence(*[negative(0x21)]*10);r = audit_mode50_receive_sequence(s)
        self.assertTrue(r['sourceReceiveSequenceComplete']);self.assertEqual(r['communicationStatusByteCandidate'],0x7f)
        self.assertEqual(r['receives'][5]['outerAttemptIndex'],1)
        result = audit_version_map_origin({'requestHex':'22f19e','producerCallSite':0x19fa2,
            'communicationStatusByte':0x7f,'communication':{'wrapper':WRAPPERS[1],**s}})
        self.assertFalse(result['mapUpdateCandidate'])

    def test_provider_failure_and_unfinished_sequences_retain_unknowns(self):
        failed = {'path':'direct','capacity':2048,'transportReturn':-60,'frameHex':''}
        self.assertEqual(audit_mode50_receive_sequence(sequence(failed,flag=0))['nativeReturnCandidate'],0)
        self.assertEqual(audit_mode50_receive_sequence(sequence(failed,failed))['nativeReturnCandidate'],-1)
        self.assertTrue(audit_mode50_receive_sequence(sequence(negative(0x21)))['receiveAttemptsRequired'])
        with self.assertRaisesRegex(ValueError,'after-terminal'):
            audit_mode50_receive_sequence(sequence(positive(),positive()))

    def test_version_origin_checks_site_request_length_and_outer_status(self):
        row={'requestHex':'22f19e','producerCallSite':0x19fa2,'communicationStatusByte':0x7e,
             'communication':{'wrapper':WRAPPERS[0],**observation()}}
        self.assertEqual(audit_version_map_origin(row)['nativeMapVectorHexCandidate'],'f19e')
        for changed in (row|{'producerCallSite':0x19d94},row|{'requestHex':'21aa'},row|{'communicationStatusByte':0}):
            with self.assertRaises(ValueError):audit_version_map_origin(changed)
        self.assertEqual(audit_communication_mode_context({'modeByte':0x50,'subtypeByte':5},WRAPPERS[0])['nativeResponseEntryCandidate'],0x11320)

    def test_source_bridge_requires_exact_request_flag_and_postprocessing(self):
        source=audit_communication_mode_context({'modeByte':0x50,'subtypeByte':5})
        source['modeContextCandidate']={'modeByte':0x50,'subtypeByte':5}
        command={'rawDescriptorComplete':True,'nativeDescriptorCompleteCandidate':True,'requestHexCandidate':'22f19e',
                 'requestFlagByteCandidate':1,'communicationSourceAudit':source,'enterSysCallerSourceAudit':audit_enter_sys_command_caller(0x50,5)}
        response={'requestHex':'22f19e','producerCallSite':0x19fa2,'communicationStatusByte':0x7e,
                  'communication':{'wrapper':WRAPPERS[1],**sequence(positive())}}
        with patch('scripts.diagnostics.x431_command_origin.audit_source_command_context',return_value=command):
            r=audit_source_command_response(b'',b'',{'commandSelection':{},'responseObservation':response})
            self.assertEqual(r['responseOriginAudit']['nativeMapVectorHexCandidate'],'f19e')
            command['nativeDescriptorCompleteCandidate']=False
            self.assertIsNone(audit_source_command_response(b'',b'',{'commandSelection':{},'responseObservation':response})['responseOriginAudit'])
            response['communication']['retryContext']['requestFlagByte']=0
            with self.assertRaisesRegex(ValueError,'retry-context-mismatch'):
                audit_source_command_response(b'',b'',{'commandSelection':{},'responseObservation':response})

    def test_mode30_source_bridge_binds_subtype_wrapper_and_producer_site(self):
        for subtype, communication in ((4, {'wrapper':'subtype4-0x10da4','aggregateHex':'010600000362f19e'}),
                (1, {'wrapper':'mode30-non-subtype4-0x11208','internal':
                     {'path':'direct','capacity':1024,'transportReturn':12,'frameHex':'00120000000003000062f19e'}})):
            source=audit_communication_mode_context({'modeByte':0x30,'subtypeByte':subtype})
            source['modeContextCandidate']={'modeByte':0x30,'subtypeByte':subtype}
            command={'rawDescriptorComplete':True,'nativeDescriptorCompleteCandidate':True,'requestHexCandidate':'22f19e',
                     'requestFlagByteCandidate':1,'communicationSourceAudit':source,'enterSysCallerSourceAudit':audit_enter_sys_command_caller(0x30,subtype)}
            response={'requestHex':'22f19e','producerCallSite':0x19d94,
                      'communicationStatusByte':0x7e if subtype==4 else 0,'communication':communication}
            with patch('scripts.diagnostics.x431_command_origin.audit_source_command_context',return_value=command):
                r=audit_source_command_response(b'',b'',{'commandSelection':{},'responseObservation':response})
                origin=r['responseOriginAudit']
                if subtype==4:self.assertEqual(origin['nativeMapVectorHexCandidate'],'f19e')
                else:self.assertEqual(origin['status'],'producer-count-exceeds-source-written-extent')
                response['producerCallSite']=0x19fa2
                with self.assertRaisesRegex(ValueError,'producer-site-mismatch'):
                    audit_source_command_response(b'',b'',{'commandSelection':{},'responseObservation':response})

    def test_cli_reads_only_explicit_observations_and_rejects_wrong_capacity(self):
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/'response.json';path.write_text(json.dumps(observation()));out=io.StringIO()
            with redirect_stdout(out):self.assertEqual(main(['--kind','mode50-response','--observation-json',str(path)]),0)
            self.assertEqual(json.loads(out.getvalue())['sourceWrittenPrefixHex'],'f19e')
        for value in (None, True, 1024):
            row=observation();row['internal']['capacity']=value
            with self.assertRaisesRegex(ValueError,'capacity-mismatch'):audit_mode50_response(row)


if __name__=='__main__':unittest.main()
