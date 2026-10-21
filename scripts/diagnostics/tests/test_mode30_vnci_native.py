"""Typed TP2.0 raw primitives against independent synthetic driver storage."""
import unittest

from scripts.diagnostics.elm import ElmError
from scripts.diagnostics.mode30_transport import ChannelOffer, SOURCE_CHANNEL_OFFERS, HOST_PARAMETERS
from scripts.diagnostics.tests.test_mode50_vnci_native import RawDriver


class Mode30NativeTests(unittest.TestCase):
    def driver(self, *, setup=False, connect=True):
        driver=RawDriver();offer=next(o for o in SOURCE_CHANNEL_OFFERS if o.payload[0]==0x26)
        pair=(offer.tx_id,offer.rx_id) if setup else (0x541,0x302)
        driver.native.create_mode30_link(*pair,offer=offer,setup=setup,connect=connect)
        return driver,offer

    def test_all_four_source_setup_pairs_and_negotiated_pairs_are_read_back_before_connect(self):
        for offer in SOURCE_CHANNEL_OFFERS:
            for setup,pair in [(True,(offer.tx_id,offer.rx_id)),(False,(0x541,0x302))]:
                d=RawDriver();d.native.create_mode30_link(*pair,offer=offer,setup=setup)
                self.assertEqual(d.addresses[d.ids['CP_CanPhysReqId']],pair[0])
                self.assertEqual(d.addresses[d.ids['CP_CanRespUUDTId']],pair[1])
                self.assertEqual(d.addresses[999],12345)
                self.assertEqual(d.parameters['CP_RepeatReqCountApp'],0)
                self.assertEqual(d.parameters['CP_RawCanSequenceHandling'],0)
                self.assertEqual(d.calls[-1],'PDUConnect')
                self.assertEqual(d.primitives,[])

    def test_unknown_offer_bad_ids_and_wrong_setup_pair_never_create_a_native_link(self):
        source=next(iter(SOURCE_CHANNEL_OFFERS))
        other=ChannelOffer(0x200,0x201,bytes.fromhex('01C00010000301'))
        for tx,rx,offer,setup in [(0x200,0x201,other,True),(True,0x302,source,False),
                (0x800,0x302,source,False),(0,0x302,source,False),(0x302,0x302,source,False),
                (0x200,0x201,source,True),(0x541,0x302,source,1)]:
            d=RawDriver()
            with self.subTest(tx=tx,rx=rx),self.assertRaises(ElmError):
                d.native.create_mode30_link(tx,rx,offer=offer,setup=setup)
            self.assertEqual(d.calls,[])

    def test_setup_primitive_can_only_receive_or_send_its_exact_source_offer(self):
        d,offer=self.driver(setup=True)
        d.native.start_mode30_primitive()
        self.assertEqual(d.primitives[-1]['payload'],b'')
        self.assertEqual(d.primitives[-1]['expected']['pattern'],offer.rx_id.to_bytes(4,'big'))
        d.native.start_mode30_primitive(offer.payload)
        self.assertEqual(d.primitives[-1]['payload'],offer.tx_id.to_bytes(4,'big')+offer.payload)
        before=len(d.primitives)
        for frame in [HOST_PARAMETERS,b'\xA8',bytes.fromhex('10000322F100'),b'\xB1']:
            with self.assertRaisesRegex(ElmError,'frame-unqualified'):d.native.start_mode30_primitive(frame)
        self.assertEqual(len(d.primitives),before)

    def test_channel_control_and_single_read_frames_have_single_owned_transmits(self):
        d,_=self.driver()
        for frame in [HOST_PARAMETERS,b'\xA1'+HOST_PARAMETERS[1:],b'\xA8',b'\xB0',b'\xBF',
                bytes.fromhex('10000322F100'),bytes.fromhex('1F00021089'),bytes.fromhex('1100023E00')]:
            d.native.start_mode30_primitive(frame)
            r=d.primitives[-1]
            self.assertEqual(r['payload'],bytes.fromhex('00000541')+frame)
            self.assertEqual((r['send'],r['receive'],r['expected_count']),(1,0,0))
        self.assertEqual(d.tags,list(range(1,len(d.tags)+1)))
        d.native.start_mode30_primitive()
        r=d.primitives[-1]
        self.assertEqual((r['payload'],r['send'],r['receive']),(b'',0,-1))
        self.assertEqual(r['expected']['pattern'],bytes.fromhex('00000302'))

    def test_write_unknown_transport_and_malformed_frames_never_reach_the_driver(self):
        d,offer=self.driver()
        for frame in [offer.payload,b'',b'\xA3',b'\x90',b'\x00',b'\xB0\0',True,'B0',
                bytes.fromhex('1000032EF100'),bytes.fromhex('1000022701'),bytes.fromhex('1000021101'),
                bytes.fromhex('20000322F100'),bytes.fromhex('10000422F100'),bytes.fromhex('1000023E80'),
                bytes.fromhex('A00F00FF00FF'),bytes.fromhex('A10F00FF00FF')]:
            with self.subTest(frame=frame),self.assertRaisesRegex(ElmError,'frame-unqualified'):
                d.native.start_mode30_primitive(frame)
        self.assertEqual(d.primitives,[])

    def test_failed_readback_or_disconnected_raw_link_never_enables_send(self):
        for fail in ['parameter','table','unknown']:
            d=RawDriver();d.omit_param_set=fail=='parameter';d.omit_table_set=fail=='table';d.corrupt_unmodified_address=fail=='unknown'
            o=next(iter(SOURCE_CHANNEL_OFFERS))
            with self.assertRaises(ElmError):d.native.create_mode30_link(o.tx_id,o.rx_id,offer=o,setup=True)
            with self.assertRaisesRegex(ElmError,'link-not-configured'):d.native.start_mode30_primitive()
            self.assertNotIn('PDUConnect',d.calls)
            self.assertEqual(d.primitives,[])
        d,_=self.driver(connect=False)
        with self.assertRaisesRegex(ElmError,'link-not-configured'):d.native.start_mode30_primitive()
        self.assertEqual(d.primitives,[])

    def test_modes_cannot_use_each_others_primitive_entry(self):
        d,_=self.driver()
        with self.assertRaisesRegex(ElmError,'mode50-link-not-configured'):d.native.start_mode50_primitive()
        with self.assertRaisesRegex(ElmError,'requires-mode30-primitive'):d.native.start(b'\x22\xF1\0')
        self.assertEqual(d.primitives,[])
        d=RawDriver();d.native.create_mode50_link()
        with self.assertRaisesRegex(ElmError,'mode30-link-not-configured'):d.native.start_mode30_primitive()
        self.assertEqual(d.primitives,[])

    def test_absent_primitive_handle_never_triggers_a_second_send(self):
        d,_=self.driver();d.no_cop=True
        with self.assertRaisesRegex(ElmError,'primitive-handle-missing'):
            d.native.start_mode30_primitive(bytes.fromhex('10000322F100'))
        self.assertEqual(len(d.primitives),1)
