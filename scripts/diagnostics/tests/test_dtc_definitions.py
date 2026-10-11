import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from scripts.diagnostics.dtc_definitions import dtc_definition, decode_observation, prepared_profile
from scripts.diagnostics.decode import decode_payload
from scripts.diagnostics.offline_match import ANCHORS
from scripts.diagnostics.all_unit_collection import build_all_unit_pack


def variant():
    source={'sha256':ANCHORS['data'],'module':'M','variant':'VersionA','offset':100}
    rows=[{'at':110,'numeric_dtc_hex':'0000C447','code':'U0447','text':'source label',
           'statusKind':2,'text_id_hex':'F1000001','source':source},
          {'at':120,'numeric_dtc_hex':'00C13002','code':'C13002','text':None,
           'statusKind':9,'text_id_hex':'F1000002','source':source}]
    return {'profile_id':'version-a','module':'M','name':'VersionA','generation':None,
            'on_target_menu':True,'pool_records':{'dtc':{'service_header':24,'count':2,'records':rows}}}


def profile(v=None):
    v=variant() if v is None else v
    return {'profileId':v['profile_id'],'module':v['module'],'variant':v['name'],'ecuIds':[9],
        'dtcDefinition':dtc_definition(v),'diagnosticSources':[{'requestedEcuId':9,'variantName':v['name'],
            'links':[{'linkIndex':0,'dtc':{'readCommandSlots':[
                {'role':'dtcRead','wireCandidateHex':'1800FF00'},
                {'role':'dtcRead','wireCandidateHex':'190208'},
                {'role':'dtcClear','wireCandidateHex':'14FFFFFF'}]}}]}]}


class DefinitionTests(unittest.TestCase):
    def test_raw_header_and_status_kind_cannot_select_uds_format_or_live_status(self):
        p=profile();definition=p['dtcDefinition']
        self.assertEqual(definition['sourceHeaderWord'],0x18)
        self.assertFalse(definition['poolHeaderIsWireReadSidQualified'])
        result=decode_observation(p,9,0,'190208','590219C1300209',source_kind='simulation')
        self.assertTrue(result['ok'],result)
        row=result['records'][0]
        self.assertEqual(row['statusHex'],'09');self.assertEqual(row['definitionMatches'][0]['sourceStatusKind'],9)
        self.assertIsNone(row['definitionMatches'][0]['liveStatusByte'])
        self.assertFalse(row['liveStatusMeaningQualified']);self.assertFalse(result['ecuVersionQualified'])
        self.assertEqual(row['sourceTextState'],'missing')

    def test_known_numeric_reference_keeps_observed_bytes_and_source_labels_separate(self):
        result=decode_observation(profile(),9,0,'1800FF00','5801C44728',source_kind='historical-capture')
        self.assertTrue(result['ok']);row=result['records'][0]
        self.assertEqual(row['dtcHex'],'C447');self.assertEqual(row['displayCode'],'U0447')
        self.assertEqual(row['sourceText'],'source label');self.assertFalse(result['dictionaryTextIsObservedVehicleText'])
        self.assertEqual(row['statusHex'],'28');self.assertFalse(result['freshReadProven'])

    def test_duplicate_numeric_keys_are_ambiguous_even_for_identical_labels(self):
        v=variant();v['pool_records']['dtc']['records'].append(v['pool_records']['dtc']['records'][0].copy())
        result=decode_observation(profile(v),9,0,'1800FF00','5801C44728',source_kind='imported-unqualified')
        self.assertTrue(result['ok']);row=result['records'][0]
        self.assertEqual(len(row['definitionMatches']),2);self.assertIsNone(row['sourceText'])
        self.assertEqual(row['definitionBindingStatus'],'source-number-ambiguous-or-unresolved')

    def test_unknown_number_never_uses_another_code_or_base_version(self):
        result=decode_observation(profile(),9,0,'1800FF00','5801FFFF28',source_kind='simulation')
        self.assertTrue(result['ok']);self.assertEqual(result['records'][0]['definitionBindingStatus'],'source-number-missing')
        self.assertIsNone(result['records'][0]['sourceText'])
        v=variant();v['pool_records']['dtc']['records'][0]['source']={**v['pool_records']['dtc']['records'][0]['source'],'variant':'BaseSys'}
        with self.assertRaisesRegex(ValueError,'source-mismatch'):dtc_definition(v)

    def test_wrong_ecu_link_request_definition_or_live_claim_is_rejected(self):
        for ecu,link,request,kind in [(1,0,'1800FF00','simulation'),(9,1,'1800FF00','simulation'),
                (9,0,'1902FF','simulation'),(9,0,'14FFFFFF','simulation'),(9,0,'1800FF00','live')]:
            self.assertFalse(decode_observation(profile(),ecu,link,request,'5801C44728',source_kind=kind)['ok'])
        p=profile();p['dtcDefinition']['records'][0]['text']='changed'
        self.assertEqual(decode_observation(p,9,0,'1800FF00','5801C44728',source_kind='simulation')['error'],
                         'dtc-definition-hash-mismatch')

    def test_direct_decoder_rejects_wrong_positive_sid_subfunction_and_pending(self):
        for kind,data in [('kwp-dtc-18','5701C44728'),('kwp-dtc-18','5901C44728'),
                ('uds-dtc-19-02','590119C1300209'),('uds-dtc-19-02','580219C1300209'),
                ('uds-dtc-19-02','7F1978')]:
            result=decode_payload(data,{'type':kind})
            self.assertFalse(result['ok']);self.assertEqual(result['records'],[])

    def test_truncated_and_negative_response_keeps_raw_but_has_no_fault_records(self):
        for request,response in [('190208','590219C130'),('1800FF00','5802C44728'),('190208','7F1931')]:
            result=decode_observation(profile(),9,0,request,response,source_kind='simulation')
            self.assertFalse(result['ok']);self.assertEqual(result['records'],[])
            self.assertEqual(result['rawResponseHex'],response)

    def test_all_unit_export_and_hash_verified_profile_keep_full_dtc_records(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);v=variant();source=root/'variants';source.write_text(json.dumps(v))
            build_all_unit_pack(root/'pack',variants=source,expected_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),
                coverage={'menu_ecus':[{'ecu_id':9,'dsn_modules':['M']}]})
            manifest_path=root/'pack/manifest.json';manifest=json.loads(manifest_path.read_text())
            # Synthetic manifest fixture: only the original-source pin is replaced.
            manifest['inputs'][0]['sha256']=ANCHORS['variants'];manifest_path.write_text(json.dumps(manifest))
            exported=prepared_profile(root/'pack','version-a')
            self.assertEqual(exported['dtcDefinition']['definitionCount'],2)
            self.assertEqual(exported['dtcDefinition']['records'][1]['textState'],'missing')
            path=root/'pack/profiles'/next((root/'pack/profiles').iterdir()).name
            path.write_text('{}')
            with self.assertRaisesRegex(ValueError,'profile-hash-mismatch'):prepared_profile(root/'pack','version-a')


if __name__=='__main__':unittest.main()
