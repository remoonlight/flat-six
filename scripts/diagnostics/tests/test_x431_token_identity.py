import io,json,struct,tempfile,unittest
from contextlib import redirect_stdout,redirect_stderr
from pathlib import Path
from scripts.diagnostics.x431_version_response import (
    audit_token_identity_response,audit_communication_pair_context,VERSION_GUARDS,main)


def record(word,commands):
    return (bytes((0x10,3,1))+struct.pack('<IIBB',0xfc00,0xfd00,3,len(commands))+
            b''.join(struct.pack('<I',c) for c in commands)+b'\0'+struct.pack('<3I',0x200005,word,0)).hex()


def observation(word=0x0e010001,command=0x200006,count=8,response='aa123400fe998877'):
    commands=(0x200007,0x200006,0x200007) if word!=0x0e010002 else (0x1003a7,0x200002,0x200001,0x20000a)
    return dict(communicationRecordHex=record(word,commands),pairIndex=0,commandId=command,
                producerCount=count,sourceWrittenResponseHex=response)


class TokenIdentityTests(unittest.TestCase):
    def test_dme_raw_four_bytes_preserve_zero_and_byte_order(self):
        r=audit_token_identity_response(observation())
        self.assertEqual(r['fieldHexCandidate'],'123400fe')
        self.assertIsNone(r['fieldTextCandidate'])
        self.assertEqual((r['fieldSourceOffset'],r['fieldSourceBytes'],r['tokenFieldOffset']),(1,4,0x1f8))
        self.assertEqual(r['slAtNormalReturnGateCandidate'],-1)

    def test_second_dme_source_context_has_distinct_six_byte_range(self):
        r=audit_token_identity_response(observation(0x0e010002,0x200001,response='aabb123400ff66ee'))
        self.assertEqual(r['fieldHexCandidate'],'123400ff66ee')
        self.assertEqual((r['fieldSourceOffset'],r['fieldSourceBytes']),(2,6))
        self.assertEqual(r['tokenLayoutWordCandidate'],6)

    def test_pdk_formats_two_bytes_as_four_uppercase_digits_plus_terminator(self):
        r=audit_token_identity_response(observation(0x0e020001,response='aa12ab'))
        self.assertEqual(r['fieldTextCandidate'],'12AB')
        self.assertEqual(bytes.fromhex(r['fieldHexCandidate']),b'12AB\0')
        self.assertEqual(r['tokenDeclaredFieldBytesCandidate'],4)
        self.assertEqual(r['fieldStoredBytesCandidate'],5)

    def test_noop_does_not_report_old_slot_as_new_field(self):
        for word,commands in [(0x0e010001,(0x200007,)),(0x0e020001,(0x200007,)),
                              (0x0e010002,(0x1003a7,0x200002,0x20000a))]:
            for c in commands:
                r=audit_token_identity_response(observation(word,c,response=''))
                self.assertEqual(r['status'],'command-leaves-previous-field-untouched')
                self.assertFalse(r['nativeFieldWriteCandidate'])
                self.assertIsNone(r['fieldHexCandidate'])
                self.assertEqual(r['tokenFlagF8Candidate'],1)
                self.assertFalse(r['nativeReturnProvesSuccess'])

    def test_every_missing_byte_is_rejected_without_padding(self):
        for word,c,end in ((0x0e010001,0x200006,5),(0x0e010002,0x200001,8),(0x0e020001,0x200006,3)):
            for size in range(end):
                r=audit_token_identity_response(observation(word,c,response=bytes(range(size)).hex()))
                self.assertEqual(r['status'],'source-written-response-too-short')
                self.assertFalse(r['sourceWrittenRangeCovered'])
                self.assertIsNone(r['fieldHexCandidate'])

    def test_zero_count_does_not_qualify_even_complete_explicit_bytes(self):
        r=audit_token_identity_response(observation(count=0))
        self.assertTrue(r['sourceWrittenRangeCovered'])
        self.assertTrue(r['nativeFieldWriteCandidate'])
        self.assertEqual(r['status'],'zero-count-field-not-qualified')
        self.assertIsNone(r['fieldHexCandidate'])

    def test_tail_read_and_map_count_are_separate(self):
        r=audit_token_identity_response(observation(count=1))
        self.assertEqual(r['fieldHexCandidate'],'123400fe')
        self.assertTrue(r['nativeReadExceedsProducerCountCandidate'])
        self.assertFalse(r['fieldIsDsnVersionVector'])
        self.assertFalse(r['freshReadProven'])

    def test_unknown_source_contexts_stay_unreviewed(self):
        for word in (0,0x0e990001,0x80000001):
            r=audit_token_identity_response(observation(word))
            self.assertEqual(r['status'],'context-parser-not-reviewed')
            self.assertIsNone(r['fieldHexCandidate'])
            self.assertIsNone(r['nativeFieldWriteCandidate'])

    def common(self,word,command,response,count=257):
        return audit_token_identity_response(dict(communicationRecordHex=record(word,(command,)),pairIndex=0,
            commandId=command,producerCount=count,sourceWrittenResponseHex=response.hex()))

    def test_two_flags_update_separate_slots_without_inventing_first_slot(self):
        for byte,third,fourth in ((0,'**','**'),(4,'ja','**'),(64,'**','ja'),(68,'ja','ja')):
            r=self.common(0x0e040001,0x200003,bytes((0,0,byte)))
            self.assertIsNone(r['fieldHexCandidate'])
            self.assertEqual([f['fieldTextCandidate'] for f in r['fields']],[None,None,third,fourth])
            self.assertEqual([f['nativeFieldWriteCandidate'] for f in r['fields']],[False,False,True,True])
            self.assertEqual(r['tokenLayoutWordCandidate'],0x02020706)

    def test_flags_keep_padding_terminators_and_source_literal_differences(self):
        r=self.common(0x0e400001,0x200003,b'\0\0\x04')
        self.assertEqual(bytes.fromhex(r['fields'][2]['fieldHexCandidate']),b'ja  \0')
        self.assertEqual(bytes.fromhex(r['fields'][3]['fieldHexCandidate']),b'****\0')
        r=self.common(0x0e070001,0x200003,b'\0\0\x04')
        self.assertEqual(r['fields'][2]['fieldTextCandidate'],'****')
        r=self.common(0x0e070001,0x200003,b'\0\0\0')
        self.assertEqual(r['fields'][2]['fieldTextCandidate'],'nein')

    def test_one_missing_flag_byte_refuses_both_updated_fields(self):
        r=self.common(0x0e360001,0x200003,b'\0\0')
        self.assertEqual(r['status'],'source-written-response-too-short')
        for f in r['fields'][1:3]:
            self.assertIsNone(f['fieldHexCandidate'])
            self.assertFalse(f['sourceWrittenRangeCovered'])

    def test_full_255_byte_copy_keeps_nuls_and_does_not_become_text(self):
        payload=bytes(range(255))
        r=self.common(0x0e080001,0x200002,b'\x12\x34'+payload)
        self.assertIsNone(r['fieldHexCandidate'])
        self.assertEqual(bytes.fromhex(r['fields'][1]['fieldHexCandidate']),payload)
        self.assertIsNone(r['fields'][1]['fieldTextCandidate'])
        self.assertEqual(r['fields'][1]['fieldStoredBytesCandidate'],255)
        self.assertEqual(r['requiredSourceEndExclusive'],257)

    def test_early_nul_does_not_shorten_native_fixed_copy(self):
        for n in (0,1,2,7,254,255,256):
            r=self.common(0x0e080001,0x200002,bytes(n))
            self.assertEqual(r['status'],'source-written-response-too-short')
            self.assertIsNone(r['fields'][1]['fieldHexCandidate'])

    def test_special_prefix_normalizes_only_last_three_source_bytes(self):
        for source in (b'001xyz',b'001\0\xff\x7f',b'001000'):
            r=self.common(0x0e270001,0x200001,b'\x12\x34'+source)
            self.assertEqual(bytes.fromhex(r['fieldHexCandidate']),b'001000')
            self.assertTrue(r['fields'][0]['sourceNormalizationAppliedCandidate'])
        for source in (b'000xyz',b'002xyz',b'101xyz',b'\x00\x001xyz'):
            r=self.common(0x0e270001,0x200001,b'\x12\x34'+source)
            self.assertEqual(bytes.fromhex(r['fieldHexCandidate']),source)
            self.assertFalse(r['fields'][0]['sourceNormalizationAppliedCandidate'])

    def test_alternate_command_domain_and_field_slot_are_preserved(self):
        r=self.common(0x0e4c0001,0x1a73,b'\0\0\x12\x34\x56\x78')
        self.assertIsNone(r['fieldHexCandidate'])
        self.assertEqual(r['fields'][1]['fieldHexCandidate'],'12345678')
        self.assertEqual(r['fields'][1]['tokenFieldOffset'],0x2f8)

    def test_fourth_slot_does_not_overwrite_first_slot_report(self):
        r=self.common(0x0e4a0001,0x200001,b'\0\0ABC')
        self.assertIsNone(r['fieldHexCandidate'])
        self.assertEqual(r['fields'][3]['fieldHexCandidate'],'414243')
        self.assertFalse(r['fields'][0]['nativeFieldWriteCandidate'])

    def test_extra_source_command_cannot_be_assumed_to_be_a_noop(self):
        r=self.common(0x0e1d0001,0x200009,bytes(257))
        self.assertEqual(r['status'],'source-command-parser-not-reviewed')
        self.assertFalse(r['sourceContextParserReviewed'])
        self.assertEqual(r['fields'],[])

    def test_shared_two_byte_formatter_keeps_byte_order(self):
        r=self.common(0x0e440001,0x200006,b'\0\xab\x12')
        self.assertEqual(r['fieldTextCandidate'],'AB12')
        self.assertEqual(bytes.fromhex(r['fieldHexCandidate']),b'AB12\0')

    def test_zero_count_and_noop_are_checked_per_field(self):
        r=self.common(0x0e040001,0x200003,b'\0\0\xff',count=0)
        self.assertEqual(r['status'],'zero-count-field-not-qualified')
        self.assertTrue(all(f['fieldHexCandidate'] is None for f in r['fields']))
        r=self.common(0x0e040001,0x1003a7,b'',count=0)
        self.assertEqual(r['status'],'command-leaves-previous-field-untouched')
        self.assertTrue(all(not f['nativeFieldWriteCandidate'] for f in r['fields']))

    def test_zero_context_has_no_assumed_layout_or_field_value(self):
        r=self.common(0,0x200001,bytes(257))
        self.assertTrue(r['zeroDispatchWord'])
        self.assertIsNone(r['tokenLayoutWordCandidate'])
        self.assertEqual(r['fields'],[])

    def test_complete_trailer_and_ordered_duplicate_commands_are_preserved(self):
        hx=record(0x0e010001,(0x200007,0x200006,0x200007))
        r=audit_communication_pair_context(hx,0)
        self.assertEqual(r['sourceCommunicationTrailerWords'],[0x200005,0x0e010001,0])
        self.assertEqual(r['sourceSelectedPairCommandIds'],[0x200007,0x200006,0x200007])
        self.assertEqual(r['sourceTrailerTokenOffsets'],[0x750,0x644,0x648])
        self.assertFalse(r['tokenDispatchWordEqualsDsnFormulaIdProven'])

    def test_command_must_belong_to_the_selected_source_pair(self):
        p=observation();p['commandId']=0x200001
        with self.assertRaisesRegex(ValueError,'command-not-in-selected-source-pair'):audit_token_identity_response(p)

    def test_exact_input_types_fields_and_bounds(self):
        changes=[{'producerCount':True},{'producerCount':-1},{'producerCount':4098},{'producerCount':1.0},
                 {'commandId':True},{'commandId':0x100000000},{'pairIndex':True},{'pairIndex':1},
                 {'sourceWrittenResponseHex':'a'},{'sourceWrittenResponseHex':'xx'},
                 {'sourceWrittenResponseHex':'00'*4098},{'sourceWrittenResponseHex':b'123'},
                 {'communicationRecordHex':observation()['communicationRecordHex']+'00'},
                 {'ecuIdentityQualified':True}]
        for change in changes:
            with self.subTest(change=change),self.assertRaises(ValueError):audit_token_identity_response(observation()|change)
        with self.assertRaises(ValueError):audit_token_identity_response({})

    def test_cli_and_bad_logical_source_remain_offline(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/'observation.json';p.write_text(json.dumps(observation()),encoding='utf-8')
            out=io.StringIO()
            with redirect_stdout(out):self.assertEqual(main(['--kind','token-identity-response','--observation-json',str(p)]),0)
            self.assertEqual(json.loads(out.getvalue())['fieldHexCandidate'],'123400fe')
            logical=Path(tmp)/'SYS_DATA';logical.write_bytes(b'bad')
            explicit={'sourceSelection':dict(familyId=13,systemKey=0xd01,linkIndex=0,pairIndex=0),
                      'commandId':0x200006,'producerCount':8,'sourceWrittenResponseHex':'00'*8}
            p.write_text(json.dumps(explicit),encoding='utf-8')
            with redirect_stderr(io.StringIO()),self.assertRaises(SystemExit) as e:
                main(['--kind','token-identity-response','--observation-json',str(p),'--logical-sys-data',str(logical)])
            self.assertEqual(e.exception.code,2)

    def test_candidates_never_qualify_vehicle_or_enable_execution(self):
        for word,c in ((0x0e010001,0x200006),(0x0e010002,0x200001),(0x0e020001,0x200006)):
            r=audit_token_identity_response(observation(word,c))
            for key,value in VERSION_GUARDS.items():self.assertEqual(r[key],value)
            self.assertFalse(r['sourceBytesRuntimeOriginQualified'])
            self.assertFalse(r['nativeReturnProvesSuccess'])
            self.assertFalse(r['fieldIsDsnVersionVector'])


if __name__=='__main__':unittest.main()
