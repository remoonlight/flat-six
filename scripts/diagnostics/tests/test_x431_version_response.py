import io,json,tempfile,unittest
from contextlib import redirect_stdout
from pathlib import Path
from scripts.diagnostics.x431_version_response import (
    audit_version_map_origin,audit_subtype4_frames,audit_subtype4_reassembly,audit_subtype4_receive_chain,
    audit_subtype4_request_packing,audit_subtype4_completion_receive,audit_communication_mode_context,
    audit_source_communication_context,
    audit_mode10_subtype3_short_response,
    audit_mode10_request_packing,audit_mode10_subtype3_response,
    audit_mode10_retry_context,audit_mode10_receive_sequence,
    audit_mode10_proex_request_packing,audit_mode10_proex_response,
    audit_mode10_proex_retry_context,audit_mode10_proex_receive_sequence,
    native_request_map_key,VERSION_GUARDS,main)


def wrapper(frame='00120000000003000062f190',count=12):
    return {'wrapper':'mode30-non-subtype4-0x11208','internal':
        {'path':'direct','capacity':1024,'transportReturn':count,'frameHex':frame}}


class VersionMapOriginTests(unittest.TestCase):
    def origin(self,site=0x19d94,communication=None):
        return audit_version_map_origin({'requestHex':'22f190','producerCallSite':site,
            'communicationStatusByte':0,
            'communication':wrapper() if communication is None else communication})

    def test_copy_extent_gap_is_not_padded_into_a_version_vector(self):
        r=self.origin()
        self.assertEqual(r['communicationReturnCandidate'],11)
        self.assertEqual(r['sourceWrittenBytes'],5)
        self.assertEqual(r['sourceWrittenPrefixHex'],'120362f190')
        self.assertEqual(r['initializationBytesCopiedCandidate'],6)
        self.assertIsNone(r['nativeMapVectorHexCandidate'])
        self.assertEqual(r['status'],'producer-count-exceeds-source-written-extent')
        for key,value in VERSION_GUARDS.items(): self.assertEqual(r[key],value)

    def test_forced_one_copies_output_start_not_declared_body_or_identifier(self):
        r=self.origin(0x1a136)
        self.assertEqual(r['producerCountCandidate'],1)
        self.assertEqual(r['nativeMapVectorHexCandidate'],'12')
        self.assertFalse(r['ecuVersionQualified'])
        self.assertIsNone(r['selectedVehicleVariant'])

    def test_zero_count_does_not_replace_previous_map_with_empty_fresh_data(self):
        r=self.origin(communication={'wrapper':'mode30-non-subtype4-0x11208',
            'internal':{'path':'cached','capacity':1024}})
        self.assertEqual(r['status'],'zero-count-leaves-previous-map-entry')
        self.assertFalse(r['mapUpdateCandidate'])
        self.assertIsNone(r['nativeMapVectorHexCandidate'])

    def test_forced_one_after_zero_still_has_no_fresh_output(self):
        r=self.origin(0x1a136,{'wrapper':'mode30-non-subtype4-0x11208',
            'internal':{'path':'cached','capacity':1024}})
        self.assertEqual(r['producerCountCandidate'],1)
        self.assertEqual(r['status'],'communication-output-unqualified')
        self.assertIsNone(r['nativeMapVectorHexCandidate'])

    def test_internal_positive_status_cannot_become_a_complete_map_vector(self):
        for site in (0x19d94,0x1a136):
            r=self.origin(site,wrapper('ff80',2))
            self.assertIsNone(r['nativeMapVectorHexCandidate'])
            self.assertFalse(r['freshReadProven'])

    def test_request_key_arithmetic_preserves_collisions_and_wrap(self):
        for hx,key in [('21aa',0xcb),('22f190',0xf1b2),('12345678',0x68ac),('ffff0001',0)]:
            self.assertEqual(native_request_map_key(bytes.fromhex(hx)),key)
        self.assertEqual(native_request_map_key(bytes.fromhex('2110')),native_request_map_key(bytes.fromhex('1a17')))
        for value in (b'',b'\x22',b'12345','22f190'):
            with self.assertRaises(ValueError):native_request_map_key(value)

    def test_outer_status_suppresses_count_and_leaves_old_map_entry(self):
        r=audit_version_map_origin({'requestHex':'22f190','producerCallSite':0x19d94,
            'communicationStatusByte':127,'communication':wrapper()})
        self.assertEqual(r['wrapperReturnCandidate'],11)
        self.assertEqual(r['communicationReturnCandidate'],0)
        self.assertEqual(r['status'],'zero-count-leaves-previous-map-entry')
        self.assertIsNone(r['nativeMapVectorHexCandidate'])

    def test_separate_outer_status_is_required_not_inferred_from_wrapper_byte(self):
        for status in (None,True,-1,256):
            with self.assertRaises(ValueError):
                audit_version_map_origin({'requestHex':'22f190','producerCallSite':0x19d94,
                    'communicationStatusByte':status,'communication':wrapper()})

    def test_unknown_site_and_input_overrides_are_rejected(self):
        for r in [[],{}, {'requestHex':'22f190','producerCallSite':1,'communication':wrapper(),'communicationStatusByte':0},
                  {'requestHex':'22','producerCallSite':0x19d94,'communication':wrapper(),'communicationStatusByte':0},
                  {'requestHex':'22f190','producerCallSite':0x19d94,'communication':wrapper(),'communicationStatusByte':0,'fresh':True}]:
            with self.assertRaises(ValueError):audit_version_map_origin(r)


class SubtypeFourFrameTests(unittest.TestCase):
    def test_selects_length_byte_and_body_from_variable_rows(self):
        for index,expected in [(1,'024142'),(2,'0358595a')]:
            r=audit_subtype4_frames('020241420358595a',index)
            self.assertEqual(r['selectedFrameHexCandidate'],expected)
            self.assertEqual(r['frames'][index-1]['nativeCopiedBytes'],len(bytes.fromhex(expected)))
            for key,value in VERSION_GUARDS.items():self.assertEqual(r[key],value)

    def test_out_of_declared_range_means_untouched_not_empty_fresh_data(self):
        r=audit_subtype4_frames('00',1)
        self.assertEqual(r['status'],'frame-index-not-declared')
        self.assertTrue(r['sourceDestinationUntouchedCandidate'])
        self.assertIsNone(r['selectedFrameHexCandidate'])

    def test_truncated_or_missing_later_row_rejects_a_complete_aggregate(self):
        for hx,label in [('020141','aggregate-frame-length-missing'),
                         ('0201410342','aggregate-frame-truncated')]:
            r=audit_subtype4_frames(hx,1)
            self.assertEqual(r['status'],label)
            self.assertIsNone(r['selectedFrameHexCandidate'])

    def test_zero_length_row_copies_only_length_and_is_a_terminator(self):
        r=audit_subtype4_frames('0100',1)
        self.assertEqual(r['selectedFrameHexCandidate'],'00')
        self.assertTrue(r['zeroLengthTerminator'])
        self.assertFalse(r['positiveEcuResponseQualified'])

    def test_maximum_byte_length_and_supplied_tail(self):
        r=audit_subtype4_frames('01ff'+'42'*255+'abcd',1)
        self.assertEqual(len(bytes.fromhex(r['selectedFrameHexCandidate'])),256)
        self.assertEqual(r['extraSuppliedBytes'],2)

    def test_invalid_index_and_unbounded_aggregate_are_rejected(self):
        for hx,index in [('0100',0),('0100',True),('0100',256),('',1),('00 00',1),('00'*1025,1)]:
            with self.assertRaises(ValueError):audit_subtype4_frames(hx,index)

    def test_cli_only_reads_explicit_offline_observation(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'frames.json';path.write_text(json.dumps({'aggregateHex':'010141','index':1}))
            out=io.StringIO()
            with redirect_stdout(out):self.assertEqual(main(['--observation-json',str(path),'--kind','subtype4']),0)
            self.assertEqual(json.loads(out.getvalue())['selectedFrameHexCandidate'],'0141')


def aggregate(*rows):
    return (bytes([len(rows)])+b''.join(bytes([len(row)])+row for row in rows)).hex()


class SubtypeFourReassemblyTests(unittest.TestCase):
    def origin(self,hx,site=0x19d94,status=0x7e):
        return audit_version_map_origin({'requestHex':'22f190','producerCallSite':site,
            'communicationStatusByte':status,
            'communication':{'wrapper':'subtype4-0x10da4','aggregateHex':hx}})

    def test_records_can_cross_fragment_boundaries_and_only_first_sid_is_removed(self):
        hx=aggregate(bytes.fromhex('a0000362'),bytes.fromhex('b0f19000024142'))
        r=audit_subtype4_reassembly(hx)
        self.assertEqual(r['concatenatedHexCandidate'],'000362f19000024142')
        self.assertEqual(r['sourceWrittenPrefixHex'],'f1904142')
        self.assertEqual(r['nativeReturnCandidate'],4)
        self.assertEqual([n['copiedBytes'] for n in r['records']],[2,2])
        self.assertEqual(r['communicationStatusByteCandidate'],0x7e)
        for key,value in VERSION_GUARDS.items():self.assertEqual(r[key],value)
        self.assertFalse(r['aggregateProviderQualified'])
        r=self.origin(hx)
        self.assertEqual(r['nativeMapVectorHexCandidate'],'f1904142')
        self.assertEqual(r['sourceWrittenBytes'],r['producerCountCandidate'])

    def test_first_row_filter_requires_both_conditions(self):
        valid=bytes.fromhex('000362f190')
        r=audit_subtype4_reassembly(aggregate(b'\x10\x80\xaa',b'\x00'+valid))
        self.assertTrue(r['fragments'][0]['firstRowFilterMatched'])
        self.assertEqual(r['sourceWrittenPrefixHex'],'f190')
        for row in (b'\x00\x80\xaa',b'\x10\x81\xaa'):
            r=audit_subtype4_reassembly(aggregate(row,b'\x00'+valid))
            self.assertFalse(r['fragments'][0]['firstRowFilterMatched'])
            self.assertFalse(r['sourceOutputComplete'])

    def test_same_filter_pattern_in_later_row_is_not_skipped(self):
        r=audit_subtype4_reassembly(aggregate(bytes.fromhex('00000362'),bytes.fromhex('1080aa')))
        self.assertEqual(r['sourceWrittenPrefixHex'],'80aa')
        self.assertFalse(r['fragments'][1]['firstRowFilterMatched'])

    def test_metadata_only_row_is_ignored_and_does_not_become_first_filter_row(self):
        r=audit_subtype4_reassembly(aggregate(b'\x44',bytes.fromhex('1000036280aa')))
        self.assertFalse(r['fragments'][0]['included'])
        self.assertEqual(r['sourceWrittenPrefixHex'],'80aa')

    def test_zero_row_terminates_before_later_declared_rows(self):
        r=audit_subtype4_reassembly(aggregate(bytes.fromhex('00000362f190'),b'',b'\x00\xff'))
        self.assertEqual(r['sourceWrittenPrefixHex'],'f190')
        self.assertEqual(r['zeroLengthTerminatorIndex'],2)
        self.assertEqual(r['ignoredDeclaredFrames'],1)
        self.assertEqual(len(r['fragments']),1)

    def test_truncated_declared_row_after_terminator_is_still_not_a_complete_input(self):
        r=audit_subtype4_reassembly('020004aa')
        self.assertEqual(r['status'],'aggregate-unqualified')
        self.assertIsNone(r['nativeReturnCandidate'])

    def test_high_header_nibble_is_not_length(self):
        r=audit_subtype4_reassembly(aggregate(bytes.fromhex('00f00362f190')))
        self.assertEqual(r['sourceWrittenPrefixHex'],'f190')
        self.assertEqual(r['records'][0]['headerHighNibble'],15)

    def test_twelve_bit_length_cannot_read_initialized_tail(self):
        r=audit_subtype4_reassembly(aggregate(bytes.fromhex('00010062aa')))
        self.assertEqual(r['declaredRecordBodyBytes'],256)
        self.assertEqual(r['availableRecordBodyBytes'],2)
        self.assertEqual(r['status'],'record-body-truncated')
        self.assertIsNone(r['sourceWrittenPrefixHex'])

    def test_trailing_header_or_body_does_not_produce_partial_vector(self):
        for stream,label in [('000262aa00','record-header-truncated'),
                             ('000262aa000242','record-body-truncated')]:
            hx=aggregate(b'\x00'+bytes.fromhex(stream))
            r=audit_subtype4_reassembly(hx)
            self.assertEqual(r['status'],label)
            self.assertIsNone(self.origin(hx)['nativeMapVectorHexCandidate'])

    def test_empty_bodies_and_first_one_byte_body_return_zero_without_fresh_map_update(self):
        for stream in ('00000000','0001620000'):
            hx=aggregate(b'\x00'+bytes.fromhex(stream))
            r=audit_subtype4_reassembly(hx)
            self.assertEqual(r['nativeReturnCandidate'],0)
            self.assertTrue(r['emptyOutputFlagCandidate'])
            origin=self.origin(hx)
            self.assertFalse(origin['mapUpdateCandidate'])
            self.assertIsNone(origin['nativeMapVectorHexCandidate'])

    def test_short_three_byte_case_returns_one_raw_header_byte_not_identifier(self):
        hx=aggregate(bytes.fromhex('00000162'))
        r=audit_subtype4_reassembly(hx)
        self.assertEqual(r['nativeReturnCandidate'],1)
        self.assertFalse(r['recordsParsed'])
        self.assertEqual(self.origin(hx)['nativeMapVectorHexCandidate'],'00')

    def test_fewer_than_three_source_bytes_leave_status_unobserved(self):
        for hx in ('00','0100',aggregate(bytes.fromhex('000001'))):
            r=audit_subtype4_reassembly(hx)
            self.assertEqual(r['nativeReturnCandidate'],-1)
            self.assertIsNone(r['communicationStatusByteCandidate'])
            self.assertIsNone(self.origin(hx)['nativeMapVectorHexCandidate'])

    def test_negative_first_body_status_suppresses_normal_caller_count(self):
        hx=aggregate(bytes.fromhex('0000037f2231'))
        r=self.origin(hx,status=0x7f)
        self.assertEqual(r['wrapperReturnCandidate'],2)
        self.assertEqual(r['communicationReturnCandidate'],0)
        self.assertFalse(r['mapUpdateCandidate'])
        with self.assertRaisesRegex(ValueError,'status-mismatch'):self.origin(hx)

    def test_forced_count_still_copies_first_flattened_byte(self):
        hx=aggregate(bytes.fromhex('00000362f190'))
        self.assertEqual(self.origin(hx,0x1a136)['nativeMapVectorHexCandidate'],'f1')

    def test_input_cannot_override_source_output(self):
        with self.assertRaises(ValueError):
            audit_version_map_origin({'requestHex':'22f190','producerCallSite':0x19d94,
                'communicationStatusByte':0x7e,'communication':{
                    'wrapper':'subtype4-0x10da4','aggregateHex':'00','outputHex':'f190'}})

    def test_cli_reassembly_requires_explicit_aggregate(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'frames.json'
            path.write_text(json.dumps({'aggregateHex':aggregate(bytes.fromhex('00000362f190'))}))
            out=io.StringIO()
            with redirect_stdout(out):
                self.assertEqual(main(['--observation-json',str(path),'--kind','subtype4-reassembly']),0)
            self.assertEqual(json.loads(out.getvalue())['sourceWrittenPrefixHex'],'f190')


def receive_chain(frame='00010600000362f190',count=9,completion=0):
    return {'aggregateReceive':{'commandBytes':15,'internal':{
        'path':'direct','capacity':1024,'transportReturn':count,'frameHex':frame}},
        'completionCallbackReturn':completion}


class SubtypeFourReceiveChainTests(unittest.TestCase):
    def test_only_exact_supplied_receive_extent_is_reassembled(self):
        r=audit_subtype4_receive_chain(receive_chain())
        self.assertEqual(r['providerReturnCandidate'],8)
        self.assertEqual(r['nativeReturnCandidate'],2)
        self.assertEqual(r['sourceWrittenPrefixHex'],'f190')
        self.assertTrue(r['sourceOutputComplete'])
        self.assertEqual(r['lowerResponse']['nativeCopyBytesCandidate'],8)
        self.assertFalse(r['providerRuntimeBindingQualified'])
        for key,value in VERSION_GUARDS.items():self.assertEqual(r[key],value)

    def test_positive_status_only_and_cached_return_do_not_provide_aggregate(self):
        for observation in (receive_chain('ff80',2),receive_chain('ff81',2),
            {'aggregateReceive':{'commandBytes':12,'internal':{'path':'cached','capacity':1024}},
             'completionCallbackReturn':0}):
            r=audit_subtype4_receive_chain(observation)
            self.assertFalse(r['sourceOutputComplete'])
            self.assertIsNone(r['reassembly'])
            self.assertIsNone(r['sourceWrittenPrefixHex'])

    def test_mismatched_count_wrap_and_capacity_truncation_block_reassembly(self):
        for frame,count in [('00010600000362f190',10),('00010600000362f190',65545),
                            ('00'+'01'*1025,1026)]:
            r=audit_subtype4_receive_chain(receive_chain(frame,count))
            self.assertFalse(r['sourceOutputComplete'])
            self.assertIsNone(r['reassembly'])

    def test_initial_negative_return_is_replaced_by_minus_one(self):
        for frame,count in [('ff02',2),('ff01',2),('',-60)]:
            r=audit_subtype4_receive_chain(receive_chain(frame,count,completion=-22))
            self.assertEqual(r['status'],'aggregate-provider-negative-return')
            self.assertEqual(r['nativeReturnCandidate'],-1)
            self.assertIsNone(r['completionGateCandidate'])

    def test_separate_negative_completion_stops_before_reassembly_and_preserves_return(self):
        r=audit_subtype4_receive_chain(receive_chain(completion=-22))
        self.assertEqual(r['status'],'subtype4-completion-callback-failed')
        self.assertEqual(r['nativeReturnCandidate'],-22)
        self.assertIsNone(r['reassembly'])

    def test_unqualified_initial_return_cannot_infer_completion_was_reached(self):
        r=audit_subtype4_receive_chain(receive_chain(count=10,completion=-22))
        self.assertIsNone(r['nativeReturnCandidate'])
        self.assertIsNone(r['reassembly'])
        self.assertIsNone(r['completionGateCandidate'])

    def test_aggregate_count_and_row_boundaries_still_checked_after_complete_receive(self):
        r=audit_subtype4_receive_chain(receive_chain('00020600000362f190',9))
        self.assertTrue(r['lowerResponse']['internalBufferComplete'])
        self.assertFalse(r['sourceOutputComplete'])
        self.assertEqual(r['reassembly']['frameAudit']['status'],'aggregate-frame-length-missing')

    def test_status_only_completion_return_may_pass_gate_but_does_not_qualify_it(self):
        r=audit_subtype4_receive_chain(receive_chain(completion=128))
        self.assertTrue(r['sourceOutputComplete'])
        self.assertTrue(r['completionGateCandidate'])
        self.assertFalse(r['completionCallbackSourceQualified'])
        self.assertFalse(r['positiveEcuResponseQualified'])

    def test_chain_origin_cannot_override_aggregate_or_outer_status(self):
        communication={'wrapper':'subtype4-read-chain-0x10da4',**receive_chain()}
        r=audit_version_map_origin({'requestHex':'22f190','producerCallSite':0x19d94,
            'communicationStatusByte':0x7e,'communication':communication})
        self.assertEqual(r['nativeMapVectorHexCandidate'],'f190')
        for communication,status in [(dict(communication,aggregateHex='00'),0x7e),
                                     (communication,0x7f)]:
            with self.assertRaises(ValueError):
                audit_version_map_origin({'requestHex':'22f190','producerCallSite':0x19d94,
                    'communicationStatusByte':status,'communication':communication})

    def test_inputs_require_fixed_capacity_observed_command_count_and_signed_completion(self):
        for count in (0,10,18,255,256,65537,True):
            observation=receive_chain();observation['aggregateReceive']['commandBytes']=count
            with self.assertRaises(ValueError):audit_subtype4_receive_chain(observation)
        for count in (None,True,-32769,32768):
            with self.assertRaises(ValueError):audit_subtype4_receive_chain(receive_chain(completion=count))
        observation=receive_chain();observation['aggregateReceive']['internal']['capacity']=256
        with self.assertRaises(ValueError):audit_subtype4_receive_chain(observation)

    def test_cli_chain_does_not_open_transport(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'receive.json';path.write_text(json.dumps(receive_chain()))
            out=io.StringIO()
            with redirect_stdout(out):
                self.assertEqual(main(['--observation-json',str(path),'--kind','subtype4-receive-chain']),0)
            self.assertEqual(json.loads(out.getvalue())['sourceWrittenPrefixHex'],'f190')


class SubtypeFourRequestPackingTests(unittest.TestCase):
    def test_version_request_lengths_match_distinct_final_command_lengths(self):
        for request,command in [('22f1',14),('22f190',15),('22f19001',16)]:
            communication={'wrapper':'subtype4-read-chain-0x10da4',**receive_chain()}
            communication['aggregateReceive']['commandBytes']=command
            r=audit_version_map_origin({'requestHex':request,'producerCallSite':0x19d94,
                'communicationStatusByte':0x7e,'communication':communication})
            packing=r['wrapperResponse']['requestPackingAudit']
            self.assertEqual(packing['fullChunkCountCandidate'],0)
            self.assertTrue(packing['sourceRequestExtentConsistent'])
            self.assertFalse(packing['actualModeSelected'])
            for key,value in VERSION_GUARDS.items():self.assertEqual(packing[key],value)

    def test_old_twelve_byte_context_cannot_support_three_byte_version_request(self):
        communication={'wrapper':'subtype4-read-chain-0x10da4',**receive_chain()}
        communication['aggregateReceive']['commandBytes']=12
        with self.assertRaisesRegex(ValueError,'request-command-length-mismatch'):
            audit_version_map_origin({'requestHex':'22f190','producerCallSite':0x19d94,
                'communicationStatusByte':0x7e,'communication':communication})

    def test_short_path_boundary_and_zero_length_use_final_packet(self):
        for length,command,payload in [(0,12,2),(5,17,7)]:
            r=audit_subtype4_request_packing(length)
            self.assertEqual(r['finalCommandBytesCandidate'],command)
            self.assertEqual(r['finalPayloadBytesCandidate'],payload)
            self.assertFalse(r['lengthRereadAfterChunkCallbacks'])

    def test_long_path_requires_separate_reread_even_when_length_might_be_unchanged(self):
        for length,chunks in [(6,1),(7,1),(255,36)]:
            r=audit_subtype4_request_packing(length)
            self.assertEqual(r['fullChunkCountCandidate'],chunks)
            self.assertEqual(r['status'],'length-reread-observation-required')
            self.assertIsNone(r['finalCommandBytesCandidate'])
            self.assertFalse(r['sourceLengthArithmeticComplete'])
            self.assertFalse(r['sourceRequestExtentConsistent'])

    def test_exact_seven_byte_multiple_still_has_final_one_byte_payload(self):
        r=audit_subtype4_request_packing(6,6)
        self.assertEqual(r['finalRemainderCandidate'],0)
        self.assertEqual(r['finalPayloadBytesCandidate'],1)
        self.assertEqual(r['finalCommandBytesCandidate'],11)
        self.assertEqual(r['sourceCopiedExtentBytesCandidate'],8)
        self.assertTrue(r['sourceRequestExtentConsistent'])

    def test_changed_reread_does_not_replace_original_chunk_source_offset(self):
        for initial,final,copied,consistent in [(6,0,9,False),(6,13,8,False),(6,7,9,True),(255,255,257,True)]:
            r=audit_subtype4_request_packing(initial,final)
            self.assertEqual(r['sourceCopiedExtentBytesCandidate'],copied)
            self.assertEqual(r['sourceRequestExtentConsistent'],consistent)
            self.assertFalse(r['requestLengthStableAcrossCallbacksProven'])
            self.assertFalse(r['preAggregateChunkReturnsQualified'])

    def test_length_inputs_cannot_wrap_or_add_reread_on_short_path(self):
        for length in (-1,256,True,None,3.0):
            with self.assertRaises(ValueError):audit_subtype4_request_packing(length)
        for length in (-1,256,True,3.0):
            with self.assertRaises(ValueError):audit_subtype4_request_packing(6,length)
        with self.assertRaises(ValueError):audit_subtype4_request_packing(3,3)

    def test_cli_request_lengths_do_not_expose_or_construct_packet_bytes(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'length.json';path.write_text(json.dumps({'requestBytes':3}))
            out=io.StringIO()
            with redirect_stdout(out):
                self.assertEqual(main(['--observation-json',str(path),'--kind','subtype4-request-packing']),0)
            r=json.loads(out.getvalue())
            self.assertEqual(r['finalCommandBytesCandidate'],15)
            self.assertNotIn('requestHex',r)
            self.assertNotIn('packetHex',r)
            self.assertFalse(r['executionEnabled'])


def completion_receive(frame='ff00',count=2):
    return {'internal':{'path':'direct','capacity':256,'transportReturn':count,'frameHex':frame}}


def audited_completion_chain(frame='ff00',count=2):
    observation=receive_chain()
    observation.pop('completionCallbackReturn')
    observation['completionReceive']=completion_receive(frame,count)
    return observation


class SubtypeFourCompletionReceiveTests(unittest.TestCase):
    def test_completion_payload_does_not_replace_or_extend_aggregate_version_bytes(self):
        r=audit_subtype4_receive_chain(audited_completion_chain('00deadbeef',5))
        self.assertEqual(r['completionCallbackReturn'],4)
        self.assertTrue(r['completionGateCandidate'])
        self.assertEqual(r['sourceWrittenPrefixHex'],'f190')
        self.assertNotIn('deadbeef',r['sourceWrittenPrefixHex'])
        self.assertFalse(r['completionReceiveAudit']['completionAcknowledgementQualified'])
        for key,value in VERSION_GUARDS.items():self.assertEqual(r[key],value)

    def test_positive_internal_status_without_completion_payload_still_passes_source_gate(self):
        for frame,returned in [('ff00',0),('ff80',128),('ff81',129)]:
            r=audit_subtype4_receive_chain(audited_completion_chain(frame,2))
            self.assertEqual(r['completionCallbackReturn'],returned)
            self.assertTrue(r['sourceOutputComplete'])
            self.assertFalse(r['completionReceiveAudit']['lowerResponse']['internalBufferComplete'])
            self.assertFalse(r['completionCallbackSourceQualified'])

    def test_negative_completion_status_stops_and_preserves_its_audited_return(self):
        for frame,returned in [('ff02',-70),('ff01',-14),('abcd',-60)]:
            r=audit_subtype4_receive_chain(audited_completion_chain(frame,2))
            self.assertEqual(r['nativeReturnCandidate'],returned)
            self.assertFalse(r['completionGateCandidate'])
            self.assertIsNone(r['reassembly'])

    def test_declared_return_cannot_disagree_with_independent_receive(self):
        observation=audited_completion_chain()
        observation['completionCallbackReturn']=-22
        with self.assertRaisesRegex(ValueError,'completion-return-observation-mismatch'):
            audit_subtype4_receive_chain(observation)
        observation['completionCallbackReturn']=0
        self.assertTrue(audit_subtype4_receive_chain(observation)['sourceOutputComplete'])

    def test_unknown_receive_return_cannot_be_replaced_by_declared_positive_return(self):
        for frame,count in [('ff00',3),('ff00',65538)]:
            observation=audited_completion_chain(frame,count)
            observation['completionCallbackReturn']=128
            r=audit_subtype4_receive_chain(observation)
            self.assertIsNone(r['completionCallbackReturn'])
            self.assertIsNone(r['completionGateCandidate'])
            self.assertIsNone(r['reassembly'])
            self.assertFalse(r['sourceOutputComplete'])

    def test_truncated_completion_copy_has_known_gate_but_no_qualified_completion_bytes(self):
        r=audit_subtype4_receive_chain(audited_completion_chain('00'+'01'*257,258))
        self.assertEqual(r['completionCallbackReturn'],257)
        self.assertTrue(r['sourceOutputComplete'])
        self.assertFalse(r['completionReceiveAudit']['lowerResponse']['internalBufferComplete'])
        self.assertFalse(r['completionReceiveAudit']['actualCleanupExecutedProven'])

    def test_cached_completion_zero_is_not_new_received_data(self):
        observation=audited_completion_chain()
        observation['completionReceive']={'internal':{'path':'cached','capacity':256}}
        r=audit_subtype4_receive_chain(observation)
        self.assertEqual(r['completionCallbackReturn'],0)
        self.assertTrue(r['completionGateCandidate'])
        self.assertFalse(r['completionReceiveAudit']['lowerResponse']['internalBufferComplete'])
        self.assertFalse(r['freshReadProven'])

    def test_initial_provider_failure_does_not_establish_callback_was_reached(self):
        observation=audited_completion_chain()
        observation['aggregateReceive']['internal'].update(frameHex='ff02',transportReturn=2)
        r=audit_subtype4_receive_chain(observation)
        self.assertEqual(r['nativeReturnCandidate'],-1)
        self.assertIsNone(r['completionGateCandidate'])

    def test_completion_receive_requires_fixed_caller_capacity_and_exact_fields(self):
        for value in ({},completion_receive()|{'requestHex':'55aa'},
                      {'internal':completion_receive()['internal']|{'capacity':1024}}):
            with self.assertRaises(ValueError):audit_subtype4_completion_receive(value)

    def test_version_map_accepts_audited_completion_and_checks_outer_status(self):
        communication={'wrapper':'subtype4-read-chain-0x10da4',**audited_completion_chain()}
        r=audit_version_map_origin({'requestHex':'22f190','producerCallSite':0x19d94,
            'communicationStatusByte':0x7e,'communication':communication})
        self.assertEqual(r['nativeMapVectorHexCandidate'],'f190')
        self.assertTrue(r['wrapperResponse']['completionReadContractAudited'])
        with self.assertRaisesRegex(ValueError,'communication-status-mismatch'):
            audit_version_map_origin({'requestHex':'22f190','producerCallSite':0x19d94,
                'communicationStatusByte':0x7f,'communication':communication})

    def test_cli_derives_signed_completion_return_without_transport(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'completion.json';path.write_text(json.dumps(completion_receive('ff02',2)))
            out=io.StringIO()
            with redirect_stdout(out):
                self.assertEqual(main(['--observation-json',str(path),'--kind','subtype4-completion-receive']),0)
            r=json.loads(out.getvalue())
            self.assertEqual(r['nativeReturnCandidate'],-70)
            self.assertFalse(r['executionEnabled'])


class CommunicationModeContextTests(unittest.TestCase):
    def test_full_configuration_extracts_separate_mode_and_subtype_without_vehicle_selection(self):
        configuration=bytearray(0x2358);configuration[1]=0x30;configuration[0x119]=4
        r=audit_communication_mode_context({'configurationHex':configuration.hex()})
        self.assertEqual((r['modeByte'],r['subtypeByte']),(0x30,4))
        self.assertEqual(r['nativeResponseEntryCandidate'],0x10da4)
        self.assertEqual(r['suppliedConfigurationBytes'],0x2358)
        self.assertFalse(r['actualModeSelected'])
        self.assertFalse(r['actualInitializationConfigurationOriginProven'])
        for key,value in VERSION_GUARDS.items():self.assertEqual(r[key],value)

    def test_nonfour_subtypes_use_different_layout_and_cannot_enter_four_chain(self):
        for subtype in (0,1,3,5,255):
            context={'modeByte':0x30,'subtypeByte':subtype}
            r=audit_communication_mode_context(context,'mode30-non-subtype4-0x11208')
            self.assertEqual(r['nativeResponseEntryCandidate'],0x11208)
            with self.assertRaisesRegex(ValueError,'mode-wrapper-mismatch'):
                audit_subtype4_receive_chain(audited_completion_chain()|{'modeContext':context})

    def test_other_modes_do_not_fall_back_to_mode30_even_with_subtype_four(self):
        for mode in (0,1,4,0x10,0x20,0x31,0x50,0x60,255):
            context={'modeByte':mode,'subtypeByte':4}
            r=audit_communication_mode_context(context)
            self.assertFalse(r['offlineResponseLayoutSupported'])
            self.assertIsNone(r['nativeResponseEntryCandidate'])
            with self.assertRaisesRegex(ValueError,'mode-wrapper-mismatch'):
                audit_subtype4_receive_chain(audited_completion_chain()|{'modeContext':context})

    def test_explicit_mode_guard_checks_version_map_before_response_parse(self):
        communication={'wrapper':'subtype4-read-chain-0x10da4',**audited_completion_chain(),
            'modeContext':{'modeByte':0x30,'subtypeByte':4}}
        observation={'requestHex':'22f190','producerCallSite':0x19d94,
            'communicationStatusByte':0x7e,'communication':communication}
        r=audit_version_map_origin(observation)
        self.assertEqual(r['nativeMapVectorHexCandidate'],'f190')
        self.assertIsNotNone(r['modeContextAudit'])
        communication['modeContext']['modeByte']=0x20
        with self.assertRaisesRegex(ValueError,'mode-wrapper-mismatch'):audit_version_map_origin(observation)

    def test_mode_context_cannot_route_four_response_into_nonfour_parser(self):
        observation={'requestHex':'22f190','producerCallSite':0x19d94,
            'communicationStatusByte':0,'communication':wrapper()|{
                'modeContext':{'modeByte':0x30,'subtypeByte':4}}}
        with self.assertRaisesRegex(ValueError,'mode-wrapper-mismatch'):audit_version_map_origin(observation)

    def test_missing_mode_context_stays_explicitly_unobserved(self):
        r=audit_subtype4_receive_chain(audited_completion_chain())
        self.assertIsNone(r['modeContextAudit'])
        self.assertFalse(r['actualNativeRuntimeQualified'])
        self.assertFalse(r['ecuVersionQualified'])

    def test_initializer_selector_or_runtime_claim_cannot_replace_required_fields(self):
        for context in ({'initializerSelector':0x30,'subtypeByte':4},
                        {'modeByte':0x30,'subtypeByte':4,'actualModeSelected':True},
                        {'modeByte':0x20,'subtypeByte':4,'initializerSelector':0x30}):
            with self.assertRaises(ValueError):audit_communication_mode_context(context)

    def test_field_bytes_do_not_wrap_and_configuration_extent_cannot_be_padded(self):
        for value in (-1,256,65584,True,None,48.0):
            with self.assertRaises(ValueError):audit_communication_mode_context({'modeByte':value,'subtypeByte':4})
            with self.assertRaises(ValueError):audit_communication_mode_context({'modeByte':0x30,'subtypeByte':value})
        for length in (0,0x11a,0x2357,0x2359):
            with self.assertRaises(ValueError):audit_communication_mode_context({'configurationHex':'00'*length})

    def test_cli_mode_candidate_never_constructs_or_executes_request(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'mode.json';path.write_text(json.dumps({'modeByte':0x30,'subtypeByte':4}))
            out=io.StringIO()
            with redirect_stdout(out):
                self.assertEqual(main(['--observation-json',str(path),'--kind','communication-mode']),0)
            r=json.loads(out.getvalue())
            self.assertEqual(r['nativeResponseEntryCandidate'],0x10da4)
            self.assertFalse(r['executionEnabled'])
            self.assertNotIn('requestHex',r)


def communication_record(mode=0x30, subtypes=(4,), *, params=((),), extra=0, trail=()):
    data=bytearray((mode,0,len(subtypes)))
    for index,subtype in enumerate(subtypes):
        data.extend(bytes(8));data.append(subtype)
        if subtype==5:data.extend(extra.to_bytes(4,'little'))
        values=params[index];data.append(len(values))
        for value in values:data.extend(value.to_bytes(4,'little'))
    data.append(len(trail))
    for value in trail:data.extend(value.to_bytes(4,'little'))
    data.extend(bytes(12))
    return data.hex()


class CommunicationDefinitionContextTests(unittest.TestCase):
    def context(self,record=None,index=0):
        return {'communicationRecordHex':communication_record() if record is None else record,'pairIndex':index}

    def test_single_pair_is_explicit_and_not_an_actual_vehicle_selection(self):
        r=audit_communication_mode_context(self.context())
        self.assertEqual((r['modeByte'],r['subtypeByte']),(0x30,4))
        self.assertEqual(r['nativeResponseEntryCandidate'],0x10da4)
        self.assertFalse(r['actualCommunicationPairSelected'])
        self.assertFalse(r['fullInitializationConfigurationReconstructed'])
        self.assertFalse(r['actualInitializationConfigurationOriginProven'])
        self.assertNotIn('requestHex',r)

    def test_explicit_second_pair_changes_layout_and_wrong_wrapper_is_rejected(self):
        record=communication_record(subtypes=(4,1),params=((),()))
        r=audit_communication_mode_context(self.context(record,1))
        self.assertEqual(r['nativeResponseEntryCandidate'],0x11208)
        self.assertEqual(r['recordSubtypeSourceOffset'],21)
        with self.assertRaisesRegex(ValueError,'mode-wrapper-mismatch'):
            audit_subtype4_receive_chain(audited_completion_chain()|{'modeContext':self.context(record,1)})

    def test_pair_mode_offset_accounts_for_mode_five_extra_and_variable_parameters(self):
        record=communication_record(subtypes=(5,4),params=((10,20,30),(40,)),extra=99,trail=(1,2))
        r=audit_communication_mode_context(self.context(record,1))
        self.assertEqual(r['recordSubtypeSourceOffset'],37)
        self.assertEqual(r['subtypeByte'],4)
        self.assertEqual(r['tokenPairModeOffset'],0x650)

    def test_record_with_unknown_family_layout_does_not_become_mode_thirty(self):
        for mode in (1,2,4,0x20,0x60,0xff):
            with self.assertRaisesRegex(ValueError,'layout-unreviewed'):
                audit_communication_mode_context(self.context(communication_record(mode)))
        r=audit_communication_mode_context(self.context(communication_record(0x10)))
        self.assertFalse(r['offlineResponseLayoutSupported'])
        self.assertEqual(r['modeByte'],0x10)

    def test_missing_pair_selection_and_invalid_indices_are_not_defaulted(self):
        with self.assertRaises(ValueError):audit_communication_mode_context({'communicationRecordHex':communication_record()})
        for index in (-1,1,8,True,0.0,None):
            with self.assertRaises(ValueError):audit_communication_mode_context(self.context(index=index))

    def test_truncation_at_every_extent_and_extra_bytes_are_rejected(self):
        record=communication_record(subtypes=(5,4),params=((1,2),(3,)),extra=4,trail=(5,))
        for end in range(0,len(record),2):
            with self.assertRaises(ValueError):audit_communication_mode_context(self.context(record[:end],1))
        with self.assertRaisesRegex(ValueError,'trailing-bytes'):
            audit_communication_mode_context(self.context(record+'00',1))

    def test_record_context_can_guard_existing_version_chain_without_new_transport(self):
        communication={'wrapper':'subtype4-read-chain-0x10da4',**audited_completion_chain(),
            'modeContext':self.context()}
        r=audit_version_map_origin({'requestHex':'22f190','producerCallSite':0x19d94,
            'communicationStatusByte':0x7e,'communication':communication})
        self.assertEqual(r['nativeMapVectorHexCandidate'],'f190')
        for key,value in VERSION_GUARDS.items():self.assertEqual(r[key],value)

    def test_source_hash_is_required_before_binding_a_selection(self):
        with self.assertRaisesRegex(ValueError,'logical-source-unreviewed'):
            audit_source_communication_context(bytes.fromhex(communication_record()),
                {'familyId':13,'systemKey':0xd04,'linkIndex':0,'pairIndex':0})

    def test_cli_source_kind_requires_source_and_other_kinds_do_not_ignore_source_argument(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'context.json';path.write_text(json.dumps(self.context()))
            for args in (['--kind','source-communication'],['--kind','communication-mode','--logical-sys-data',str(path)]):
                with self.assertRaises(SystemExit) as error:main(['--observation-json',str(path),*args])
                self.assertEqual(error.exception.code,2)


def mode10_receive(frame, *, length=3, extended=0):
    return {'requestBytes':length,'extendedRequestByte':extended,'internal':{
        'path':'direct','capacity':0x1014,'transportReturn':len(frame)+1,'frameHex':(b'\0'+frame).hex()}}


def mode10_single(service=0x62,body=b'\xf1\x90'):
    return b'\0\x01'+bytes(6)+bytes((len(body)+1,service))+body


class Mode10ShortResponseTests(unittest.TestCase):
    def test_single_frame_drops_only_service_and_keeps_identifier_in_map_vector(self):
        r=audit_mode10_subtype3_short_response(mode10_receive(mode10_single()))
        self.assertEqual(r['sourceWrittenPrefixHex'],'f190')
        self.assertEqual(r['nativeReturnCandidate'],2)
        self.assertEqual(r['communicationStatusByteCandidate'],0x7e)
        self.assertFalse(r['ecuIdentityQualified'])
        self.assertFalse(r['freshReadProven'])

    def test_multiframe_uses_twelve_bit_length_and_distinct_body_start(self):
        frame=b'\0\x02'+bytes(6)+b'\x10\x04\x62\xf1\x90\xaa'
        r=audit_mode10_subtype3_short_response(mode10_receive(frame))
        self.assertEqual(r['sourceWrittenPrefixHex'],'f190aa')
        self.assertEqual((r['nativeReturnCandidate'],r['sourceBodyOffset']),(3,11))

    def test_multiframe_zero_length_preserves_negative_native_count(self):
        r=audit_mode10_subtype3_short_response(mode10_receive(b'\0\x02'+bytes(6)+bytes(2)))
        self.assertEqual(r['nativeReturnCandidate'],-1)
        self.assertEqual(r['sourceWrittenPrefixHex'],'')

    def test_single_zero_or_one_length_sets_no_data_flag_and_zero_count(self):
        for length in (0,1):
            r=audit_mode10_subtype3_short_response(mode10_receive(b'\0\x01'+bytes(6)+bytes((length,0x62))))
            self.assertEqual(r['nativeReturnCandidate'],0)
            self.assertEqual(r['sourceNoDataFlagCandidate'],1)
            self.assertEqual(r['sourceWrittenPrefixHex'],'')

    def test_wait_retry_and_other_negative_codes_require_further_observations(self):
        for code in (0x21,0x23,0x78,0x10):
            r=audit_mode10_subtype3_short_response(mode10_receive(mode10_single(0x7f,bytes((0x22,code)))))
            self.assertTrue(r['receiveAttemptsRequired'])
            self.assertIsNone(r['nativeReturnCandidate'])
            self.assertFalse(r['sourceOutputComplete'])
            self.assertEqual(r['communicationStatusByteCandidate'],0x7e if code==0x78 else 0x7f)

    def test_code_thirty_three_copies_body_but_common_status_suppresses_count(self):
        r=audit_mode10_subtype3_short_response(mode10_receive(mode10_single(0x7f,b'\x22\x33')))
        self.assertEqual(r['nativeReturnCandidate'],0)
        self.assertEqual(r['sourceWrittenPrefixHex'],'2233')
        self.assertEqual(r['communicationStatusByteCandidate'],0x7f)

    def test_status_only_and_cached_return_do_not_supply_zero_initialized_header(self):
        for lower in ({'path':'direct','capacity':0x1014,'transportReturn':2,'frameHex':'ff80'},
                      {'path':'cached','capacity':0x1014}):
            r=audit_mode10_subtype3_short_response({'requestBytes':3,'extendedRequestByte':0,'internal':lower})
            self.assertIsNone(r['nativeReturnCandidate'])
            self.assertFalse(r['sourceOutputComplete'])

    def test_truncated_header_negative_code_and_declared_body_stay_incomplete(self):
        for frame in (bytes(9),mode10_single()[:-1],mode10_single(0x7f,b'\x22\x33')[:-1],
                      b'\0\x02'+bytes(6)+b'\x1f\xff\x62\xaa'):
            r=audit_mode10_subtype3_short_response(mode10_receive(frame))
            self.assertFalse(r['sourceOutputComplete'])
            self.assertIsNone(r['nativeReturnCandidate'])

    def test_long_extended_boolean_and_wrong_capacity_cannot_use_short_provider(self):
        for length,extended in ((8,0),(3,1),(True,0),(3,False)):
            with self.assertRaises(ValueError):audit_mode10_subtype3_short_response(mode10_receive(mode10_single(),length=length,extended=extended))
        r=mode10_receive(mode10_single());r['internal']['capacity']=1024
        with self.assertRaises(ValueError):audit_mode10_subtype3_short_response(r)

    def test_zero_frame_count_is_not_qualified_as_a_received_frame(self):
        r=audit_mode10_subtype3_short_response(mode10_receive(bytes(8)+b'\x03\x62\xf1\x90'))
        self.assertFalse(r['sourceOutputComplete'])
        self.assertEqual(r['status'],'mode10-zero-frame-layout-unqualified')

    def test_version_chain_checks_length_mode_and_status_before_map_update(self):
        communication={'wrapper':'mode10-subtype3-short-0xf8d4',**mode10_receive(mode10_single()),
            'modeContext':{'modeByte':0x10,'subtypeByte':3}}
        value={'requestHex':'22f190','producerCallSite':0x19fa2,'communicationStatusByte':0x7e,'communication':communication}
        r=audit_version_map_origin(value)
        self.assertEqual(r['nativeMapVectorHexCandidate'],'f190')
        for bad in ({'requestBytes':2},{'modeContext':{'modeByte':0x30,'subtypeByte':3}}):
            with self.assertRaises(ValueError):audit_version_map_origin(value|{'communication':communication|bad})
        with self.assertRaises(ValueError):audit_version_map_origin(value|{'communicationStatusByte':0x7f})
        with self.assertRaisesRegex(ValueError,'producer-site-mismatch'):
            audit_version_map_origin(value|{'producerCallSite':0x19d94})

    def test_cli_mode10_receive_retains_all_vehicle_guards(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'receive.json';path.write_text(json.dumps(mode10_receive(mode10_single())))
            out=io.StringIO()
            with redirect_stdout(out):self.assertEqual(main(['--kind','mode10-short-response','--observation-json',str(path)]),0)
            r=json.loads(out.getvalue())
            for key,value in VERSION_GUARDS.items():self.assertEqual(r[key],value)


class Mode10RequestPackingTests(unittest.TestCase):
    def test_short_and_long_paths_use_distinct_providers(self):
        short=audit_mode10_request_packing(7,0);long=audit_mode10_request_packing(8,0)
        self.assertEqual((short['providerExportCandidate'],short['providerCommandBytesCandidate']),('CommSubFunc0001',17))
        self.assertEqual((long['providerExportCandidate'],long['providerCommandBytesCandidate']),('CommSubFunc0005',31))
        self.assertEqual(long['sourceFragmentCountCandidate'],2)
        self.assertEqual(long['sourceUniquePayloadReadExtentCandidate'],9)

    def test_counter_boundary_switches_record_packing_without_truncating_count(self):
        before=audit_mode10_request_packing(248,6);after=audit_mode10_request_packing(249,6)
        self.assertEqual((before['sourceFragmentCountCandidate'],after['sourceFragmentCountCandidate']),(255,256))
        self.assertEqual((before['providerCommandBytesCandidate'],after['providerCommandBytesCandidate']),(3573,2057))
        self.assertIsNone(before['sourceFragmentCounterHighByteWrittenCandidate'])
        self.assertEqual(after['sourceFragmentCounterHighByteWrittenCandidate'],1)
        self.assertEqual(after['sourceFragmentCounterLowByteCandidate'],0)
        self.assertTrue(after['sourceFrameControlCallsExpected'])
        self.assertFalse(after['frameControlSideEffectsQualified'])

    def test_high_length_sixteen_has_two_source_mutations_without_claiming_execution(self):
        r=audit_mode10_request_packing(255,16)
        self.assertEqual(r['effectiveRequestBytesCandidate'],4095)
        self.assertEqual([x['value'] for x in r['requestLengthByteMutationCandidates']],[15,0])
        self.assertFalse(r['actualRequestLengthMutationProven'])
        self.assertEqual((r['sourceFragmentCountCandidate'],r['providerCommandBytesCandidate']),(586,4697))
        self.assertTrue(r['packedCommandWithinTemplateCopyCandidate'])

    def test_eight_byte_temporary_copy_overlaps_without_extending_request_read(self):
        r=audit_mode10_request_packing(14,0)
        self.assertEqual(r['sourceLargestTemporaryPayloadCopyCandidate'],8)
        self.assertEqual(r['sourcePayloadCopySumCandidate'],16)
        self.assertEqual(r['sourceUniquePayloadReadExtentCandidate'],15)
        self.assertTrue(r['sourcePayloadExtentMatchesLengthCandidate'])

    def test_ignored_extended_byte_is_reported_and_cannot_qualify_receive(self):
        r=audit_mode10_request_packing(4,17)
        self.assertEqual(r['effectiveRequestBytesCandidate'],4)
        self.assertFalse(r['extendedLengthLayoutQualified'])
        self.assertFalse(r['shortRequestPath'])
        with self.assertRaisesRegex(ValueError,'extended-length-layout'):
            audit_mode10_subtype3_response(mode10_receive(mode10_single(),length=4,extended=17))

    def test_long_provider_reuses_body_parse_without_reclassifying_pending_as_final(self):
        for length,extended in ((8,0),(248,6),(249,6),(255,15)):
            r=audit_mode10_subtype3_response(mode10_receive(mode10_single(),length=length,extended=extended))
            self.assertEqual(r['providerExportCandidate'],'CommSubFunc0005')
            self.assertEqual(r['sourceWrittenPrefixHex'],'f190')
            self.assertFalse(r['providerRuntimeBindingQualified'])
        pending=audit_mode10_subtype3_response(mode10_receive(mode10_single(0x7f,b'\x22\x78'),length=8))
        self.assertTrue(pending['receiveAttemptsRequired'])
        self.assertIsNone(pending['nativeReturnCandidate'])

    def test_general_wrapper_cannot_use_low_byte_to_claim_matching_version_request(self):
        communication={'wrapper':'mode10-subtype3-0xf8d4',**mode10_receive(mode10_single(),extended=1),
            'modeContext':{'modeByte':0x10,'subtypeByte':3}}
        value={'requestHex':'22f190','producerCallSite':0x19fa2,'communicationStatusByte':0x7e,'communication':communication}
        with self.assertRaisesRegex(ValueError,'request-length-mismatch'):audit_version_map_origin(value)
        communication['extendedRequestByte']=0
        self.assertEqual(audit_version_map_origin(value)['nativeMapVectorHexCandidate'],'f190')

    def test_request_bytes_are_unsigned_exact_values_and_never_wrapped(self):
        for value in (-1,256,True,1.0,None):
            with self.assertRaises(ValueError):audit_mode10_request_packing(value,0)
            with self.assertRaises(ValueError):audit_mode10_request_packing(3,value)

    def test_cli_length_audit_produces_no_packet_and_no_transport_claim(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'length.json';path.write_text(json.dumps({'requestBytes':249,'extendedRequestByte':6}))
            out=io.StringIO()
            with redirect_stdout(out):self.assertEqual(main(['--kind','mode10-request-packing','--observation-json',str(path)]),0)
            r=json.loads(out.getvalue())
            self.assertEqual(r['sourceFragmentCountCandidate'],256)
            for key,value in VERSION_GUARDS.items():self.assertEqual(r[key],value)
            self.assertNotIn('requestHex',r)


class Mode10ReceiveSequenceTests(unittest.TestCase):
    @staticmethod
    def context(**changes):
        return dict(filterIdWord=0,sendOnlyAndOne=0,requestFlagByte=1,
                    initialCommunicationStatusByte=0x7e)|changes

    def sequence(self,frames, *, length=3,extended=0,context=None):
        return dict(requestBytes=length,extendedRequestByte=extended,
            retryContext=self.context() if context is None else context,
            receives=[mode10_receive(frame)['internal'] for frame in frames])

    @staticmethod
    def negative(code):return mode10_single(0x7f,bytes((0x22,code)))

    @staticmethod
    def failure():return dict(path='direct',capacity=4116,transportReturn=2,frameHex='ff02')

    def test_filter_precedence_and_only_exact_one_send_flag_override(self):
        for word,send,limit in ((0,0,2),(0xe000ffff,0,1),(0xe0010000,0,2),
                               (0xe0010000,1,1),(0,2,2),(0xffffffff,1,1)):
            r=audit_mode10_retry_context(self.context(filterIdWord=word,sendOnlyAndOne=send))
            self.assertEqual(r['outerAttemptLimitCandidate'],limit)
            self.assertFalse(r['runtimeGlobalsQualified'])

    def test_context_requires_explicit_initial_status_and_unsigned_exact_values(self):
        missing=self.context();del missing['initialCommunicationStatusByte']
        with self.assertRaises(ValueError):audit_mode10_retry_context(missing)
        for key in self.context():
            for value in (-1,True,1.0,None,0x100000000):
                with self.assertRaises(ValueError):audit_mode10_retry_context(self.context(**{key:value}))

    def test_filter_context_can_derive_only_from_explicit_complete_source_pair(self):
        record='100301e0ea000020f800000305a70310000200200001002000030020000a00200000000020000100400e00000000'
        value=self.context();del value['filterIdWord']
        value.update(communicationRecordHex=record,pairIndex=0)
        r=audit_mode10_retry_context(value)
        self.assertEqual(r['filterIdWord'],0xeae0f820)
        self.assertEqual(r['outerAttemptLimitCandidate'],2)
        self.assertEqual(r['filterWordSource'],'source-common-record-selected-pair')
        self.assertFalse(r['actualFilterSetterCallProven'])
        for change in ({'pairIndex':1},{'pairIndex':True},{'communicationRecordHex':record+'00'},
                       {'communicationRecordHex':'30'+record[2:]},{'filterIdWord':0}):
            with self.assertRaises(ValueError):audit_mode10_retry_context(value|change)

    def test_retry_source_pair_must_match_mode_source_pair_when_both_are_supplied(self):
        record='100301e0ea000020f800000305a70310000200200001002000030020000a00200000000020000100400e00000000'
        context=self.context();del context['filterIdWord']
        context.update(communicationRecordHex=record,pairIndex=0)
        communication={'wrapper':'mode10-subtype3-sequence-0xf8d4',
            **self.sequence([mode10_single()],context=context),
            'modeContext':{'communicationRecordHex':record,'pairIndex':0}}
        value=dict(requestHex='22f190',producerCallSite=0x19fa2,communicationStatusByte=0x7e,communication=communication)
        self.assertEqual(audit_version_map_origin(value)['nativeMapVectorHexCandidate'],'f190')
        changed='100301e1'+record[8:]
        communication['modeContext']={'communicationRecordHex':changed,'pairIndex':0}
        with self.assertRaisesRegex(ValueError,'source-pair-mismatch'):audit_version_map_origin(value)

    def test_pending_then_positive_completes_without_using_pending_body(self):
        r=audit_mode10_receive_sequence(self.sequence([self.negative(0x78),mode10_single()]))
        self.assertEqual((r['nativeReturnCandidate'],r['sourceWrittenPrefixHex']),(2,'f190'))
        self.assertTrue(r['sourceReceiveSequenceComplete'])
        self.assertEqual([a['outerAttemptOrdinal'] for a in r['observations']],[1,2])
        self.assertEqual(r['sourceBusyDelayCallsCandidate'],0)

    def test_four_busy_then_positive_stays_in_same_outer_attempt(self):
        frames=[self.negative(c) for c in (0x21,0x23,0x21,0x23)]+[mode10_single()]
        r=audit_mode10_receive_sequence(self.sequence(frames))
        self.assertEqual(r['sourceBusyDelayCallsCandidate'],4)
        self.assertEqual([a['outerAttemptOrdinal'] for a in r['observations']],[1]*5)
        self.assertEqual(r['sourceWrittenPrefixHex'],'f190')

    def test_fifth_busy_advances_outer_and_resets_busy_counter(self):
        r=audit_mode10_receive_sequence(self.sequence([self.negative(0x21)]*5+[mode10_single()]))
        self.assertEqual(r['observations'][-1]['outerAttemptOrdinal'],2)
        self.assertEqual(r['observations'][-1]['busyReceivesBefore'],0)
        self.assertEqual(r['sourceBusyDelayCallsCandidate'],5)

    def test_ten_busy_terminate_with_suppressed_count_and_no_body(self):
        r=audit_mode10_receive_sequence(self.sequence([self.negative(0x23)]*10,length=8))
        self.assertTrue(r['sourceReceiveSequenceComplete'])
        self.assertEqual((r['nativeReturnCandidate'],r['communicationStatusByteCandidate']),(0,0x7f))
        self.assertFalse(r['sourceOutputComplete'])
        self.assertIsNone(r['sourceWrittenPrefixHex'])
        self.assertEqual(r['sourceBusyDelayCallsCandidate'],10)

    def test_long_pending_terminal_positive_count_is_not_a_written_body(self):
        for length,extended,fp,command in ((8,0,31,31),(249,6,3587,2057)):
            r=audit_mode10_receive_sequence(self.sequence([self.negative(0x78)]*2,length=length,extended=extended))
            self.assertEqual(r['nativeReturnCandidate'],fp)
            self.assertEqual(r['requestPackingAudit']['providerCommandBytesCandidate'],command)
            self.assertTrue(r['sourceReceiveSequenceComplete'])
            self.assertFalse(r['sourceOutputComplete'])

    def test_failure_flag_zero_returns_zero_without_requiring_another_receive(self):
        value=self.sequence([],length=8,context=self.context(requestFlagByte=0));value['receives']=[self.failure()]
        r=audit_mode10_receive_sequence(value)
        self.assertEqual(r['nativeReturnCandidate'],0)
        self.assertTrue(r['sourceReceiveSequenceComplete'])
        self.assertEqual(r['outerAttemptsAdvancedCandidate'],0)

    def test_failure_one_outer_retains_initial_count_but_two_outer_returns_minus_one(self):
        for send,count,expected in ((1,1,31),(0,2,-1)):
            value=self.sequence([],length=8,context=self.context(sendOnlyAndOne=send))
            value['receives']=[self.failure()]*count;r=audit_mode10_receive_sequence(value)
            self.assertEqual(r['nativeReturnCandidate'],expected)
            self.assertFalse(r['sourceOutputComplete'])
        value['retryContext']['initialCommunicationStatusByte']=0x7f
        self.assertEqual(audit_mode10_receive_sequence(value)['nativeReturnCandidate'],0)

    def test_failure_after_busy_preserves_status_for_terminal_gate(self):
        value=self.sequence([self.negative(0x21)],length=8,context=self.context(sendOnlyAndOne=1))
        value['receives'].append(self.failure());r=audit_mode10_receive_sequence(value)
        self.assertEqual(r['sourceInitialReturnCandidate'],31)
        self.assertEqual(r['nativeReturnCandidate'],0)
        self.assertEqual(r['communicationStatusByteCandidate'],0x7f)

    def test_missing_busy_or_pending_observations_leave_final_status_and_count_unknown(self):
        for frames in ([],[self.negative(0x21)]*4,[self.negative(0x78)]):
            r=audit_mode10_receive_sequence(self.sequence(frames))
            self.assertFalse(r['sourceReceiveSequenceComplete'])
            self.assertTrue(r['additionalObservationRequired'])
            self.assertIsNone(r['nativeReturnCandidate'])
            self.assertIsNone(r['communicationStatusByteCandidate'])

    def test_incomplete_intermediate_negative_status_cannot_suppress_final_map_count(self):
        communication={'wrapper':'mode10-subtype3-sequence-0xf8d4',**self.sequence([self.negative(0x21)])}
        r=audit_version_map_origin(dict(requestHex='22f190',producerCallSite=0x19fa2,
              communicationStatusByte=0x7f,communication=communication))
        self.assertIsNone(r['communicationReturnCandidate'])
        self.assertIsNone(r['producerCountCandidate'])
        self.assertIsNone(r['mapUpdateCandidate'])
        # The retained one-receive interface also lacks the subsequent return.
        communication={'wrapper':'mode10-subtype3-0xf8d4',**mode10_receive(self.negative(0x21))}
        r=audit_version_map_origin(dict(requestHex='22f190',producerCallSite=0x19fa2,
              communicationStatusByte=0x7f,communication=communication))
        self.assertIsNone(r['communicationReturnCandidate'])
        self.assertIsNone(r['mapUpdateCandidate'])

    def test_final_success_integrates_map_and_checks_final_status(self):
        communication={'wrapper':'mode10-subtype3-sequence-0xf8d4',
            **self.sequence([self.negative(0x78),mode10_single()]),'modeContext':{'modeByte':0x10,'subtypeByte':3}}
        value=dict(requestHex='22f190',producerCallSite=0x19fa2,communicationStatusByte=0x7e,communication=communication)
        self.assertEqual(audit_version_map_origin(value)['nativeMapVectorHexCandidate'],'f190')
        with self.assertRaisesRegex(ValueError,'status-mismatch'):audit_version_map_origin(value|{'communicationStatusByte':0x7f})
        with self.assertRaisesRegex(ValueError,'producer-site-mismatch'):audit_version_map_origin(value|{'producerCallSite':0x1a136})

    def test_denial_terminal_preserves_copied_bytes_and_zero_return(self):
        r=audit_mode10_receive_sequence(self.sequence([self.negative(0x33)]))
        self.assertEqual((r['nativeReturnCandidate'],r['sourceWrittenPrefixHex']),(0,'2233'))
        self.assertTrue(r['sourceOutputComplete'])

    def test_unqualified_receive_cannot_be_repaired_by_later_positive_frame(self):
        good=mode10_receive(mode10_single())['internal']
        bads=[dict(path='cached',capacity=4116),dict(path='direct',capacity=4116,transportReturn=2,frameHex='ff80'),
              mode10_receive(mode10_single()[:-1])['internal'],
              dict(path='direct',capacity=4116,transportReturn=65538,frameHex='ff02')]
        for bad in bads:
            value=self.sequence([]);value['receives']=[bad,good];r=audit_mode10_receive_sequence(value)
            self.assertFalse(r['sourceReceiveSequenceComplete']);self.assertFalse(r['sourceOutputComplete'])
            self.assertIsNone(r['nativeReturnCandidate']);self.assertEqual(r['unprocessedObservations'],1)

    def test_extra_observations_after_terminal_and_excess_tape_are_rejected(self):
        for frames,context in (([mode10_single()]*2,self.context()),
                               ([self.negative(0x78)]*2,self.context(sendOnlyAndOne=1)),
                               ([self.negative(0x21)]*11,self.context())):
            with self.assertRaises(ValueError):audit_mode10_receive_sequence(self.sequence(frames,context=context))
        value=self.sequence([]);value['receives']={}
        with self.assertRaises(ValueError):audit_mode10_receive_sequence(value)

    def test_cli_sequence_is_offline_and_preserves_all_vehicle_guards(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'sequence.json';path.write_text(json.dumps(self.sequence([self.negative(0x78),mode10_single()])))
            out=io.StringIO()
            with redirect_stdout(out):self.assertEqual(main(['--kind','mode10-receive-sequence','--observation-json',str(path)]),0)
            r=json.loads(out.getvalue())
            for key,value in VERSION_GUARDS.items():self.assertEqual(r[key],value)
            self.assertFalse(r['actualReceiveSequenceQualified'])


class Mode10ProExTests(unittest.TestCase):
    def test_mode_fields_alone_do_not_choose_between_pro_and_proex(self):
        r=audit_communication_mode_context(dict(modeByte=0x10,subtypeByte=3))
        self.assertIsNone(r['nativeResponseEntryCandidate'])
        self.assertEqual(r['nativeResponseEntryCandidates'],[0xf8d4,0x11678])
        self.assertEqual(audit_communication_mode_context(dict(modeByte=0x10,subtypeByte=3),
            'mode10-subtype3-0xf8d4')['nativeResponseEntryCandidate'],0xf8d4)
        self.assertFalse(r['actualModeSelected'])

    @staticmethod
    def context(**changes):
        return dict(filterIdWord=0,requestFlagByte=1,initialCommunicationStatusByte=0x7e)|changes

    def sequence(self,frames, *, length=3,extended=0,context=None):
        return dict(requestBytes=length,extendedRequestByte=extended,
            retryContext=self.context() if context is None else context,
            receives=[mode10_receive(frame)['internal'] for frame in frames])

    @staticmethod
    def negative(code):return mode10_single(0x7f,bytes((0x22,code)))

    def test_filter_limit_has_no_single_send_override(self):
        self.assertEqual(audit_mode10_proex_retry_context(self.context())['outerAttemptLimitCandidate'],2)
        r=audit_mode10_proex_retry_context(self.context(filterIdWord=0xe000abcd))
        self.assertEqual(r['outerAttemptLimitCandidate'],1)
        self.assertIsNone(r['sourceSendOnlyGlobal'])
        self.assertFalse(r['sourceSingleSendGlobalUsedByEntry'])
        with self.assertRaises(ValueError):audit_mode10_proex_retry_context(self.context(sendOnlyAndOne=1))

    def test_source_record_context_retains_proex_entry(self):
        record='100301e0ea000020f800000305a70310000200200001002000030020000a00200000000020000100400e00000000'
        context={k:v for k,v in self.context().items() if k!='filterIdWord'}|dict(communicationRecordHex=record,pairIndex=0)
        r=audit_mode10_proex_retry_context(context)
        self.assertEqual(r['filterIdWord'],0xeae0f820)
        self.assertEqual(r['communicationPairSourceAudit']['nativeResponseEntryCandidate'],0x11678)
        self.assertFalse(r['actualFilterSetterCallProven'])

    def test_denial_is_not_the_pro_immediate_parse_case(self):
        observation=mode10_receive(self.negative(0x33))
        pro=audit_mode10_subtype3_response(observation);ex=audit_mode10_proex_response(observation)
        self.assertTrue(pro['sourceOutputComplete'])
        self.assertTrue(ex['receiveAttemptsRequired'])
        self.assertFalse(ex['sourceOutputComplete'])
        self.assertIsNone(ex['nativeReturnCandidate'])
        self.assertEqual(ex['providerCallSite'],0x119c4)

    def test_denials_exhaust_attempts_without_copying_body(self):
        r=audit_mode10_proex_receive_sequence(self.sequence([self.negative(0x33)]*2))
        self.assertTrue(r['sourceReceiveSequenceComplete'])
        self.assertEqual(r['nativeReturnCandidate'],0)
        self.assertEqual(r['communicationStatusByteCandidate'],0x7f)
        self.assertFalse(r['sourceOutputComplete'])
        self.assertIsNone(r['sourceWrittenPrefixHex'])

    def test_denial_followed_by_success_resets_status_and_copies_only_success(self):
        r=audit_mode10_proex_receive_sequence(self.sequence([self.negative(0x33),mode10_single()]))
        self.assertEqual((r['nativeReturnCandidate'],r['communicationStatusByteCandidate']),(2,0x7e))
        self.assertEqual(r['sourceWrittenPrefixHex'],'f190')
        self.assertEqual(r['outerAttemptsAdvancedCandidate'],1)

    def test_five_busy_observations_advance_outer_and_reset_busy_counter(self):
        r=audit_mode10_proex_receive_sequence(self.sequence([self.negative(0x21)]*5+[mode10_single()]))
        self.assertEqual(r['sourceBusyDelayCallsCandidate'],5)
        self.assertEqual(r['observations'][-1]['outerAttemptOrdinal'],2)
        self.assertEqual(r['observations'][-1]['busyReceivesBefore'],0)
        self.assertEqual(r['sourceWrittenPrefixHex'],'f190')
        self.assertFalse(r['actualReceiveSequenceQualified'])

    def test_pending_with_one_outer_attempt_can_leave_positive_long_count_without_body(self):
        r=audit_mode10_proex_receive_sequence(self.sequence([self.negative(0x78)],length=8,
            context=self.context(filterIdWord=0xe0000000)))
        self.assertEqual(r['nativeReturnCandidate'],31)
        self.assertFalse(r['sourceOutputComplete'])
        self.assertTrue(r['sourceReceiveSequenceComplete'])
        self.assertEqual(r['requestPackingAudit']['providerCallSiteCandidate'],0x119c4)

    def test_lower_failure_exit_preserves_flag_and_initial_status_rules(self):
        failure=Mode10ReceiveSequenceTests.failure()
        value=self.sequence([],context=self.context(requestFlagByte=0));value['receives']=[failure]
        r=audit_mode10_proex_receive_sequence(value)
        self.assertEqual(r['nativeReturnCandidate'],0)
        value=self.sequence([]);value['receives']=[failure]*2
        self.assertEqual(audit_mode10_proex_receive_sequence(value)['nativeReturnCandidate'],-1)

    def test_cached_and_truncated_observations_cannot_be_repaired_by_later_success(self):
        good=mode10_receive(mode10_single())['internal']
        for bad in (dict(path='cached',capacity=4116),
                    dict(path='direct',capacity=4116,transportReturn=2,frameHex='ff80'),
                    mode10_receive(mode10_single()[:-1])['internal']):
            value=self.sequence([]);value['receives']=[bad,good]
            r=audit_mode10_proex_receive_sequence(value)
            self.assertFalse(r['sourceReceiveSequenceComplete'])
            self.assertIsNone(r['nativeReturnCandidate'])
            self.assertEqual(r['unprocessedObservations'],1)

    def test_positive_body_does_not_bind_proex_to_a_known_pro_version_producer(self):
        communication={'wrapper':'mode10-subtype3-proex-0x11678',**mode10_receive(mode10_single())}
        r=audit_mode10_proex_response({k:v for k,v in communication.items() if k!='wrapper'})
        self.assertEqual(r['sourceWrittenPrefixHex'],'f190')
        with self.assertRaisesRegex(ValueError,'proex-producer-binding-unqualified'):audit_version_map_origin(dict(requestHex='22f190',producerCallSite=0x19fa2,
            communicationStatusByte=0x7e,communication=communication))

    def test_extra_observations_and_unknown_context_fields_are_rejected(self):
        for frames in ([mode10_single()]*2,[self.negative(0x21)]*11):
            with self.assertRaises(ValueError):audit_mode10_proex_receive_sequence(self.sequence(frames))
        with self.assertRaises(ValueError):audit_mode10_proex_receive_sequence(self.sequence([])|{'sendOnlyAndOne':1})

    def test_cli_distinguishes_proex_and_retains_all_vehicle_guards(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'ex.json';path.write_text(json.dumps(self.sequence([self.negative(0x33),mode10_single()])))
            out=io.StringIO()
            with redirect_stdout(out):self.assertEqual(main(['--kind','mode10-proex-receive-sequence','--observation-json',str(path)]),0)
            r=json.loads(out.getvalue())
            self.assertEqual(r['wrapperContext'],'mode10-subtype3-proex-sequence-0x11678')
            for key,value in VERSION_GUARDS.items():self.assertEqual(r[key],value)


if __name__=='__main__':unittest.main()
