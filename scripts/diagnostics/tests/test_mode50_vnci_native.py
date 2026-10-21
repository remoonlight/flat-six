"""Pinned RAW CAN API setup with independently stored synthetic driver items."""
import ctypes as c
import unittest

from scripts.diagnostics import vnci
from scripts.diagnostics.elm import ElmError
from scripts.diagnostics.mode50_transport import request_frame, FLOW_CONTROL
from scripts.diagnostics.tests.test_dpu_configuration import DriverStorage


class RawDriver(DriverStorage):
    address_names=('CP_CanPhysReqId','CP_CanPhysReqFormat','CP_CanPhysReqExtAddr',
                   'CP_CanRespUUDTId','CP_CanRespUUDTFormat','CP_CanRespUUDTExtAddr')
    def __init__(self):
        super().__init__()
        self.resources=[];self.primitives=[];self.objects=[];self.tags=[]
        def object_id(kind,name):
            self.objects.append((kind,name))
            return self.ids[name] if name in self.ids else {'HI':501,'LOW':502,
                'ISO_11898_2_DWCAN':503,'ISO_11898_RAW':504}.get(name,505)
        self.native.object_id=object_id

    def call(self,name,*args):
        if name=='PDUCreateComLogicalLink':
            resource=c.cast(args[1],c.POINTER(vnci.Resource)).contents
            flags=c.cast(args[5],c.POINTER(vnci.Flag)).contents
            self.resources.append(dict(bus=resource.bus,protocol=resource.protocol,
                pins=[(p.number,p.type) for p in resource.pins[:resource.count]],
                flags=c.string_at(flags.data,flags.count)))
        elif name=='PDUStartComPrimitive':
            self.tags.append(args[6].value)
            ctrl=c.cast(args[5],c.POINTER(vnci.Control)).contents
            record=dict(type=args[2],payload=c.string_at(args[4],args[3]) if args[3] else b'',
                send=ctrl.send_cycles,receive=ctrl.receive_cycles,flags=c.string_at(ctrl.flags.data,ctrl.flags.count),
                expected_count=ctrl.expected_count)
            if ctrl.expected_count:
                expected=c.cast(ctrl.expected,c.POINTER(vnci.ExpectedResponse)).contents
                record['expected']=dict(type=expected.response_type,acceptance=expected.acceptance,
                    mask=c.string_at(expected.mask,expected.mask_size),pattern=c.string_at(expected.pattern,expected.mask_size),
                    ids=list(c.cast(expected.unique_ids,c.POINTER(vnci.U32))[:expected.unique_count]))
            self.primitives.append(record)
        return super().call(name,*args)


class Mode50NativeTests(unittest.TestCase):
    def test_raw_link_uses_source_pair_protocol_and_readback_without_sending(self):
        driver=RawDriver();driver.native.create_mode50_link()
        self.assertEqual(driver.resources,[dict(bus=503,protocol=504,pins=[(6,501),(14,502)],flags=b'\x80\0\0\0')])
        for name,value in [('CP_CanPhysReqId',0x754),('CP_CanRespUUDTId',0x7BE),('CP_CanPhysReqFormat',0),
                           ('CP_CanRespUUDTFormat',0),('CP_CanPhysReqExtAddr',0),('CP_CanRespUUDTExtAddr',0)]:
            self.assertEqual(driver.addresses[driver.ids[name]],value)
        for name,value in [('CP_Baudrate',500000),('CP_RepeatReqCountApp',0),('CP_Loopback',0),
                           ('CP_TransmitIndEnable',0),('CP_RawCanSequenceHandling',0),('CP_P2Max',8000000)]:
            self.assertEqual(driver.parameters[name],value)
        self.assertEqual(driver.addresses[999],12345);self.assertEqual(driver.unique_id,1)
        self.assertEqual(driver.alive,set());self.assertEqual(driver.primitives,[])
        self.assertNotIn('CP_TesterPresentHandling',[name for _,name in driver.objects])

    def test_parameter_and_filter_readback_failures_stop_before_connect(self):
        for failure in ['parameter','response','changed-unknown']:
            driver=RawDriver()
            driver.omit_param_set=failure=='parameter';driver.omit_table_set=failure=='response'
            driver.corrupt_unmodified_address=failure=='changed-unknown'
            with self.assertRaises(ElmError):driver.native.create_mode50_link()
            self.assertNotIn('PDUConnect',driver.calls);self.assertEqual(driver.primitives,[])
            self.assertFalse(driver.native._mode50_configured)

    def test_invalid_and_missing_table_items_are_never_accepted(self):
        for problem in ['missing','unique-id','ids','duplicate','class','type','unknown-type']:
            driver=RawDriver();driver.bad_table=problem
            with self.assertRaises(ElmError):driver.native.create_mode50_link()
            self.assertNotIn('PDUConnect',driver.calls);self.assertEqual(driver.primitives,[])

    def test_missing_link_or_item_release_error_cannot_enable_raw_primitives(self):
        for problem in ['link','destroy']:
            driver=RawDriver();driver.no_link=problem=='link';driver.destroy_error=problem=='destroy'
            with self.assertRaises(ElmError):driver.native.create_mode50_link()
            with self.assertRaisesRegex(ElmError,'mode50-link-not-configured'):driver.native.start_mode50_primitive()
            self.assertNotIn('PDUConnect',driver.calls)

    def test_receive_only_has_owned_filter_and_never_contains_tx_data(self):
        driver=RawDriver();driver.native.create_mode50_link();cop,tag=driver.native.start_mode50_primitive()
        self.assertEqual((cop,tag),(19,1));self.assertEqual(driver.tags,[tag])
        self.assertEqual(driver.primitives,[dict(type=0x8004,payload=b'',send=0,receive=-1,flags=b'\0'*4,
            expected_count=1,expected=dict(type=0,acceptance=1,mask=b'\xFF'*4,pattern=bytes.fromhex('000007BE'),ids=[1]))])
        self.assertEqual(c.sizeof(vnci.ExpectedResponse),48)

    def test_single_transmit_contains_exact_can_id_and_source_frame_without_repeat(self):
        driver=RawDriver();driver.native.create_mode50_link()
        for frame in [request_frame(b'\x3E\0'),request_frame(b'\x10\x03'),request_frame(b'\x22\xF1\x90'),
                      *[request_frame(bytes.fromhex(hx)) for hx in ['190208','19020C','19022C']],FLOW_CONTROL]:
            cop,tag=driver.native.start_mode50_primitive(frame)
            self.assertEqual(cop,19);self.assertEqual(tag,len(driver.primitives));self.assertEqual(driver.tags[-1],tag)
            self.assertEqual(driver.primitives[-1],dict(type=0x8004,payload=bytes.fromhex('00000754')+frame,
                send=1,receive=0,flags=b'\0'*4,expected_count=0))

    def test_write_security_unknown_control_and_malformed_frames_do_not_reach_api(self):
        driver=RawDriver();driver.native.create_mode50_link()
        for frame in [request_frame(p) for p in [b'\x2E\xF1\x00',b'\x27\x01',b'\x31\x01',b'\x11\x01',b'\x10\x89',b'\x19\x03\x08',b'\x19\x02\xff']]+[
                b'',b'\x55'*8,bytes.fromhex('55302005AAAAAAAA'),bytes.fromhex('55033E00AAAAAAAA'),
                bytes.fromhex('55023E0000000000'),'55023E00AAAAAAAA',True]:
            with self.assertRaisesRegex(ElmError,'mode50-frame-unqualified'):driver.native.start_mode50_primitive(frame)
        self.assertEqual(driver.primitives,[])

    def test_unconnected_or_missing_primitive_handle_never_replays(self):
        driver=RawDriver();driver.native.create_mode50_link(connect=False)
        with self.assertRaisesRegex(ElmError,'mode50-link-not-configured'):driver.native.start_mode50_primitive()
        self.assertEqual(driver.primitives,[])
        driver=RawDriver();driver.native.create_mode50_link();driver.no_cop=True
        with self.assertRaisesRegex(ElmError,'primitive-handle-missing'):driver.native.start_mode50_primitive(request_frame(b'\x3E\0'))
        self.assertEqual(len(driver.primitives),1)

    def test_normal_pdu_entry_never_transmits_on_a_raw_link(self):
        driver=RawDriver();driver.native.create_mode50_link()
        with self.assertRaisesRegex(ElmError,'raw-link-requires-mode50-primitive'):driver.native.start(b'\x22\xF1\x90')
        self.assertEqual(driver.primitives,[])
