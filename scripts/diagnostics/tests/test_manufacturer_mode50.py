"""Mode50 source-session integration with explicit raw byte fixtures; no device IO."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from scripts.diagnostics import manufacturer_read as read
from scripts.diagnostics.mode50_transport import FLOW_CONTROL, request_frame
from scripts.diagnostics.realtime_preparation import dump,sha
from scripts.diagnostics.tests.test_manufacturer_read import bundle,request
from scripts.diagnostics.tests.test_realtime_preparation import field
from scripts.diagnostics.tests.test_mode50_transport import Port,lines,response_frames
from scripts.diagnostics.tests.test_vnci import ID
from scripts.diagnostics.tests.test_mode50_vnci import RawNative
from scripts.diagnostics.uds_source_read import MODE50_VERSIONS


class RawSourceNative(RawNative):
    def __init__(self,replies):super().__init__();self.replies=replies;self.ecu_payloads=self.sent
    def start_mode50_primitive(self,frame=None):
        if frame is not None and frame!=FLOW_CONTROL:
            hx=frame[2:2+frame[1]].hex().upper()
            self.responses.append(self.replies.get(hx,bytes((0x7F,frame[2],0x31))))
        return super().start_mode50_primitive(frame)


class ModelPort(Port):
    """Raw CAN/FC fixture preserving actual byte boundaries and sent requests."""
    def __init__(self,replies,address=0xF1):
        super().__init__();self.replies=replies;self.address=address;self.remaining=[];self.ecu_payloads=[]
        self.interrupt_request=None;self.interrupt_count=0
    def write(self,data):
        if data==b'ATI\r':self.writes.append(data);self.buf.extend(b'ELM327 v2.3\r>');return len(data)
        if data.startswith(b'AT') or data==b'\x1B':return super().write(data)
        self.writes.append(data);packet=bytes.fromhex(data.decode().strip())
        if packet==FLOW_CONTROL:
            if not self.remaining:raise AssertionError('flow control without a first frame')
            current=self.remaining[:16];del self.remaining[:16]
        else:
            assert len(packet)==8 and packet[0]==0x55 and 1<=packet[1]<=6
            hx=packet[2:2+packet[1]].hex().upper();assert packet==request_frame(bytes.fromhex(hx))
            self.ecu_payloads.append(hx)
            if hx==self.interrupt_request:
                self.interrupt_count+=1
                if self.interrupt_count==2:raise OSError('model disconnected')
            payload=self.replies.get(hx,bytes((0x7F,packet[2],0x31)))
            frames=response_frames(payload,self.address);current=frames[:1];self.remaining=frames[1:]
        self.buf.extend(lines(current)+b'>');return len(data)


def model_contract(pid):
    from scripts.diagnostics.tests.test_manufacturer_uds_source import model_contract as ordinary
    c=ordinary(5);c.update(ecuId=88,txId=0x754,rxId=0x7BE,
        transportAddressing={'kind':'porsche-mode50','transmitAddress':0x55},
        sourceCommands=['3E00','22F19E','22F1A2','22F1DF','1003'],
        identityConstraints={'versionRules':['SCHEINWERFER#30'+pid[-1]]},
        versionIdentityRequests=['22F19E','22F1A2'])
    c['identityRequests']=[r for r in c['identityRequests'] if r['requestHex']!='22F1A2']
    c['versionSelectors']=[dict(requestHex=hx,selector={'byteOffsetSigned':2,'bitOffsetByte':0,'formulaId':0},formula=
        'IDENTICAL:Standar,BitMask=0,BaseDataType=A_ASCIISTRING,BitLength='+str(size*8)+',Encoding=ISO-8859-2,HighLow=1,Radix=10,DataType=A_UNICODE2STRING,Precision=null;')
        for hx,size in [('22F19E',12),('22F1A2',3)]]
    return c


def model_replies(pid):
    rows={'3E00':b'\x7E\0','1003':b'\x50\x03','22F19E':bytes.fromhex('62F19E')+b'SCHEINWERFER',
        '22F1A2':bytes.fromhex('62F1A2')+('30'+pid[-1]).encode(),'22F1DF':bytes.fromhex('62F1DF01'),
        '22F102':bytes.fromhex('62F1022A07')}
    for r in model_contract(pid)['identityRequests']:
        hx=r['requestHex'];text={'22F189':'1234','22F1A3':'123','22F191':'12345678901',
            '22F187':'12345678902','22F190':'WP0ZZZ98ZES123456'}[hx]
        rows[hx]=bytes.fromhex('62'+hx[2:])+text.encode()
    return rows


class Mode50ManufacturerTests(unittest.TestCase):
    def run_case(self,pid=None,*,action='manufacturer-read',device='elm',mutate=None,retry=None,cycles=1):
        pid=pid or next(iter(MODE50_VERSIONS));ports=[];pdus=[]
        raw_native=RawSourceNative(model_replies(pid))
        def opened(*args,**kwargs):
            port=ModelPort(model_replies(pid))
            if mutate:mutate(port,len(ports))
            ports.append(port);return port,'synthetic'
        req={**request(), 'action':action,'ecuId':88,'profileId':pid,
            'cycleCount':cycles,'deviceId':ID if device=='vnci' else 'bt:001122334455',
            'parameterIds':[] if action=='manufacturer-identify' else ['a'*64,'b'*64]}
        if action=='manufacturer-identify':req['profileId']=next(iter(MODE50_VERSIONS))
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);profiles=[]
            for i,key in enumerate(MODE50_VERSIONS):
                p={'profileId':key,'generation':None,'ecuIds':[88], 'status':'definition-only',
                   'parameters':[field('a','22F102'),field('b','22F102',1)],'identity':[],'checklist':[]}
                if i==0:manifest=bundle(root,p)
                filename='profiles/'+str(i)*64+'.json';dump(root/filename,p)
                profiles.append(dict(profileId=key,ecuIds=[88],file=filename,sha256=sha(root/filename)))
            dump(root/'index.json',dict(profiles=profiles));manifest['outputs'][0]['sha256']=sha(root/'index.json');dump(root/'manifest.json',manifest)
            with patch('scripts.diagnostics.uds_source_read.source_contract',side_effect=lambda p,s:model_contract(p['profileId'])),\
                    patch('scripts.diagnostics.connection.open_selected_port',side_effect=opened) as opening,\
                    patch('scripts.diagnostics.vnci.NativeDpu',return_value=raw_native) as native:
                out=read.read_manufacturer(req,root=root,wait_retry=retry,on_pdu=pdus.append)
                if device=='vnci':opening.assert_not_called();native.assert_called_once()
                else:native.assert_not_called()
        return out,[raw_native] if device=='vnci' else ports,pdus

    def test_four_source_versions_read_parameters_with_raw_address_and_flow_control(self):
        for pid in MODE50_VERSIONS:
            out,ports,pdus=self.run_case(pid,cycles=2)
            self.assertTrue(out['ok'],out.get('error'));self.assertEqual([s['value'] for s in out['samples']],[42,7,42,7])
            self.assertEqual(ports[0].ecu_payloads[:5],model_contract(pid)['sourceCommands'])
            self.assertEqual(ports[0].ecu_payloads.count('22F102'),2)
            self.assertTrue(out['transport']['closed']);self.assertEqual(ports[0].closed,1)
            self.assertTrue(any(e['pduHex']=='62F1022A07' for e in pdus));self.assertFalse(out['liveVerified'])

    def test_identifies_actual_version_without_measurement_requests(self):
        for pid in MODE50_VERSIONS:
            out,ports,_=self.run_case(pid,action='manufacturer-identify')
            self.assertTrue(out['ok'],out.get('error'));self.assertEqual(out['identifiedProfileId'],pid)
            self.assertEqual(out['samples'],[]);self.assertNotIn('22F102',ports[0].ecu_payloads)
            self.assertEqual(out['requestCount'],0)

    def test_vnci_uses_source_raw_can_for_identity_and_two_read_cycles(self):
        for pid in MODE50_VERSIONS:
            for action in ('manufacturer-read','manufacturer-identify'):
                out,ports,pdus=self.run_case(pid,action=action,device='vnci',cycles=1 if action=='manufacturer-identify' else 2)
                self.assertTrue(out['ok'],out);self.assertTrue(out['transport']['closed'])
                self.assertEqual(ports[0].links,[('ISO_11898_RAW',0x754,0x7BE)])
                self.assertEqual(ports[0].closed,1);self.assertTrue(all(pdu['transportKind']=='d-pdu-mode50' for pdu in pdus))
                if action=='manufacturer-identify':
                    self.assertEqual(out['identifiedProfileId'],pid);self.assertEqual(out['samples'],[])
                    self.assertNotIn('22F102',ports[0].sent)
                else:self.assertEqual([s['value'] for s in out['samples']],[42,7,42,7])

    def test_wrong_version_does_not_start_session_or_read_measurements(self):
        def change(port,index):port.replies['22F1A2']=bytes.fromhex('62F1A2')+b'999'
        out,ports,_=self.run_case(mutate=change)
        self.assertEqual(out['error'],'identity-mismatch');self.assertNotIn('1003',ports[0].ecu_payloads)
        self.assertNotIn('22F102',ports[0].ecu_payloads)

    def test_reconnect_binds_observed_address_in_addition_to_full_identity_pdus(self):
        for address_change in (False,True):
            def change(port,index):
                if index:port.address=0x55 if address_change else 0xF1
                else:port.interrupt_request='22F102'
            out,ports,_=self.run_case(mutate=change,retry=lambda *args:True,cycles=3)
            self.assertEqual(len(ports),2);self.assertTrue(out['transport']['closed'])
            if address_change:
                self.assertEqual(out['error'],'identity-mismatch');self.assertNotIn('22F102',ports[1].ecu_payloads)
            else:self.assertTrue(out['ok'],out.get('error'))

    def test_unavailable_parameter_isolated_without_request_replay(self):
        def change(port,index):port.replies['22F102']=bytes.fromhex('7F2231')
        out,ports,_=self.run_case(mutate=change)
        self.assertTrue(out['ok'],out.get('error'));self.assertEqual(len(out['unavailableParameters']),2)
        self.assertEqual(ports[0].ecu_payloads.count('22F102'),1)


if __name__=='__main__':unittest.main()
