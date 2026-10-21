from copy import deepcopy
from fractions import Fraction
import itertools
import unittest

from scripts.diagnostics.coding_named_edits import preview_coding_field_changes,preview_named_coding_changes
from scripts.diagnostics.coding_numeric import rational
from scripts.diagnostics.decoder_reference import reference_patch
from scripts.diagnostics.tests.test_coding_numeric import linear
from scripts.diagnostics.tests.test_coding_named_edits import flag,edit


class NumericDraftTests(unittest.TestCase):
    def test_reserved_numeric_field_cannot_be_selected_using_its_numeric_bounds(self):
        number=linear(width=8,upper=255);number['labels']=[dict(text='预留 4')]
        with self.assertRaisesRegex(ValueError,'purpose-unqualified'):
            preview_coding_field_changes([number],{'22F100':bytes(6)},[dict(fieldId='100',physicalValue='4')])
    def test_physical_target_and_named_choice_combine_against_same_complete_block(self):
        number=linear(width=8,upper=255);number['byteOffset']=1
        records=[flag(1,0),number];before={'22F100':b'\xA4\x03\xFF','22F101':b'\x5A'}
        unchanged=deepcopy(before)
        for changes in itertools.permutations([edit(1),dict(fieldId='100',physicalValue='10')]):
            result=preview_coding_field_changes(records,before,list(changes))
            self.assertEqual(result['blocks'][0]['afterHex'],'A50AFF')
            self.assertEqual(result['blocks'][1]['afterHex'],'5A')
            self.assertEqual(before,unchanged)
            number_change=next(c for c in result['changes'] if c['fieldId']=='100')
            self.assertEqual(number_change['targetPhysical'],rational(10))
            self.assertFalse(result['executionEnabled']);self.assertIsNone(result['writePayload'])

    def test_fraction_is_exact_and_not_rounded_to_nearest_supported_value(self):
        number=linear(width=8,upper=255,xa='1',xb='3',xc='2')
        before=bytes(6)
        result=preview_coding_field_changes([number],{'22F100':before},[dict(fieldId='100',physicalValue='5/3')])
        self.assertEqual(bytes.fromhex(result['blocks'][0]['afterHex']),reference_patch(number,before,3))
        self.assertEqual(result['changes'][0]['targetPhysical'],rational(Fraction(5,3)))
        with self.assertRaisesRegex(ValueError,'not-representable'):
            preview_coding_field_changes([number],{'22F100':before},[dict(fieldId='100',physicalValue='0.5')])

    def test_negative_signed_target_both_orders_preserves_unknown_bits(self):
        for order in (0,1):
            number=linear(width=8,shift=7,order=order,lower=-128,upper=127,signed=True)
            before=bytes.fromhex('FAA56DB5FFFF')
            result=preview_coding_field_changes([number],{'22F100':before},[dict(fieldId='100',physicalValue='-123')])
            self.assertEqual(bytes.fromhex(result['blocks'][0]['afterHex']),reference_patch(number,before,133))

    def test_same_exact_physical_value_removes_change_without_normalizing_bytes(self):
        number=linear(width=8,upper=255,xa='1',xb='3',xc='2')
        before=bytes.fromhex('FA5A03A5')
        result=preview_coding_field_changes([number],{'22F100':before},[dict(fieldId='100',physicalValue='5/3')])
        self.assertEqual(result['changes'],[])
        self.assertEqual(result['blocks'][0]['afterHex'],before.hex().upper())

    def test_source_bounds_reserved_values_and_mask_holes_are_rejected(self):
        number=linear(width=8,upper=255,mask='0F',reserved='Ic:[0x2,0x3]->0xF001;')
        before={'22F100':bytes.fromhex('000001FFFF')}
        for text,reason in [('256','out-of-source-bounds'),('2','reserved'),('16','mask-unrepresentable')]:
            with self.assertRaisesRegex(ValueError,reason):
                preview_coding_field_changes([number],before,[dict(fieldId='100',physicalValue=text)])

    def test_missing_explicit_source_bounds_do_not_invent_a_vehicle_input_range(self):
        number=linear(width=8,upper='null')
        with self.assertRaisesRegex(ValueError,'complete-source-bounds-required'):
            preview_coding_field_changes([number],{'22F100':bytes(6)},[dict(fieldId='100',physicalValue='10')])

    def test_unexpected_current_value_is_preserved_without_becoming_a_legal_target(self):
        for number,raw in [(linear(width=8,upper=255,reserved='Ic:[0x2,0x3]->0xF001;'),2),
                           (linear(width=8,upper=10),20)]:
            before=bytes([0xA5,0x5A,raw,0xFF])
            draft=preview_coding_field_changes([number],{'22F100':before},[dict(fieldId='100',physicalValue='4')])
            self.assertEqual(draft['blocks'][0]['beforeHex'],before.hex().upper())
            self.assertEqual(draft['blocks'][0]['afterHex'],'A55A04FF')
            self.assertEqual(draft['changes'][0]['before'],'未识别的当前值')
            with self.assertRaisesRegex(ValueError,'reserved|out-of-source-bounds'):
                preview_coding_field_changes([number],{'22F100':before},[dict(fieldId='100',physicalValue=str(raw))])

    def test_numeric_input_does_not_bypass_enum_choices_or_source_layout(self):
        with self.assertRaisesRegex(ValueError,'linear-definition-required'):
            preview_coding_field_changes([flag(1,0)],{'22F100':b'\0'},[dict(fieldId='1',physicalValue='1')])
        with self.assertRaisesRegex(ValueError,'selection-invalid'):
            preview_named_coding_changes([linear()],{'22F100':bytes(6)},[dict(fieldId='100',physicalValue='1')])
        for change in [dict(fieldId='100',physicalValue='1e3'),dict(fieldId='100',physicalValue='NaN'),
                       dict(fieldId='100',physicalValue='1',rawValue=1),dict(fieldId='100',physicalValue=True)]:
            with self.assertRaisesRegex(ValueError,'target-invalid|selection-invalid'):
                preview_coding_field_changes([linear()],{'22F100':bytes(6)},[change])

    def test_overlapping_enum_and_numeric_targets_cannot_override_each_other(self):
        number=linear(width=8,upper=255);number['byteOffset']=0
        records=[flag(1,0),number]
        with self.assertRaisesRegex(ValueError,'target-conflict'):
            preview_coding_field_changes(records,{'22F100':b'\0'},[edit(1),dict(fieldId='100',physicalValue='2')])


if __name__=='__main__':unittest.main()
