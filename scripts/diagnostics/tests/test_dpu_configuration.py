"""Production D-PDU setup with independent synthetic driver storage; no I/O."""
import ctypes as c
import time
import unittest
from unittest.mock import patch

from scripts.diagnostics.elm import ElmError
from scripts.diagnostics import vnci
from scripts.diagnostics.tests.test_vnci import FakeNative, ID


class DriverStorage:
    """Each Get allocates a fresh item; changing it never changes the driver."""
    address_names=('CP_CanPhysReqId','CP_CanPhysReqFormat','CP_CanRespUSDTId',
                   'CP_CanRespUSDTFormat','CP_CanRespUUDTId')
    def __init__(self):
        self.ids={name:i for i,name in enumerate(vnci.UINT_PARAMETER_CLASSES,100)}
        self.names={v:k for k,v in self.ids.items()}
        self.parameters={name:1 for name,category in vnci.UINT_PARAMETER_CLASSES.items() if category!=6}
        self.addresses={self.ids[name]:0 for name in self.address_names}
        self.addresses[999]=12345 # unknown/unmodified parameter must survive
        self.unique_id=77;self.calls=[];self.allocated=[];self.alive=set()
        self.param_gets=0;self.table_gets=0;self.omit_param_set=False;self.omit_table_set=False
        self.bad_param=None;self.bad_table=None;self.no_link=False;self.no_cop=False;self.destroy_error=False
        self.reset_after_table_set=False
        self.corrupt_unmodified_address=False
        self.native=vnci.NativeDpu.__new__(vnci.NativeDpu)
        self.native.module=1;self.native.link=None;self.native.online=False;self.native.constructed=False
        self.native.call=self.call
        self.native.object_id=lambda kind,name:self.ids[name] if name in self.ids else 400
        self.native._directory=type('Directory',(),{'close':lambda _:None})()

    def _param(self,oid,value,category):
        scalar=vnci.U32(value);item=vnci.Param(0x1200,oid,0x105,category,c.cast(c.pointer(scalar),vnci.PTR))
        self.allocated.extend((scalar,item));return item

    def call(self,name,*args):
        self.calls.append(name)
        if name=='PDUCreateComLogicalLink':
            if not self.no_link:c.cast(args[4],c.POINTER(vnci.U32)).contents.value=9
        elif name=='PDUGetComParam':
            oid=args[2];key=self.names[oid];self.param_gets+=1
            if self.bad_param=='missing' and self.param_gets%2==0:return
            if self.bad_param=='error' and self.param_gets%2==0:raise ElmError('driver-readback-error')
            item=self._param(oid,self.parameters[key],vnci.UINT_PARAMETER_CLASSES[key])
            if self.bad_param=='id':item.id=999
            if self.bad_param=='class':item.category=6
            if self.bad_param=='type':item.data_type=0x104
            if self.bad_param=='null':item.data=None
            c.cast(args[3],c.POINTER(c.POINTER(vnci.Param)))[0]=c.pointer(item)
            self.alive.add(c.addressof(item))
        elif name=='PDUSetComParam':
            item=args[2].contents
            if not self.omit_param_set:
                self.parameters[self.names[item.id]]=c.cast(item.data,c.POINTER(vnci.U32)).contents.value
        elif name=='PDUGetUniqueRespIdTable':
            self.table_gets+=1
            if self.bad_table=='missing' and self.table_gets==2:return
            pairs=list(self.addresses.items())
            if self.bad_table=='duplicate':pairs.append(pairs[0])
            params=(vnci.Param*len(pairs))(*[self._param(oid,value,6) for oid,value in pairs])
            uid=self.unique_id if self.bad_table!='unique-id' or self.table_gets==1 else 55
            entry=vnci.UniqueData(uid,len(params),params);entries=(vnci.UniqueData*1)(entry)
            table=vnci.UniqueTable(0x1700,1,entries)
            if self.bad_table=='class':params[0].category=3
            if self.bad_table=='type':params[0].data_type=0x104
            if self.bad_table=='unknown-type':params[-1].data_type=0x104
            if self.bad_table=='ids' and self.table_gets==2:params[-1].id=998
            self.allocated.extend((params,entry,entries,table))
            c.cast(args[2],c.POINTER(c.POINTER(vnci.UniqueTable)))[0]=c.pointer(table)
            self.alive.add(c.addressof(table))
        elif name=='PDUSetUniqueRespIdTable':
            if not self.omit_table_set:
                entry=args[2].contents.data[0];self.unique_id=entry.id
                self.addresses={p.id:c.cast(p.data,c.POINTER(vnci.U32)).contents.value for p in entry.params[:entry.count]}
                if self.reset_after_table_set:self.parameters['CP_TesterPresentHandling']=1
                if self.corrupt_unmodified_address:self.addresses[999]=99999
        elif name=='PDUDestroyItem':
            self.alive.remove(c.addressof(args[0].contents))
            if self.destroy_error:raise ElmError('destroy-item-error')
        elif name=='PDUStartComPrimitive':
            if not self.no_cop:c.cast(args[7],c.POINTER(vnci.U32)).contents.value=19


class DpuConfigurationTests(unittest.TestCase):
    def assert_stopped_before_connect_and_send(self,driver):
        self.assertNotIn('PDUConnect',driver.calls)
        self.assertNotIn('PDUStartComPrimitive',driver.calls)
        self.assertFalse(driver.native.online)
        with self.assertRaisesRegex(ElmError,'link-not-configured'):driver.native.start(b'\x01\x00')
        self.assertEqual(driver.alive,set())

    def test_three_read_routes_verify_all_settings_before_connect(self):
        for protocol,tx,rx in (('ISO_14230_3_on_ISO_15765_2',0x7e0,0x7e8),
                              ('ISO_15765_3_on_ISO_15765_2',0x710,0x77a),
                              ('ISO_OBD_on_ISO_15765_4',0x7df,0x7e8)):
            driver=DriverStorage();driver.native.create_link(protocol,tx,rx)
            self.assertEqual(driver.calls[-1],'PDUConnect');self.assertTrue(driver.native.online)
            self.assertEqual(driver.param_gets,33);self.assertEqual(driver.table_gets,2)
            self.assertEqual(driver.parameters['CP_RequestAddrMode'],2 if tx==0x7df else 1)
            for name in ('CP_TesterPresentHandling','CP_RepeatReqCountApp','CP_RepeatReqCountTrans',
                         'CP_RC21Handling','CP_RC23Handling'):self.assertEqual(driver.parameters[name],0)
            self.assertEqual(driver.addresses[driver.ids['CP_CanPhysReqId']],0x7e0 if tx==0x7df else tx)
            self.assertEqual(driver.addresses[driver.ids['CP_CanRespUSDTId']],rx)
            self.assertEqual(driver.addresses[999],12345);self.assertEqual(driver.unique_id,1)
            self.assertEqual(driver.alive,set());driver.native.close()

    def test_silent_setter_failure_is_detected_using_fresh_storage(self):
        driver=DriverStorage();driver.omit_param_set=True
        with self.assertRaisesRegex(ElmError,'param-readback-mismatch'):driver.native.create_link('protocol',0x7e0,0x7e8)
        self.assertEqual(driver.param_gets,2);self.assert_stopped_before_connect_and_send(driver)

    def test_parameter_identity_type_class_missing_and_failed_read_stop(self):
        for problem in ('id','class','type','null','missing','error'):
            with self.subTest(problem=problem):
                driver=DriverStorage();driver.bad_param=problem
                with self.assertRaises(ElmError):driver.native.create_link('protocol',0x7e0,0x7e8)
                self.assert_stopped_before_connect_and_send(driver)

    def test_later_setting_cannot_silently_reenable_tester_present(self):
        driver=DriverStorage();driver.reset_after_table_set=True
        with self.assertRaisesRegex(ElmError,'param-readback-mismatch:CP_TesterPresentHandling'):
            driver.native.create_link('protocol',0x7e0,0x7e8)
        self.assert_stopped_before_connect_and_send(driver)

    def test_unqualified_parameter_values_stop_before_API_calls(self):
        for name,value in (('unknown',0),('CP_CanPhysReqId',0),('CP_Baudrate',True),
                           ('CP_Baudrate',-1),('CP_Baudrate',2**32),('CP_Baudrate',0.0)):
            driver=DriverStorage()
            with self.assertRaisesRegex(ElmError,'param-setting-not-qualified'):driver.native.set_param(name,value)
            self.assertEqual(driver.calls,[])

    def test_unique_setter_silent_failure_missing_and_wrong_readback_stop(self):
        for problem in ('silent','missing','unique-id','ids'):
            with self.subTest(problem=problem):
                driver=DriverStorage()
                if problem=='silent':driver.omit_table_set=True
                else:driver.bad_table=problem
                with self.assertRaises(ElmError):driver.native.create_link('protocol',0x7e0,0x7e8)
                self.assert_stopped_before_connect_and_send(driver)

    def test_duplicate_or_malformed_unique_parameters_are_never_set(self):
        for problem in ('duplicate','class','type','unknown-type'):
            driver=DriverStorage();driver.bad_table=problem
            with self.assertRaises(ElmError):driver.native.create_link('protocol',0x7e0,0x7e8)
            self.assertNotIn('PDUSetUniqueRespIdTable',driver.calls)
            self.assert_stopped_before_connect_and_send(driver)

    def test_unmodified_response_parameter_must_also_match_readback(self):
        driver=DriverStorage();driver.corrupt_unmodified_address=True
        with self.assertRaisesRegex(ElmError,'response-readback-mismatch'):
            driver.native.create_link('protocol',0x7e0,0x7e8)
        self.assert_stopped_before_connect_and_send(driver)

    def test_missing_link_handle_and_item_release_failure_never_connect(self):
        for problem in ('link','release'):
            driver=DriverStorage();driver.no_link=problem=='link';driver.destroy_error=problem=='release'
            with self.assertRaises(ElmError):driver.native.create_link('protocol',0x7e0,0x7e8)
            self.assert_stopped_before_connect_and_send(driver)

    def test_unconnected_rehearsal_cannot_send_and_unknown_primitive_handle_is_not_replayed(self):
        driver=DriverStorage();driver.native.create_link('protocol',0x7e0,0x7e8,connect=False)
        self.assert_stopped_before_connect_and_send(driver)
        driver=DriverStorage();driver.native.create_link('protocol',0x7e0,0x7e8);driver.no_cop=True
        with self.assertRaisesRegex(ElmError,'primitive-handle-missing'):driver.native.start(b'\x01\x00')
        self.assertEqual(driver.calls.count('PDUStartComPrimitive'),1)

    def test_failed_reconfiguration_clears_prior_permission_and_poison_stops_requests(self):
        for target in ('named','engine'):
            native=FakeNative();client=vnci.DpuClient(ID,native=native)
            client.configure_standard_engine(time.monotonic()+2)
            def fail(*args):raise ElmError('readback-mismatch')
            native.create_link=fail
            with self.assertRaisesRegex(ElmError,'readback-mismatch'):
                if target=='named':client.configure_pair('7E0','7E8',time.monotonic()+2)
                else:client.configure_standard_engine(time.monotonic()+2)
            self.assertEqual(client.allowed,set());self.assertFalse(client._configured)
            with self.assertRaisesRegex(ElmError,'client-poisoned'):client.request('0100',time.monotonic()+2)
            self.assertEqual(native.sent,[]);client.close_restore()

    def test_invalid_target_clears_permission_without_configuring_another_link(self):
        native=FakeNative();client=vnci.DpuClient(ID,native=native)
        client.configure_standard_engine(time.monotonic()+2);prior=len(native.links)
        with self.assertRaisesRegex(ElmError,'address-not-authorized'):client.configure_pair('711','712',time.monotonic()+2)
        self.assertEqual(client.allowed,set());self.assertEqual(len(native.links),prior)
        with self.assertRaisesRegex(ElmError,'not-configured'):client.request('0100',time.monotonic()+2)
        self.assertEqual(native.sent,[]);client.close_restore()


if __name__=='__main__':unittest.main()
