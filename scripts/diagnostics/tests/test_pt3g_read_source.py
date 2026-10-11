"""Synthetic native results and MDF fixtures; never load a diagnostic driver."""
import ctypes as c
import hashlib
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET

from scripts.diagnostics import pt3g_read_source as source
from scripts.diagnostics.catalog import load_catalog
from scripts.diagnostics.elm import ElmError


def fixture():
    doc=ET.Element('MDF')
    for name,id in (('HI',0),('LOW',1)):
        e=ET.SubElement(doc,'PINTYPE',EID=name)
        ET.SubElement(e,'ID').text=str(id);ET.SubElement(e,'SHORT_NAME').text=name
    module=ET.SubElement(doc,'MODULETYPE')
    ET.SubElement(module,'ID').text='11';ET.SubElement(module,'SHORT_NAME').text='PT3G_VCI'
    bus=ET.SubElement(doc,'BUSTYPE',EID='bus')
    ET.SubElement(bus,'ID').text='0';ET.SubElement(bus,'SHORT_NAME').text='ISO_11898_2_DWCAN'
    names=list(source.COMMON)+['CP_RequestAddrMode']+list(source.UNIQUE)+['CP_CanPhysReqId','CP_CanRespUSDTId']
    for index,name in enumerate(names):
        param=ET.SubElement(doc,'COMPARAM',EID=name)
        for tag,text in dict(ID=str(index),SHORT_NAME=name,DATA_TYPE='PDU_PT_UNUM32',
            CLASS='UNIQUE_ID' if name in source.UNIQUE or name in ('CP_CanPhysReqId','CP_CanRespUSDTId') else 'COM',LAYER='PHYSICAL' if name=='CP_Baudrate' else 'TRANSPORT',DEFAULT_VALUE='0').items():
            ET.SubElement(param,tag).text=text
    for index,(_,resource_name,protocol_name,_,_) in enumerate(source.RESOURCES):
        eid='resource'+str(index);protocol_eid='protocol'+str(index)
        ET.SubElement(module,'RESOURCE',IDREF=eid)
        resource=ET.SubElement(doc,'RESOURCE',EID=eid)
        ET.SubElement(resource,'SHORT_NAME').text=resource_name;ET.SubElement(resource,'ID').text=str(100+index)
        ET.SubElement(resource,'BUSTYPE',IDREF='bus');ET.SubElement(resource,'PROTOCOL',IDREF=protocol_eid)
        for number,type in ((6,'HI'),(14,'LOW')):
            pin=ET.SubElement(resource,'PIN_ON_MODULE');ET.SubElement(pin,'PIN_ON_MODULE').text=str(number);ET.SubElement(pin,'PINTYPE',IDREF=type)
        protocol=ET.SubElement(doc,'PROTOCOL',EID=protocol_eid)
        ET.SubElement(protocol,'SHORT_NAME').text=protocol_name;ET.SubElement(protocol,'ID').text=str(index+1)
        for name in names:
            if name=='CP_Baudrate' and index:continue
            ref=ET.SubElement(bus if name=='CP_Baudrate' else protocol,'COMPARAM_REF')
            ET.SubElement(ref,'COMPARAM',IDREF=name)
            for tag,text in (('DEFAULT_VALUE','1' if name=='CP_TesterPresentHandling' else '0'),('MIN_VALUE','0'),('MAX_VALUE','4294967295')):ET.SubElement(ref,tag).text=text
    return doc


class Pt3gReadSourceTests(unittest.TestCase):
    def contract(self,doc=None):
        raw=ET.tostring(fixture() if doc is None else doc)
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/'MDF.xml';path.write_bytes(raw)
            with patch.dict(source.SUPPORT_SHA256,{'MDF_VCI.xml':hashlib.sha256(raw).hexdigest()}):
                return source.read_source_contract(path)

    def test_named_routes_and_parameter_candidates_keep_all_vehicle_gates_closed(self):
        out=self.contract();self.assertEqual(len(out['routes']),3);self.assertEqual(len(out['objectQueries']),25)
        for route in out['routes']:
            settings={r['name']:r for r in route['parameters']}
            self.assertEqual(settings['CP_Baudrate']['candidateValue'],500000)
            self.assertEqual(settings['CP_TesterPresentHandling']['defaultValue'],'1')
            self.assertEqual(settings['CP_TesterPresentHandling']['candidateValue'],0)
            self.assertEqual(settings['CP_RepeatReqCountApp']['candidateValue'],0)
            self.assertEqual(settings['CP_RepeatReqCountTrans']['candidateValue'],0)
            self.assertTrue(all(not p['applied'] for p in settings.values()))
            self.assertEqual({k:route[k] for k in source.GUARDS},source.GUARDS)
        catalog={p['ecu'].lower():p for p in load_catalog()['profiles'] if p['id'] in ('porsche-981-2014-dme','porsche-981-2014-gateway')}
        for route in out['routes'][:2]:
            self.assertEqual((route['txId'],route['rxId']),(catalog[route['purpose']]['txId'],catalog[route['purpose']]['rxId']))

    def test_unreviewed_file_rejected_before_xml_parse(self):
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/'MDF.xml';path.write_bytes(b'<bad')
            with patch.object(source.ET,'fromstring') as parse:
                with self.assertRaisesRegex(ValueError,'source-unreviewed'):source.read_source_contract(path)
            parse.assert_not_called()

    def test_conflict_group_alone_does_not_authorize_module_resource(self):
        doc=fixture();module=doc.find('MODULETYPE');resource=module.find('RESOURCE');module.remove(resource)
        ET.SubElement(module,'RESOURCE_CONFLICT_GROUP').append(resource)
        with self.assertRaisesRegex(ValueError,'resource-not-in-module'):self.contract(doc)

    def test_wrong_pin_missing_parameter_wrong_class_and_narrow_bounds_rejected(self):
        doc=fixture();doc.find('RESOURCE/PIN_ON_MODULE/PIN_ON_MODULE').text='8'
        with self.assertRaisesRegex(ValueError,'pin-mismatch'):self.contract(doc)
        doc=fixture();doc.find('PROTOCOL/COMPARAM_REF/COMPARAM').set('IDREF','missing')
        with self.assertRaisesRegex(ValueError,'reference-missing'):self.contract(doc)
        for name,tag,value in (('CP_CanPhysReqId','CLASS','COM'),('CP_Baudrate','DATA_TYPE','PDU_PT_BYTEFIELD')):
            doc=fixture();doc.find(f"COMPARAM[@EID='{name}']/{tag}").text=value
            with self.assertRaisesRegex(ValueError,'contract-invalid'):self.contract(doc)
        doc=fixture();doc.find('BUSTYPE/COMPARAM_REF/MAX_VALUE').text='250000'
        with self.assertRaisesRegex(ValueError,'contract-invalid'):self.contract(doc)

    def test_duplicate_objects_and_duplicate_parameter_refs_rejected(self):
        doc=fixture();ET.SubElement(doc,'COMPARAM',EID='bus')
        with self.assertRaisesRegex(ValueError,'object-ambiguous'):self.contract(doc)
        doc=fixture();protocol=doc.find('PROTOCOL');protocol.append(ET.fromstring(ET.tostring(protocol.find('COMPARAM_REF'))))
        with self.assertRaisesRegex(ValueError,'parameter-ambiguous'):self.contract(doc)

    def test_probe_cannot_connect_load_link_send_set_params_or_query_unknown_names(self):
        native=source._ReadObjectProbe.__new__(source._ReadObjectProbe)
        native._read_objects={(0x8021,b'ISO_15765_3_on_ISO_15765_2')}
        for name,args in [('PDUModuleConnect',(1,)),('PDUCreateComLogicalLink',()),('PDUStartComPrimitive',()),
                          ('PDUSetComParam',()),('PDUIoCtl',()),('PDUGetObjectId',(0x8021,b'UNKNOWN',None)),
                          ('PDUGetObjectId',(True,b'ISO_15765_3_on_ISO_15765_2',None))]:
            with self.assertRaises(ElmError):native.call(name,*args)

    def test_runtime_mismatch_undefined_and_api_error_are_distinct_missing_evidence(self):
        contract=self.contract()
        class Fake:
            problem=None
            def call(self,name,kind,label,pointer):
                row=next(r for r in contract['objectQueries'] if r['objectType']==kind and r['name'].encode()==label)
                if self.problem=='error':raise ElmError('native-failure')
                c.cast(pointer,c.POINTER(source.U32)).contents.value=source.UNDEF if self.problem=='undefined' else row['sourceId']+(self.problem=='mismatch')
        native=Fake();rows=source._query_objects(native,contract);self.assertTrue(all(r['sourceIdMatchesRuntime'] for r in rows))
        for problem in ('error','undefined','mismatch'):
            native.problem=problem;rows=source._query_objects(native,contract)
            self.assertTrue(all(not r['sourceIdMatchesRuntime'] for r in rows))
            self.assertTrue(all(bool(r['error'])==(problem!='mismatch') for r in rows))


if __name__=='__main__':unittest.main()
