"""Factory heading boundaries; pure parser tests, no PDF/device dependencies."""
from pathlib import Path
import importlib.util
import unittest

spec = importlib.util.spec_from_file_location(
    'manual_title_headers', Path(__file__).resolve().parents[3] / 'scripts/build-obd-manual-titles.py')
parser = importlib.util.module_from_spec(spec)
spec.loader.exec_module(parser)


def line(text, top, size=11.756, font='Arial-BoldMT_3'):
    return {'text': text, 'top': top, 'words': [{'text': text, 'fontname': font, 'size': size}]}


def english(title='Bank 1 intake camshaft adjustment', heading='Diagnostic information - DME (DFI) control unit'):
    return [line('P000A', 22, 24, 'Helvetica'), line(heading, 84, 14.85),
            line(title, 115), line('Diagnostic conditions', 142)]


class ManualTitleHeaderTests(unittest.TestCase):
    def test_chinese_body_module_mentions_do_not_invalidate_heading(self):
        rows, reason = parser.parse([line('诊断 DME 控制', 110, 17.485),
            line('P050200 车速信号 1 - 信号过低', 158), line('诊断条', 213),
            line('诊断检查在控制单元中执行', 350, 17.485)], '981', 4018)
        self.assertIsNone(reason)
        self.assertEqual(rows[0]['code'], 'P050200')
        self.assertEqual(rows[0]['title'], '车速信号 1 - 信号过低')

    def test_two_real_module_headings_are_rejected(self):
        rows, reason = parser.parse([line('诊断 DME 控制', 110, 17.485),
            line('诊断 PDK 控制', 128, 17.485), line('P050200 车速信号', 158), line('诊断条', 213)], '981', 1)
        self.assertFalse(rows)
        self.assertEqual(reason, 'module-header-not-unique')

    def test_body_code_is_not_a_header(self):
        rows, reason = parser.parse([line('诊断 DME 控制', 110, 17.485),
            line('P050200 车速信号', 400), line('诊断条', 430)], '981', 1)
        self.assertFalse(rows)
        self.assertEqual(reason, 'code-outside-title-band')

    def test_diagnosis_variant_and_duplicate_printed_code(self):
        lines = english('P000A Original title', 'Diagnosis information DME control module')
        rows, reason = parser.parse(lines, '982', 1)
        self.assertIsNone(reason)
        self.assertEqual(rows[0]['title'], 'Original title')
        self.assertEqual(rows[0]['module'], 'dme')

    def test_dme_heading_without_control_unit_suffix(self):
        rows, reason = parser.parse(english(heading='Diagnostic information - DME (DFI)'), '982', 1)
        self.assertIsNone(reason)
        self.assertEqual(rows[0]['module'], 'dme')

    def test_wrapped_title_stops_at_notice_not_bold_warning_body(self):
        lines = english('Oxygen sensor heater driver, bank')[:3] + [line('1', 128),
            line('NOTE', 156, 14.85, 'Arial-BoldItalicMT'), line('Do not use spray!', 173)]
        rows, reason = parser.parse(lines, '982', 4258)
        self.assertIsNone(reason)
        self.assertEqual(rows[0]['title'], 'Oxygen sensor heater driver, bank 1')
        self.assertEqual(rows[0]['sectionBoundary'], 'NOTE')

    def test_applicability_is_preserved_outside_title(self):
        lines = english('Fuel pump control unit: activation')[:3] + [
            line('I Only for rear-wheel drive vehicles', 129, font='ArialMT_5'),
            line('Diagnostic conditions', 160)]
        rows, reason = parser.parse(lines, '982', 4369)
        self.assertIsNone(reason)
        self.assertEqual(rows[0]['title'], 'Fuel pump control unit: activation')
        self.assertEqual(rows[0]['applicability'], ['I Only for rear-wheel drive vehicles'])

    def test_unspecified_can_heading_does_not_become_gateway(self):
        rows, reason = parser.parse(english(heading='Diagnostic information - CAN'), '982', 5182)
        self.assertIsNone(reason)
        self.assertIsNone(rows[0]['module'])

    def test_different_repeated_code_is_not_assigned_to_header(self):
        rows, reason = parser.parse(english('P000B Other title'), '982', 1)
        self.assertFalse(rows)
        self.assertEqual(reason, 'different-code-in-title')

    def test_regular_title_and_parenthetical_text_are_preserved(self):
        lines = english('Clutch switch implausible (EPB)')
        lines[2]['words'][0]['fontname'] = 'ArialMT_5'
        rows, reason = parser.parse(lines, '982', 4558)
        self.assertIsNone(reason)
        self.assertEqual(rows[0]['title'], 'Clutch switch implausible (EPB)')

    def test_other_applicability_bullet_is_not_silently_dropped(self):
        lines = english('Charge air cooling - inadequate effect')[:3] + [
            line('I Carrera only', 129, font='SidisSymbole'), line('Diagnostic conditions', 160)]
        rows, reason = parser.parse(lines, '982', 4598)
        self.assertIsNone(reason)
        self.assertEqual(rows[0]['applicability'], ['I Carrera only'])

    def test_unknown_font_cannot_become_a_title(self):
        lines = english(); lines[2] = line('Body prose', 115, font='UnknownFont')
        rows, reason = parser.parse(lines, '982', 1)
        self.assertFalse(rows)
        self.assertEqual(reason, 'title-not-complete')

    def test_title_too_far_below_heading_is_rejected(self):
        lines = english(); lines[2]['top'] = 190
        rows, reason = parser.parse(lines, '982', 1)
        self.assertFalse(rows)
        self.assertEqual(reason, 'title-outside-title-band')

    def test_table_of_contents_code_position_is_rejected(self):
        lines = english(); lines[0]['top'] = 86
        rows, reason = parser.parse(lines, '982', 1)
        self.assertFalse(rows)
        self.assertEqual(reason, 'code-outside-title-band')

    def test_no_title_boundary_is_rejected(self):
        rows, reason = parser.parse(english()[:3], '982', 1)
        self.assertFalse(rows)
        self.assertEqual(reason, 'title-boundary-not-found')

    def test_unknown_chinese_glyph_is_not_replaced_with_a_guess(self):
        rows, reason = parser.parse([line('诊断 DME 控制', 110, 17.485),
            line('P050200 车速\u4cf4信号', 158), line('诊断条', 213)], '981', 1)
        self.assertFalse(rows)
        self.assertEqual(reason, 'unreviewed-recovered-glyph')


class ManualTitleReferenceAdmissionTests(unittest.TestCase):
    def reference(self, **extra):
        return {'generation': '982', 'module': 'dme', 'sourceSection': '9x1_DME_DFI_17-_17500', **extra}

    def test_consistent_source_section_is_required(self):
        self.assertIsNone(parser.reference_rejection(self.reference()))
        self.assertEqual(parser.reference_rejection(self.reference(sourceSection=None)), 'source-section-unavailable')

    def test_non_target_source_section_overrides_generic_dme_header(self):
        row = self.reference(sourceSection='95B_Soundaktuator_innen_14-_14650')
        self.assertEqual(parser.reference_rejection(row), 'non-target-source-section')

    def test_module_conflict_is_not_relabelled(self):
        self.assertEqual(parser.reference_rejection(self.reference(sourceSection='9x1_PDK_12-_17100')), 'module-source-conflict')

    def test_explicit_carrera_header_is_not_target_evidence(self):
        self.assertEqual(parser.reference_rejection(self.reference(applicability=['I Carrera only'])),
                         'extracted-header-non-target-applicability')

    def test_unresolved_can_heading_is_quarantined(self):
        self.assertEqual(parser.reference_rejection(self.reference(module=None)), 'module-unresolved')

    def test_literal_manufacturer_code_needs_exact_module_and_section(self):
        rows,reason=parser.parse([line('C13235',23,24,'Helvetica'),
            line('Diagnosis information - Rear-end electronics',84,14.85),
            line('Drive Air deflector',114),line('Diagnostic conditions',142),
            line('9x1_BCM_hinten_12_14650 138 / 150 05.09.2014',792)],'982',5825)
        self.assertIsNone(reason);self.assertEqual(rows[0]['module'],'bcm-rear')
        self.assertIsNone(parser.reference_rejection(rows[0]))
        self.assertEqual(parser.reference_rejection({**rows[0],'sourceSection':'9x1_BCM_vorne_12_14650'}),'module-source-conflict')
        self.assertEqual(parser.reference_rejection({**rows[0],'code':'B200000'}),'code-namespace-unqualified')

    def test_headlight_reference_does_not_move_to_the_opposite_side(self):
        self.assertIsNone(parser.reference_rejection(self.reference(module='headlamp-left',code='C11000',sourceSection='9x1_SWSG_links_12_14650')))
        self.assertEqual(parser.reference_rejection(self.reference(module='headlamp-left',code='C11000',sourceSection='9x1_SWSG_rechts_12_14650')),'module-source-conflict')

    def test_symbol_font_warning_is_not_a_vehicle_condition(self):
        for warning in ['8 WARNING','MN CAUTION','K INFORMATION','8 DANGER']:
            lines=english('Airbag control unit - Locking','Diagnosis information - Airbag control unit')
            lines[0]=line('C11004',23,24,'Helvetica')
            lines[3:3]=[line(warning,140,10,'SidisSymbole'),line('Original safety notice',160)]
            rows,reason=parser.parse(lines,'982',1)
            self.assertIsNone(reason);self.assertNotIn('applicability',rows[0]);self.assertEqual(rows[0]['title'],'Airbag control unit - Locking')


if __name__ == '__main__':
    unittest.main()
