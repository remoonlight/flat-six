import copy
import hashlib
import io
import json
from contextlib import redirect_stdout,ExitStack
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from scripts.diagnostics.selector_source_gap import SelectorReadSourceGap,main
from scripts.diagnostics.x431_command_origin import _parse_command_catalog,_lookup_command
from scripts.diagnostics.x431_version_response import VERSION_GUARDS


def link(index,name,ids,*,mode=0x10,subtype=3,complete=True):
    return {'index':index,'serviceOff':200,'commOff':100,'service':{'variants':[{'name':name}]},
            'comm':{'protocolKind':mode,'class':'static-source-confirmed','consumedEnd':200 if complete else 199,
                    'pairs':[{'mode':subtype,'params':ids}]}}


class SourceGapTests(unittest.TestCase):
    def inspector(self,systems,matches=True):
        audit=SelectorReadSourceGap.__new__(SelectorReadSourceGap)
        audit.parsed={'systems':systems};audit.catalog={'namespace':'COMMAND.GAG','fixture':True}
        audit.by_request={'22F19E':[{'commandId':1,'flagByte':1}]} if matches else {}
        return audit

    def system(self,ecu,*links):return {'systemKey':0xd00|ecu,'links':list(links)}

    def guards(self,result):
        for key,value in VERSION_GUARDS.items():self.assertEqual(result[key],value)
        self.assertFalse(result['otherSourceOccurrencesBorrowed'])
        self.assertFalse(result['missingSelectorCoverageRepaired'])
        self.assertIsNone(result['selectedCurrentLinkIndex'])

    def test_other_ecu_command_presence_never_repairs_current_selector(self):
        audit=self.inspector([self.system(47,link(0,'LED',[2])),self.system(1,link(0,'DME',[1,1]))])
        result=audit.reference(47,'LED','22f19e')
        self.assertEqual(result['status'],'read-command-only-in-other-ECUs')
        self.assertEqual(result['sourceOccurrenceCount'],2)
        self.assertEqual(result['currentSourceOccurrenceCount'],0)
        self.assertEqual([row['requestIndex'] for row in result['sourceOccurrences']],[0,1])
        self.assertFalse(any(row['eligibleCurrentSourceOccurrence'] for row in result['sourceOccurrences']))
        self.guards(result)

    def test_same_ecu_other_named_link_is_retained_without_fallback(self):
        audit=self.inspector([self.system(47,link(0,'LED',[2]),link(1,'OTHER',[1]))])
        result=audit.reference(47,'LED','22F19E')
        self.assertEqual(result['status'],'read-command-only-in-other-current-ECU-links')
        self.assertEqual(result['currentSourceOccurrenceCount'],0);self.guards(result)
        result=audit.reference(47,'led','22F19E')
        self.assertEqual(result['status'],'current-exact-source-name-missing')

    def test_duplicate_links_and_duplicate_name_inside_one_link_are_ambiguous(self):
        for links in ([link(0,'LED',[1]),link(1,'LED',[1])],[link(0,'LED',[1])]):
            links=copy.deepcopy(links)
            if len(links)==1:links[0]['service']['variants'].append({'name':'LED'})
            result=self.inspector([self.system(47,*links)]).reference(47,'LED','22F19E')
            self.assertEqual(result['status'],'current-source-link-ambiguous')
            self.assertEqual(result['currentExactSourceNameMatchCount'],2);self.guards(result)

    def test_unreviewed_caller_and_record_extent_cannot_be_eligible(self):
        for row in (link(0,'LED',[1],mode=0x60),link(0,'LED',[1],complete=False)):
            result=self.inspector([self.system(47,row)]).reference(47,'LED','22F19E')
            self.assertEqual(result['status'],'current-source-occurrence-layout-or-caller-unreviewed')
            self.assertEqual(result['eligibleCurrentSourceOccurrenceCount'],0);self.guards(result)

    def test_even_exact_source_read_keeps_runtime_vehicle_and_repair_guards_false(self):
        result=self.inspector([self.system(47,link(0,'LED',[1]))]).reference(47,'LED','22F19E')
        self.assertEqual(result['status'],'current-source-read-occurrence-present')
        self.assertEqual(result['eligibleCurrentSourceOccurrenceCount'],1);self.guards(result)

    def test_catalog_absence_and_unreferenced_command_are_distinct(self):
        systems=[self.system(47,link(0,'LED',[2]))]
        result=self.inspector(systems,matches=False).reference(47,'LED','22F19E')
        self.assertEqual(result['status'],'read-command-absent-from-pinned-catalog')
        result=self.inspector(systems).reference(47,'LED','22F19E')
        self.assertEqual(result['status'],'read-command-present-without-family-SYS_DATA-reference')

    def test_exact_read_schema_rejects_writes_substrings_and_guessable_identity(self):
        audit=self.inspector([])
        for request in ('2EF19E00','14FFFFFF','3101','22F19E00','22 F19E','X22F19E','22F19','zz',None):
            with self.assertRaises(ValueError):audit.reference(47,'LED',request)
        for ecu,name in ((True,'LED'),(-1,'LED'),(256,'LED'),(47,''),(47,'LED\0'),(47,'灯'),(47,'L'*241)):
            with self.assertRaises(ValueError):audit.reference(ecu,name,'22F19E')

    def test_constructor_checks_sources_before_parsing_or_scanning(self):
        with patch('scripts.diagnostics.selector_source_gap.parse_payload',side_effect=AssertionError('must not parse')):
            with self.assertRaisesRegex(ValueError,'SYS_DATA-unreviewed'):SelectorReadSourceGap(b'unknown',b'unknown')
        with self.assertRaisesRegex(ValueError,'language-source-unreviewed'):_parse_command_catalog(b'unknown')
        for value in (True,-1,0x100000000,'1'):
            with self.assertRaisesRegex(ValueError,'command-id-invalid'):_lookup_command(b'unknown',value)

    def construct_fixture(self,records):
        stack=ExitStack();self.addCleanup(stack.close)
        logical=b'explicit fixture only';keys=[i.to_bytes(4,'big') for i in range(1,len(records)+1)]
        stack.enter_context(patch('scripts.diagnostics.selector_source_gap.SYS_DATA_LOGICAL_SHA256',hashlib.sha256(logical).hexdigest()))
        stack.enter_context(patch('scripts.diagnostics.selector_source_gap.parse_payload',return_value={'ok':True,'familyIdQueried':13,'systems':[]}))
        stack.enter_context(patch('scripts.diagnostics.x431_command_origin._parse_command_catalog',return_value=({'offset':100},b'',{'ctx':bytearray(512),'count':len(records)}, {},keys)))
        stack.enter_context(patch('scripts.diagnostics.x431_command_origin.decode_record',side_effect=lambda st,i:(keys[i],records[i])))
        return SelectorReadSourceGap(logical,b'fixture'),keys

    def test_catalog_scan_requires_exact_complete_read_descriptor_not_substring(self):
        audit,_=self.construct_fixture([bytes.fromhex(value) for value in ('010322F19E','000322F19E','010422F19E','01043122F19E','010222F1')])
        self.assertEqual(len(audit.records),5);self.assertEqual(len(audit.by_request['22F19E']),2)
        self.assertEqual([row['flagByte'] for row in audit.by_request['22F19E']],[1,0])
        self.assertNotIn('3122F19E',audit.by_request)

    def test_catalog_decoded_key_mismatch_stops_scan(self):
        audit,keys=self.construct_fixture([bytes.fromhex('010322F19E')])
        with patch('scripts.diagnostics.x431_command_origin.decode_record',return_value=(b'wrong',b'')):
            with self.assertRaisesRegex(ValueError,'key-mismatch'):SelectorReadSourceGap(b'explicit fixture only',b'fixture')

    def test_cli_has_explicit_sources_and_reports_only_offline_result(self):
        audit=self.inspector([self.system(47,link(0,'LED',[2]))])
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);(root/'sys').write_bytes(b'fixture');(root/'lang').write_bytes(b'fixture')
            out=io.StringIO()
            with patch('scripts.diagnostics.selector_source_gap.SelectorReadSourceGap',return_value=audit),redirect_stdout(out):
                self.assertEqual(main(['--logical-sys-data',str(root/'sys'),'--language-ggp',str(root/'lang'),'--ecu-id','47','--variant-name','LED','--request','22F19E']),0)
            self.guards(json.loads(out.getvalue()))


if __name__=='__main__':unittest.main()
