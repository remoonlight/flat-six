import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from copy import deepcopy
from unittest.mock import patch

from scripts.diagnostics.manufacturer_coding_fields import coding_display_fields, coding_named_edit_draft, coding_edit_draft, _coding_definition
from scripts.diagnostics.tests.test_coding_read_origin import record, variant


class CodingDisplayTests(unittest.TestCase):
    def test_factory_choice_current_display_agrees_before_and_after_opening_the_editor(self):
        self.record.update(formula='TEXTTABLE:DataType=A_UINT32,[0x00]->0x310007B4xF0002A75;[0x01]->0x310007B4;LengthInfo=Standard,BitLength=1,BitMask=0,HighLow=1;',
                           enumText={'310007B4':'0'},byteOffset=0,bitOffset=0)
        self.file.write_text(json.dumps(variant([self.record]))+'\n')
        self.anchor.stop();self.anchor=patch('scripts.diagnostics.manufacturer_coding_fields.ANCHORS',
            {'variants':hashlib.sha256(self.file.read_bytes()).hexdigest()})
        self.anchor.start();self.addCleanup(self.anchor.stop)
        resolved=_coding_definition(self.source,lambda:None)['pool_records']['coding']['records'][0]
        pin=hashlib.sha256(json.dumps(resolved,sort_keys=True,ensure_ascii=False).encode()).hexdigest()
        with patch('scripts.diagnostics.coding_named_edits._PIWIS_DOOR_CHOICES',{100:(pin,('启用','未启用'))}):
            for data,label in [('2A','启用'),('2B','未启用')]:
                self.blocks[0]['dataHex']=data
                read=coding_display_fields(self.source,self.blocks,lambda:None)[0]
                editable=coding_display_fields(self.source,self.blocks,lambda:None,draft_choices=True)[0]
                self.assertEqual(read['currentDisplay'],label);self.assertEqual(editable['currentDisplay'],label)
                self.assertFalse(read['editable']);self.assertTrue(editable['editable'])
                self.assertEqual([o['label'] for o in editable['options']],['启用','未启用'])
            draft=coding_edit_draft(self.source,self.blocks,[dict(fieldId='100',optionId='source-option-0')],lambda:None)
            self.assertEqual(draft['changes'][0]['after'],'启用');self.assertEqual(draft['blocks'][0]['afterHex'],'2A')

    def test_factory_only_originals_are_required_and_preserved_without_becoming_editable(self):
        self._named_source();extra=dict(requestHex='2204A3',dataHex='A55AFF')
        with patch('scripts.diagnostics.manufacturer_coding_fields._read_bindings',return_value=({'factoryOnlyReadRequests':[{'requestHex':'2204A3'}]},{})):
            for build in (coding_edit_draft,coding_named_edit_draft):
                result=build(self.source,self.blocks+[extra],[dict(fieldId='100',optionId='source-option-1')],lambda:None)
                block=next(b for b in result['blocks'] if b['requestHex']=='2204A3')
                self.assertEqual(block['beforeHex'],extra['dataHex']);self.assertEqual(block['afterHex'],extra['dataHex'])
                self.assertEqual(block['changedBitMaskHex'],'000000')
                with self.assertRaisesRegex(ValueError,'original-coverage-invalid'):
                    build(self.source,self.blocks,[dict(fieldId='100',optionId='source-option-1')],lambda:None)
                with self.assertRaisesRegex(ValueError,'original-coverage-invalid'):
                    build(self.source,self.blocks+[extra,dict(requestHex='2204A4',dataHex='FF')],[dict(fieldId='100',optionId='source-option-1')],lambda:None)
    def test_variable_text_metadata_retains_full_factory_sized_original_and_zero_tail(self):
        self.record['formula']='IDENTICAL:MinMax,MinLength=0,BaseDataType=A_ASCIISTRING,MaxLength=8,BitMask=0,Encoding=ISO-8859-2,Termination=ZERO,Radix=10,DataType=A_UNICODE2STRING,Precision=null;'
        self.file.write_text(json.dumps(variant([self.record]))+'\n')
        with patch('scripts.diagnostics.manufacturer_coding_fields.ANCHORS',{'variants':hashlib.sha256(self.file.read_bytes()).hexdigest()}):
            self.blocks[0]['dataHex']='4F4C44210058595A'
            shown=coding_display_fields(self.source,self.blocks,lambda:None,draft_choices=True)[0]
            self.assertEqual(shown['text'],dict(length=7,minimumLength=0,termination='ZERO',encoding='ISO-8859-2',currentValue='OLD!'))
            self.assertFalse(coding_display_fields(self.source,self.blocks,lambda:None)[0]['editable'])
            draft=coding_edit_draft(self.source,self.blocks,[dict(fieldId='100',textValue='')],lambda:None)
            self.assertEqual(draft['blocks'][0]['afterHex'],'004C44210058595A')
            self.assertEqual(draft['blocks'][0]['beforeHex'],self.blocks[0]['dataHex'])
    def test_fixed_text_metadata_is_private_source_editing_only(self):
        self.record['formula']='IDENTICAL:Standar,BitMask=0,BaseDataType=A_ASCIISTRING,BitLength=32,Encoding=ISO-8859-2,HighLow=1,Radix=10,DataType=A_UNICODE2STRING,Precision=null;'
        self.file.write_text(json.dumps(variant([self.record]))+'\n')
        self.anchor.stop();self.anchor=patch('scripts.diagnostics.manufacturer_coding_fields.ANCHORS',{'variants':hashlib.sha256(self.file.read_bytes()).hexdigest()})
        self.anchor.start();self.addCleanup(self.anchor.stop)
        self.blocks[0]['dataHex']='4F4C4421'
        shown=coding_display_fields(self.source,self.blocks,lambda:None,draft_choices=True)[0]
        self.assertEqual(shown['text'],dict(length=4,encoding='ISO-8859-2',currentValue='OLD!'))
        self.assertTrue(shown['editable']);self.assertEqual(shown['options'],[])
        self.assertFalse(coding_display_fields(self.source,self.blocks,lambda:None)[0]['editable'])
        draft=coding_edit_draft(self.source,self.blocks,[dict(fieldId='100',textValue='NEW!')],lambda:None)
        self.assertEqual(draft['blocks'][0]['afterHex'],'4E455721')
        self.blocks[0]['dataHex']='4F4C0000'
        self.assertIsNone(coding_display_fields(self.source,self.blocks,lambda:None,draft_choices=True)[0]['text']['currentValue'])
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.file = Path(self.temp.name) / 'variants.jsonl'
        self.record = record(0x22, 0x04A1, 100)
        self.file.write_text(json.dumps(variant([self.record])) + '\n')
        self.source = dict(profileId='source-fixture', coding=dict(definitionFieldCount=1,
            requestGroups=[dict(requestHex='2204A1', namedFields=[dict(at=100, displayName='来源名称')],
                                fields=[dict(sourceOffset=100)])]))
        self.blocks = [dict(requestHex='2204A1', dataHex='2A')]
        self.env = patch.dict(os.environ, {'PORSCHE981_VARIANTS': str(self.file)})
        self.env.start(); self.addCleanup(self.env.stop)
        self.anchor = patch('scripts.diagnostics.manufacturer_coding_fields.ANCHORS',
                            {'variants': hashlib.sha256(self.file.read_bytes()).hexdigest()})
        self.anchor.start(); self.addCleanup(self.anchor.stop)

    def test_actual_source_value_and_name_have_no_write_qualification(self):
        fields = coding_display_fields(self.source, self.blocks, lambda: None)
        self.assertEqual(fields[0]['name'], '来源名称')
        self.assertEqual(fields[0]['currentDisplay'], '42')
        self.assertIs(fields[0]['editable'], False)
        self.assertEqual(fields[0]['options'], [])

    def test_changed_source_fails_instead_of_showing_invented_values(self):
        self.file.write_text(self.file.read_text() + '\n')
        with self.assertRaisesRegex(ValueError, 'coding-display-source-mismatch'):
            coding_display_fields(self.source, self.blocks, lambda: None)

    def test_incomplete_named_coverage_fails(self):
        self.source['coding']['requestGroups'][0]['namedFields'] = []
        with self.assertRaisesRegex(ValueError, 'coding-display-name-missing'):
            coding_display_fields(self.source, self.blocks, lambda: None)

    def test_cancel_guard_covers_file_and_decoding_work(self):
        def cancelled():
            raise ValueError('cancelled')
        with self.assertRaisesRegex(ValueError, 'cancelled'):
            coding_display_fields(self.source, self.blocks, cancelled)

    def _named_source(self):
        self.record.update(enumText={'F001':'关闭','F002':'开启'}, formula=
            'TEXTTABLE:BaseDataType=A_UINT8,DataType=A_UINT8,[0]->0xF001;[1]->0xF002;BitLength=1,BitMask=0,HighLow=1;')
        self.file.write_text(json.dumps(variant([self.record]))+'\n')
        self.anchor.stop()
        self.anchor=patch('scripts.diagnostics.manufacturer_coding_fields.ANCHORS',
            {'variants':hashlib.sha256(self.file.read_bytes()).hexdigest()})
        self.anchor.start();self.addCleanup(self.anchor.stop)

    def test_draft_uses_pinned_records_and_preserves_complete_original(self):
        self._named_source();before=deepcopy(self.blocks)
        draft=coding_named_edit_draft(self.source,self.blocks,[dict(fieldId='100',optionId='source-option-1')],lambda:None)
        self.assertEqual(draft['blocks'][0]['afterHex'],'2B')
        self.assertEqual(draft['blocks'][0]['changedBitMaskHex'],'01')
        self.assertEqual(self.blocks,before)
        self.assertFalse(draft['executionEnabled'])

    def test_draft_field_options_are_source_ids_and_labels_without_raw_values(self):
        self._named_source()
        field=coding_display_fields(self.source,self.blocks,lambda:None,draft_choices=True)[0]
        self.assertTrue(field['editable'])
        self.assertEqual(field['currentOptionId'],'source-option-0')
        self.assertEqual(field['options'],[dict(id='source-option-0',label='关闭'),dict(id='source-option-1',label='开启')])
        self.assertFalse(coding_display_fields(self.source,self.blocks,lambda:None)[0]['editable'])

    def test_equivalent_duplicate_options_keep_the_actual_current_range_identity(self):
        self._named_source()
        self.record['formula']=('TEXTTABLE:BaseDataType=A_UINT8,DataType=A_UINT8,'
            '[0]->0xF001;[0,1]->0xF001;[2]->0xF002;BitLength=8,BitMask=0,HighLow=1;')
        self.file.write_text(json.dumps(variant([self.record]))+'\n')
        with patch('scripts.diagnostics.manufacturer_coding_fields.ANCHORS',
                   {'variants':hashlib.sha256(self.file.read_bytes()).hexdigest()}):
            field=coding_display_fields(self.source,[dict(requestHex='2204A1',dataHex='01')],lambda:None,draft_choices=True)[0]
        self.assertEqual(field['currentOptionId'],'source-option-1')
        self.assertEqual(field['options'],[dict(id='source-option-1',label='关闭'),dict(id='source-option-2',label='开启')])

    def test_draft_requires_all_source_fields_and_exact_read_block_coverage(self):
        self._named_source();edits=[dict(fieldId='100',optionId='source-option-1')]
        source=deepcopy(self.source);source['coding']['requestGroups'][0]['fields']=[]
        with self.assertRaisesRegex(ValueError,'source-coverage-invalid'):
            coding_named_edit_draft(source,self.blocks,edits,lambda:None)
        for blocks in ([],self.blocks*2,self.blocks+[dict(requestHex='2204A2',dataHex='FF')]):
            with self.assertRaisesRegex(ValueError,'original-coverage-invalid|original-block-duplicate'):
                coding_named_edit_draft(self.source,blocks,edits,lambda:None)

    def test_draft_keeps_source_read_alias(self):
        self._named_source();edits=[dict(fieldId='100',optionId='source-option-1')]
        with patch('scripts.diagnostics.manufacturer_coding_fields._read_bindings',return_value=(None,{'2204A1':'2204A2'})):
            draft=coding_named_edit_draft(self.source,[dict(requestHex='2204A2',dataHex='2A')],edits,lambda:None)
        self.assertEqual(draft['blocks'][0]['requestHex'],'2204A2')
        self.assertEqual(draft['changes'][0]['requestHex'],'2204A2')

    def test_numeric_draft_uses_source_bounds_and_full_original_read(self):
        self.record['formula']=('LINEAR:Lower=0,Upper=255,BaseDataType=A_UINT8,DataType=A_FLOAT64,'
            'Xa=0,Xb=2,Xc=1,BitLength=8,HighLow=1,BitMask=0,Encoding=Undefined,Precision=2;')
        self.file.write_text(json.dumps(variant([self.record]))+'\n')
        with patch('scripts.diagnostics.manufacturer_coding_fields.ANCHORS',
                   {'variants':hashlib.sha256(self.file.read_bytes()).hexdigest()}):
            draft=coding_edit_draft(self.source,self.blocks,[dict(fieldId='100',physicalValue='21.5')],lambda:None)
        self.assertEqual(draft['blocks'][0]['beforeHex'],'2A')
        self.assertEqual(draft['blocks'][0]['afterHex'],'2B')
        self.assertEqual(draft['changes'][0]['targetPhysical'],dict(numerator='43',denominator='2'))
        self.assertFalse(draft['executionEnabled'])

    def test_numeric_metadata_uses_exact_physical_value_not_rounded_display(self):
        self.record['unit']='ms'
        self.record['formula']=('LINEAR:Lower=0,Upper=255,BaseDataType=A_UINT8,DataType=A_FLOAT64,'
            'Xa=1,Xb=3,Xc=2,BitLength=8,HighLow=1,BitMask=0,Encoding=Undefined,Precision=2;')
        self.file.write_text(json.dumps(variant([self.record]))+'\n')
        with patch('scripts.diagnostics.manufacturer_coding_fields.ANCHORS',
                   {'variants':hashlib.sha256(self.file.read_bytes()).hexdigest()}):
            field=coding_display_fields(self.source,[dict(requestHex='2204A1',dataHex='03')],lambda:None,draft_choices=True)[0]
            self.record['formula']+='Ic:[0xA5]->0xF003;'
            self.file.write_text(json.dumps(variant([self.record]))+'\n')
            with patch('scripts.diagnostics.manufacturer_coding_fields.ANCHORS',
                       {'variants':hashlib.sha256(self.file.read_bytes()).hexdigest()}):
                unknown=coding_display_fields(self.source,[dict(requestHex='2204A1',dataHex='A5')],lambda:None,draft_choices=True)[0]
        self.assertTrue(field['editable']);self.assertEqual(field['options'],[])
        self.assertEqual(field['numeric'],dict(minimum='-1/3',maximum='509/3',currentValue='5/3',unit='ms'))
        self.assertEqual(field['currentDisplay'],'1.67 ms')
        self.assertEqual(unknown['numeric']['unit'],'ms');self.assertIsNone(unknown['numeric']['currentValue'])

    def test_large_binary_field_has_bounded_display_and_keeps_complete_original(self):
        for size in (1,2048,2049,4092):
            self.record['formula'] = ('IDENTICAL:Standar,BitMask=0,BaseDataType=A_BYTEFIELD,'
                f'BitLength={size * 8},Encoding=Undefined,HighLow=1,Radix=16,'
                'DataType=A_BYTEFIELD,Precision=null;')
            self.file.write_text(json.dumps(variant([self.record])) + '\n')
            blocks=[dict(requestHex='2204A1',dataHex='A5'*size)];before=deepcopy(blocks)
            with patch('scripts.diagnostics.manufacturer_coding_fields.ANCHORS',
                       {'variants':hashlib.sha256(self.file.read_bytes()).hexdigest()}):
                field=coding_display_fields(self.source,blocks,lambda:None)[0]
            self.assertEqual(blocks,before)
            self.assertFalse(field['editable']);self.assertEqual(field['options'],[])
            self.assertEqual(field['currentDisplay'],'A5'*size if size<=2048 else f'二进制数据（{size} 字节）')
