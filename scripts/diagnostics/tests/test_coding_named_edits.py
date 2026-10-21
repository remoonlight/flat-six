from copy import deepcopy
import hashlib
import itertools
import json
import unittest
from unittest.mock import patch

from scripts.diagnostics.coding_named_edits import named_coding_choices, preview_named_coding_changes
from scripts.diagnostics.decoder_reference import reference_patch
from scripts.diagnostics.x431_values import preview_coding
from scripts.diagnostics.tests.test_coding_fields import enum
from scripts.diagnostics.tests.test_x431_compound_text import compound
from scripts.diagnostics.x431_values import decode_record


def flag(at, shift):
    record = enum(1, shift)
    record['at'] = at
    record['formula']['text'] = record['formula']['text'].replace('[0,1]', '[0]').replace('[2,3]', '[1]')
    return record


def edit(at, option=1):
    return dict(fieldId=str(at), optionId=f'source-option-{option}')


class NamedCodingEditTests(unittest.TestCase):
    def test_empty_leaf_uses_the_last_nonempty_source_name_without_changing_value_decoding(self):
        record=flag(1,0)
        record.pop('name',None)
        record['labels']=[{'text':'菜单'},{'text':'来源字段'},{'text':''}]
        original=b'\xA4\x5A'
        before=decode_record(record,original)
        self.assertEqual(before['displayName'],'')
        choices=named_coding_choices(record,original)
        self.assertEqual(choices['current']['displayName'],'来源字段')
        self.assertEqual({k:v for k,v in choices['current'].items() if k!='displayName'},
                         {k:v for k,v in before.items() if k!='displayName'})
        draft=preview_named_coding_changes([record],{'22F100':original},[edit(1)])
        self.assertEqual(draft['changes'][0]['name'],'来源字段')
        self.assertEqual(draft['blocks'][0]['afterHex'],'A55A')
        self.assertFalse(draft['executionEnabled']);self.assertIsNone(draft['writePayload'])
        self.assertEqual(decode_record(record,original),before)

    def test_empty_names_placeholders_and_reserved_ancestors_remain_unqualified(self):
        for labels,name in [([{'text':''},{'text':' '}],None),
                            ([{'text':'来源字段'},{'text':''}],'SubIndexNum=0'),
                            ([{'text':'来源字段'},{'text':''}],''),
                            ([{'text':'预留'},{'text':''}],None)]:
            record=flag(1,0);record['labels']=labels
            if name is None:record.pop('name',None)
            else:record['name']=name
            with self.subTest(labels=labels,name=name),self.assertRaises(ValueError):
                named_coding_choices(record,b'\xA4')

    def test_factory_bound_compound_choices_preserve_the_original_decoder_and_other_bits(self):
        record={**compound(), 'at':8312236, 'source':{'variant':'synthetic-door'}}
        pin=hashlib.sha256(json.dumps(record,sort_keys=True,ensure_ascii=False).encode()).hexdigest()
        with patch('scripts.diagnostics.coding_named_edits._PIWIS_DOOR_CHOICES',{record['at']:(pin,('启用','未启用'))}):
            for fill in (0x5A,0xA5):
                for current in (0,1):
                    before=reference_patch(record,bytes([fill])*12,current)
                    self.assertEqual(decode_record(record,before)['text'],'0','retained X431 display calculation is unchanged')
                    choices=named_coding_choices(record,before)
                    self.assertEqual(choices['currentOptionId'],f'source-option-{current}')
                    self.assertEqual(choices['current']['text'],('启用','未启用')[current])
                    for target in (0,1):
                        draft=preview_named_coding_changes([record],{'220600':before},[edit(record['at'],target)])
                        self.assertEqual(bytes.fromhex(draft['blocks'][0]['afterHex']),reference_patch(record,before,target))
                        self.assertFalse(draft['executionEnabled']);self.assertIsNone(draft['writePayload'])
                        self.assertEqual(len(draft['changes']),int(current!=target))
            self.assertEqual(preview_coding(record,bytes(12),1)['reason'],'preview_compound_enum_meaning_unqualified')

    def test_factory_mapping_requires_the_complete_record_and_does_not_cover_basesys(self):
        record={**compound(), 'at':8312236, 'source':{'variant':'synthetic-door'}}
        pin=hashlib.sha256(json.dumps(record,sort_keys=True,ensure_ascii=False).encode()).hexdigest()
        with patch('scripts.diagnostics.coding_named_edits._PIWIS_DOOR_CHOICES',{record['at']:(pin,('启用','未启用'))}):
            for changed in ({'bitOffset':2},{'byteOffset':4},{'pid':0x601},{'source':{'variant':'other'}},
                            {'name':'other purpose'},{'enumText':{'310007B4':'guess'}},{'extra':'unreviewed'}):
                with self.assertRaisesRegex(ValueError,'factory-choice-source-mismatch'):
                    named_coding_choices({**record,**changed},bytes(12))
            with self.assertRaisesRegex(ValueError,'named-options-unqualified'):
                named_coding_choices({**record,'at':8305489},bytes(12))
        with self.assertRaisesRegex(ValueError,'factory-choice-source-mismatch'):
            named_coding_choices(record,bytes(12))

    def test_factory_brand_option_is_not_renamed_to_activation(self):
        record={**compound('8181003A',7,4), 'at':8313575}
        pin=hashlib.sha256(json.dumps(record,sort_keys=True,ensure_ascii=False).encode()).hexdigest()
        with patch('scripts.diagnostics.coding_named_edits._PIWIS_DOOR_CHOICES',{record['at']:(pin,('Audi','未启用'))}):
            choices=named_coding_choices(record,bytes(12))
            self.assertEqual([o['label'] for o in choices['options']],['Audi','未启用'])
            self.assertEqual(choices['current']['text'],'Audi')
            draft=preview_named_coding_changes([record],{'220600':bytes(12)},[edit(record['at'],1)])
            self.assertEqual((draft['changes'][0]['before'],draft['changes'][0]['after']),('Audi','未启用'))

    def test_large_named_intervals_have_one_source_endpoint_and_preserve_current_member(self):
        record=enum(16,0)
        record['formula']['text']=record['formula']['text'].replace('[0,1]','[0x00,0x0B]').replace('[2,3]','[0x0C,0xFFFF]')
        original=reference_patch(record,b'\x5A\xA5\xFF',0xA5A5)
        choices=named_coding_choices(record,original)
        self.assertEqual([o['rawValue'] for o in choices['options']],[0,12])
        self.assertEqual(choices['currentOptionId'],'source-option-1')
        same=preview_named_coding_changes([record],{'22F100':original},[edit(1,1)])
        self.assertEqual(same['changes'],[]);self.assertEqual(same['blocks'][0]['afterHex'],original.hex().upper())
        changed=preview_named_coding_changes([record],{'22F100':original},[edit(1,0)])
        self.assertEqual(bytes.fromhex(changed['blocks'][0]['afterHex']),reference_patch(record,original,0))
        self.assertFalse(changed['executionEnabled']);self.assertIsNone(changed['writePayload'])
        self.assertEqual(preview_coding(record,original,12)['reason'],'preview_range_enum','legacy arbitrary raw-value preview retains its own limit')

    def test_large_interval_does_not_guess_another_endpoint_when_canonical_value_is_reserved(self):
        record=enum(16,0)
        record['formula']['text']=record['formula']['text'].replace('[0,1]','[0x00,0x0B]').replace('[2,3]','[0x0C,0xFFFF]')+'Ic:[0x0C]->0xF003;'
        record['enumText']['F003']='保留值'
        choices=named_coding_choices(record,b'\xA5\xA5')
        self.assertEqual([o['rawValue'] for o in choices['options']],[0])
        with self.assertRaisesRegex(ValueError,'option-unqualified'):
            preview_named_coding_changes([record],{'22F100':b'\xA5\xA5'},[edit(1,1)])

    def test_named_interval_and_option_count_must_still_fit_the_source_layout(self):
        record=enum(16,0)
        record['formula']['text']=record['formula']['text'].replace('[2,3]','[0x0C,0x1FFFF]')
        with self.assertRaisesRegex(ValueError,'options-unqualified'):named_coding_choices(record,b'\xA5\xA5')
        record['formula']['text']='TEXTTABLE:DataType=A_UINT32,'+''.join(f'[0x{i:X}]->0xF001;' for i in range(257))+'BitLength=16,BitMask=0,HighLow=1;'
        with self.assertRaisesRegex(ValueError,'options-unqualified'):named_coding_choices(record,b'\xA5\xA5')

    def test_reserved_labels_do_not_qualify_a_field_even_with_legal_raw_options(self):
        for label in ('预留 2','保留','Reserved 3','Reserve1','未定义','Unused'):
            record=flag(1,0);record['name']=label
            with self.assertRaisesRegex(ValueError,'purpose-unqualified'):
                preview_named_coding_changes([record],{'22F100':b'\0'},[edit(1)])
        known=flag(1,0);reserved=flag(2,1);reserved['name']='预留 2'
        self.assertEqual(preview_named_coding_changes([known,reserved],{'22F100':b'\xA4'},[edit(1)])['blocks'][0]['afterHex'],'A5')
        reserved['bitOffset']=0
        with self.assertRaisesRegex(ValueError,'reserved-field-changed'):
            preview_named_coding_changes([known,reserved],{'22F100':b'\0'},[edit(1)])
    def test_source_ranges_provide_one_canonical_target_and_preserve_current_range_member(self):
        record = enum(3, 1)
        choices = named_coding_choices(record, b'\xA2')  # raw1, in first source interval
        self.assertEqual(choices['currentOptionId'], 'source-option-0')
        self.assertEqual(choices['options'], [dict(id='source-option-0', label='关闭', rawValue=0),
                                             dict(id='source-option-1', label='开启', rawValue=2)])
        result = preview_named_coding_changes([record], {'22F100': b'\xA2'}, [edit(1,0)])
        self.assertEqual(result['changes'], [])
        self.assertEqual(result['blocks'][0]['afterHex'], 'A2')
        self.assertFalse(result['executionEnabled']); self.assertIsNone(result['writePayload'])

    def test_named_changes_preserve_complete_blocks_and_are_order_independent(self):
        records = [flag(1,0), flag(2,1)]
        originals = {'22F100': b'\xA4\x5A', '22F101': b'\xFF'}
        untouched = deepcopy(originals)
        for order in itertools.permutations([edit(1),edit(2)]):
            result = preview_named_coding_changes(records, originals, list(order))
            blocks = {b['requestHex']: b for b in result['blocks']}
            self.assertEqual(blocks['22F100']['afterHex'], 'A75A')
            self.assertEqual(blocks['22F100']['changedBitMaskHex'], '0300')
            self.assertEqual(blocks['22F101']['afterHex'], 'FF')
            self.assertEqual(originals, untouched)

    def test_actual_bit_patch_matches_independent_reference_in_both_byte_orders(self):
        for order in (0,1):
            record=enum(12,4,order)
            record['formula']['text']=record['formula']['text'].replace('[0,1]','[0x123]').replace('[2,3]','[0xABC]')
            original=reference_patch(record,b'\x5A\xA5\xFF',0x123)
            result=preview_named_coding_changes([record], {'22F100': original}, [edit(1)])
            self.assertEqual(bytes.fromhex(result['blocks'][0]['afterHex']),reference_patch(record,original,0xABC))

    def test_overlapping_selected_targets_are_checked_after_combining(self):
        a,b=flag(1,0),flag(2,0)
        with self.assertRaisesRegex(ValueError,'target-conflict'):
            preview_named_coding_changes([a,b], {'22F100':b'\0'}, [edit(1),edit(2,0)])
        self.assertEqual(preview_named_coding_changes([a,b], {'22F100':b'\0'}, [edit(1),edit(2)])['blocks'][0]['afterHex'],'01')

    def test_unselected_distinct_field_cannot_change_silently(self):
        a,b=flag(1,0),enum(2,0)
        b['at']=2
        with self.assertRaisesRegex(ValueError,'unselected-field-changed'):
            preview_named_coding_changes([a,b], {'22F100':b'\0'}, [edit(1)])

    def test_unknown_current_choice_is_not_invented_and_unknown_unchanged_bits_are_preserved(self):
        a=flag(1,0);b=enum(3,1);b['at']=2
        result=preview_named_coding_changes([a,b],{'22F100':b'\xA8'},[edit(1)])
        self.assertEqual(result['blocks'][0]['afterHex'],'A9')
        self.assertEqual(named_coding_choices(b,b'\xA8')['currentOptionId'],None)
        result=preview_named_coding_changes([b],{'22F100':b'\xA8'},[edit(2)])
        self.assertEqual(result['changes'][0]['before'],'未识别的当前值')
        self.assertEqual(result['blocks'][0]['afterHex'],'A4')

    def test_reserved_current_bits_are_backed_by_original_not_offered_as_target(self):
        record=enum(3,1)
        record['formula']['text']+='Ic:[4,7]->0xF003;'
        record['enumText']['F003']='保留值'
        choices=named_coding_choices(record,b'\xA8')
        self.assertIsNone(choices['currentOptionId'])
        self.assertEqual([o['rawValue'] for o in choices['options']],[0,2])
        draft=preview_named_coding_changes([record],{'22F100':b'\xA8'},[edit(1)])
        self.assertEqual(draft['blocks'][0]['beforeHex'],'A8')
        self.assertEqual(draft['blocks'][0]['afterHex'],'A4')
        with self.assertRaisesRegex(ValueError,'option-unqualified'):
            preview_named_coding_changes([record],{'22F100':b'\xA8'},[edit(1,2)])

    def test_exact_alias_and_opaque_projection_are_reported_without_losing_unknown_bits(self):
        a=flag(1,0); alias=deepcopy(a);alias['at']=2
        aggregate=deepcopy(a);aggregate['at']=3
        aggregate['formula']['text']='IDENTICAL:Standar,BitMask=0,BaseDataType=A_BYTEFIELD,BitLength=16,Encoding=Undefined,HighLow=1,Radix=16,DataType=A_BYTEFIELD,Precision=null;'
        result=preview_named_coding_changes([a,alias,aggregate], {'22F100':b'\xA4\x5A'}, [edit(1)])
        self.assertEqual(result['blocks'][0]['afterHex'],'A55A')
        self.assertEqual(result['aliases'],[dict(fieldId='2',requestHex='22F100',kind='same-field-alias'),
                                           dict(fieldId='3',requestHex='22F100',kind='block-projection')])

    def test_unknown_overlapping_definition_and_missing_original_are_rejected(self):
        a=flag(1,0);bad=deepcopy(a);bad.update(at=2,formula={'text':'unresolved'})
        with self.assertRaisesRegex(ValueError,'overlapping-layout-unresolved'):
            preview_named_coding_changes([a,bad], {'22F100':b'\0'}, [edit(1)])
        with self.assertRaisesRegex(ValueError,'original-block-missing'):
            preview_named_coding_changes([a], {'22F101':b'\0'}, [edit(1)])

    def test_unresolved_options_duplicates_raw_values_and_extra_inputs_are_rejected(self):
        a=flag(1,0)
        for edits in ([edit(1),edit(1)], [edit(9)], [edit(1,99)],
                      [dict(fieldId='1',rawValue=1)], [dict(**edit(1),rawValue=1)]):
            with self.assertRaisesRegex(ValueError,'selection-invalid|option-unqualified'):
                preview_named_coding_changes([a],{'22F100':b'\0'},edits)
        a['enumText']={}
        with self.assertRaisesRegex(ValueError,'named-options-unqualified'):
            preview_named_coding_changes([a],{'22F100':b'\0'},[edit(1)])

    def test_cancel_guard_applies_before_source_and_draft_work(self):
        def cancel():raise ValueError('cancelled')
        with self.assertRaisesRegex(ValueError,'cancelled'):
            preview_named_coding_changes([flag(1,0)],{'22F100':b'\0'},[edit(1)],cancel)


if __name__ == '__main__':unittest.main()
