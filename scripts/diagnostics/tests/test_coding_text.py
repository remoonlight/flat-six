from copy import deepcopy
import itertools
import unittest
from scripts.diagnostics.coding_text import text_domain, preview_text_candidate
from scripts.diagnostics.coding_named_edits import preview_coding_field_changes
from scripts.diagnostics.tests.test_coding_named_edits import flag, edit
from scripts.diagnostics.tests.test_coding_numeric import linear


def field(at=900, length=4, offset=1, encoding='ISO-8859-2'):
    return dict(at=at,byteOffset=offset,bitOffset=0,labels=[dict(text='模型文本')],
        readSID=0x22,pid=0xF100,read_request_candidate_hex='22F100',
        formula=dict(text=f'IDENTICAL:Standar,BitMask=0,BaseDataType=A_ASCIISTRING,BitLength={length*8},Encoding={encoding},HighLow=1,Radix=10,DataType=A_UNICODE2STRING,Precision=null;'))


def variable(at=900, maximum=8, minimum=0, offset=1):
    record=field(at=at,offset=offset)
    record['formula']['text']=(f'IDENTICAL:MinMax,MinLength={minimum},BaseDataType=A_ASCIISTRING,MaxLength={maximum},'
        'BitMask=0,Encoding=ISO-8859-2,Termination=ZERO,Radix=10,DataType=A_UNICODE2STRING,Precision=null;')
    return record


class CodingTextTests(unittest.TestCase):
    def test_variable_source_bounds_reserve_terminator_in_complete_original_tail(self):
        record=variable();before=b'\xFFOLD!\x00XYZ'
        self.assertEqual(text_domain(record,before),dict(ok=True,length=7,minimumLength=0,termination='ZERO',encoding='ISO-8859-2',currentValue='OLD!'))
        for value in ('','N','ĄŁČŠ','ABCDEFG'):
            result=preview_text_candidate(record,before,value);after=bytes.fromhex(result['afterHex'])
            data=value.encode('iso8859-2')+b'\x00'
            self.assertEqual(after,before[:1]+data+before[1+len(data):])
            self.assertEqual(result['rawHex'],value.encode('iso8859-2').hex().upper())
            self.assertEqual(len(after),len(before))
        with self.assertRaises(ValueError):preview_text_candidate(record,before,'ABCDEFGH')

    def test_variable_empty_is_an_explicit_change_from_unknown_or_nonempty_current(self):
        for before in (b'\xFFOLD!\x00XYZ',b'\xFF'+b'\xA5'*8):
            draft=preview_coding_field_changes([variable()],{'22F100':before},[dict(fieldId='900',textValue='')])
            self.assertEqual(len(draft['changes']),1)
            self.assertEqual(draft['changes'][0]['textValue'],'')
            self.assertEqual(bytes.fromhex(draft['blocks'][0]['afterHex']),before[:1]+b'\x00'+before[2:])
        before=b'\xFF\x00'+b'\xA5'*7
        self.assertEqual(preview_coding_field_changes([variable()],{'22F100':before},[dict(fieldId='900',textValue='')])['changes'],[])

    def test_variable_does_not_infer_missing_capacity_or_end_of_pdu_layout(self):
        record=variable();before=b'\xFFOLD!\x00XYZ'
        for data in (before[:-1],before+b'\xAA'):
            self.assertFalse(text_domain(record,data)['ok'])
        for old,new in (('Termination=ZERO','Termination=END-OF-PDU'),('MinLength=0,',''),('MaxLength=8,','')):
            altered=deepcopy(record);altered['formula']['text']=altered['formula']['text'].replace(old,new)
            self.assertFalse(text_domain(altered,before)['ok'])

    def test_variable_minimum_encoding_controls_and_reserved_fields(self):
        record=variable(minimum=2);before=b'\xFFOLD!\x00XYZ'
        for value in ('','A','汉字','A\x00','A\n','A\u00ad'):
            with self.assertRaises(ValueError):preview_text_candidate(record,before,value)
        record['labels']=[dict(text='未定义')]
        with self.assertRaisesRegex(ValueError,'purpose-unqualified'):
            preview_coding_field_changes([record],{'22F100':before},[dict(fieldId='900',textValue='AB')])

    def test_variable_does_not_silently_change_other_fields_or_full_blocks(self):
        record=variable();other=field(at=901,length=2,offset=3)
        before={'22F100':b'\xFFOLD!\x00XYZ','22F101':b'\xFE'}
        with self.assertRaisesRegex(ValueError,'unselected-field-changed'):
            preview_coding_field_changes([record,other],before,[dict(fieldId='900',textValue='NEW!')])
        result=preview_coding_field_changes([record],before,[dict(fieldId='900',textValue='N')])
        self.assertEqual(result['blocks'][1]['afterHex'],'FE')
        self.assertEqual(result['blocks'][0]['beforeHex'],before['22F100'].hex().upper())
    def test_source_codec_exact_length_roundtrip_preserves_outside_bytes(self):
        record=field();original=b'\xA5OLD!\x00\xFF'
        for value in ('ABCD','ĄŁČŠ',' A  '):
            result=preview_text_candidate(record,original,value)
            self.assertEqual(bytes.fromhex(result['afterHex']),b'\xA5'+value.encode('iso8859-2')+b'\x00\xFF')
            self.assertEqual(result['current'],'OLD!')
            self.assertEqual(result['target'],value)
        self.assertEqual(text_domain(record,original),dict(ok=True,length=4,encoding='ISO-8859-2',currentValue='OLD!'))

    def test_no_padding_truncation_normalization_or_unsupported_characters(self):
        for value in ('','ABC','ABCDE','汉字AB','😀ABC','A\x00BC','A\nBC','A\x7fBC','A\u00a0BC','A\u00adBC',None):
            with self.subTest(value=value),self.assertRaises(ValueError):
                preview_text_candidate(field(),b'\xFFOLD!',value)

    def test_missing_encoding_variable_length_mask_and_offsets_remain_readonly(self):
        for old,new in (('ISO-8859-2','UTF-8'),('BitLength=32','MinLength=0,MaxLength=32'),('BitMask=0','BitMask=15'),('HighLow=1','HighLow=0')):
            record=field();record['formula']['text']=record['formula']['text'].replace(old,new)
            self.assertFalse(text_domain(record,b'\xFFOLD!')['ok'])
        for changes in (dict(bitOffset=1),dict(byteOffset=-1),dict(byteOffset=3)):
            record={**field(),**changes};self.assertFalse(text_domain(record,b'\xFFOLD!')['ok'])

    def test_unknown_current_not_shortened_or_guessed_and_full_original_retained(self):
        record=field()
        for data in (b'A\x00\x00\x00',b'\x01ABC',b'\xA0ABC'):
            original=b'\xFF'+data+b'\xFE'
            self.assertIsNone(text_domain(record,original)['currentValue'])
            result=preview_coding_field_changes([record],{'22F100':original},[dict(fieldId='900',textValue='NEW!')])
            self.assertEqual(result['blocks'][0]['beforeHex'],original.hex().upper())
            self.assertFalse(result['executionEnabled']);self.assertIsNone(result['writePayload'])

    def test_text_numeric_choice_mix_is_order_independent_and_preserves_other_blocks(self):
        number=linear(width=8,upper=255);number['byteOffset']=5
        records=[flag(1,0),field(),number];original={'22F100':b'\xA4OLD!\x03\xFF','22F101':b'\x5A'}
        saved=deepcopy(original)
        edits=[edit(1),dict(fieldId='900',textValue='NEW!'),dict(fieldId='100',physicalValue='10')]
        for order in itertools.permutations(edits):
            result=preview_coding_field_changes(records,original,list(order))
            self.assertEqual(bytes.fromhex(result['blocks'][0]['afterHex']),b'\xA5NEW!\x0A\xFF')
            self.assertEqual(result['blocks'][1]['afterHex'],'5A')
            self.assertEqual(len(result['changes']),3)
            self.assertEqual(original,saved)

    def test_reserved_and_unselected_overlaps_cannot_be_changed_indirectly(self):
        record=field();other=field(at=901,length=3,offset=2)
        for labels in ([dict(text='预留')],[dict(text='其他用途')]):
            other['labels']=labels
            with self.assertRaisesRegex(ValueError,'reserved-field-changed|unselected-field-changed'):
                preview_coding_field_changes([record,other],{'22F100':b'\xFFOLD!'},[dict(fieldId='900',textValue='NEW!')])
        record['labels']=[dict(text='未定义')]
        with self.assertRaisesRegex(ValueError,'purpose-unqualified'):
            preview_coding_field_changes([record],{'22F100':b'\xFFOLD!'},[dict(fieldId='900',textValue='NEW!')])

    def test_same_value_is_no_change_and_mixed_target_kinds_rejected(self):
        record=field();before={'22F100':b'\xFFOLD!'}
        self.assertEqual(preview_coding_field_changes([record],before,[dict(fieldId='900',textValue='OLD!')])['changes'],[])
        for target in (dict(fieldId='900',physicalValue='1234'),dict(fieldId='900',optionId='source-option-0'),dict(fieldId='900',textValue='NEW!',physicalValue='1234')):
            with self.assertRaises(ValueError):preview_coding_field_changes([record],before,[target])


if __name__=='__main__':unittest.main()
