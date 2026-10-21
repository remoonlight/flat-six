"""Powered-head-only bench lifecycle fixtures; no physical qualification."""
import contextlib
import io
import tempfile
import unittest
from pathlib import Path
from unittest import mock
from scripts.diagnostics import adapter_check as ac
from scripts.diagnostics.tests import test_adapter_check as bench
from scripts.diagnostics.tests import test_connection as hosts


class FamilyPort(bench._BenchSimPort):
    def __init__(self,identity):
        super().__init__('porsche-981-2014-dme');self.identity=identity;self.close_count=0;self.write_timeout=1
    def write(self,data):
        if data in (b'ATI\r',b'ATWS\r'):
            self._rx.extend((self.identity+'\r>').encode('ascii'));return len(data)
        return super().write(data)
    def close(self):self.close_count+=1;super().close()


class BenchSelectionTests(bench._LockIsolated):
    def test_error_brand_banner_never_reaches_configuration_or_reopen(self):
        for banner in ['ELM327 v2.3\rERROR', 'OBDKey 1.4\rNO DATA',
                       'vLinker FS\rSTOPPED', 'This device supports ELM327 commands']:
            with self.subTest(banner=banner), tempfile.TemporaryDirectory() as temp:
                port = FamilyPort(banner);factory = mock.Mock(return_value=port)
                out = ac.run_adapter_check(confirmed_powered_bench=True, out_dir=Path(temp),port_factory=factory)
            self.assertFalse(out['ok']);factory.assert_called_once()
            self.assertFalse(out['adapterCommandsVerified']['ATI'])
            self.assertNotIn('ATE0', out['adapterCommandsVerified'])
            self.assertEqual(out['ecuRequestsSent'], 0)
            self.assertEqual(port.close_count, 1);self.assertLockFree()

    def stn_port(self, firmware='STN2232 v5.10.3\r>'):
        port = FamilyPort('ELM327 v1.4b')
        write = port.write
        def respond(data):
            if data == b'STI\r':
                port._rx.extend(firmware.encode('ascii'));return len(data)
            return write(data)
        port.write = respond
        return port

    def test_sti_query_allowed_on_all_opens_and_not_counted_as_ecu_traffic(self):
        ports = [self.stn_port() for _ in range(3)]
        with tempfile.TemporaryDirectory() as temp:
            out = ac.run_adapter_check(confirmed_powered_bench=True, out_dir=Path(temp),
                adapter_model='OBDLink', port_factory=mock.Mock(side_effect=ports))
        self.assertTrue(out['ok'], out)
        self.assertEqual(out['ecuRequestsSent'], 0)
        self.assertFalse(out['hardwareQualified'])
        self.assertTrue(out['adapterCommandsVerified']['STI'])
        self.assertEqual(sum(r['command']=='STI' for r in out['commandRecords']), 3)
        self.assertTrue(all(p.close_count==1 for p in ports))
        self.assertLockFree()

    def test_invalid_sti_stops_before_configuration_and_reopen(self):
        port = self.stn_port('?\r>');factory = mock.Mock(return_value=port)
        with tempfile.TemporaryDirectory() as temp:
            out = ac.run_adapter_check(confirmed_powered_bench=True, out_dir=Path(temp),
                adapter_model='OBDLink', port_factory=factory)
        self.assertFalse(out['ok']);factory.assert_called_once()
        self.assertFalse(out['adapterCommandsVerified']['STI'])
        self.assertNotIn('ATE0', out['adapterCommandsVerified'])
        self.assertEqual(out['ecuRequestsSent'], 0)
        self.assertEqual(port.close_count, 1);self.assertLockFree()

    def test_changed_stn_firmware_is_detected_despite_same_elm_banner(self):
        ports = [self.stn_port(), self.stn_port('STN2256 v5.10.3\r>')]
        factory = mock.Mock(side_effect=ports)
        with tempfile.TemporaryDirectory() as temp:
            out = ac.run_adapter_check(confirmed_powered_bench=True, out_dir=Path(temp),
                adapter_model='OBDLink', port_factory=factory)
        self.assertEqual(out['error'], 'adapter-identity-changed')
        self.assertEqual(factory.call_count, 2)
        self.assertEqual(out['reopenCount'], 0)
        self.assertTrue(all(p.close_count==1 for p in ports));self.assertLockFree()

    def test_five_families_use_selected_identity_and_rate_on_every_open(self):
        for model,ati in [('vLinker','vLinker FS'),('OBDLink MX+','OBDLink MX+'),('ELM327','ELM327 v1.5'),
                ('OBDKey','OBDKey 1.4'),('OBDLink','STN1170 v4.0')]:
            ports=[]
            def opened(*args,**kw):
                self.assertEqual(args,('usb:'+'a'*64,));self.assertEqual(kw['adapter_model'],model);self.assertEqual(kw['serial_baud'],230400)
                port=FamilyPort(ati);ports.append(port);return port,'COM21'
            with self.subTest(model=model),tempfile.TemporaryDirectory() as temp,mock.patch.object(ac,'open_selected_port',side_effect=opened):
                out=ac.run_adapter_check(confirmed_powered_bench=True,out_dir=Path(temp),device_id='usb:'+'a'*64,adapter_model=model,serial_baud=230400)
            self.assertTrue(out['ok'],out);self.assertEqual(len(ports),3)
            self.assertTrue(all(p.close_count==1 for p in ports));self.assertEqual(out['ecuRequestsSent'],0)
            self.assertFalse(out['hardwareQualified']);self.assertEqual(out['adapterModel'],model)

    def test_missing_or_invalid_selection_rejected_before_discovery(self):
        for args in [{},{'device_id':hosts.VLIKER},{'device_id':'vnci:123','adapter_model':'vLinker'},
                {'device_id':hosts.VLIKER,'adapter_model':'CANtieCAR'},
                {'device_id':hosts.VLIKER,'adapter_model':'vLinker','serial_baud':True},
                {'device_id':'tcp:127.0.0.1:35000','adapter_model':'ELM327','serial_baud':9600}]:
            with self.subTest(args=args),tempfile.TemporaryDirectory() as temp,mock.patch.object(ac,'open_selected_port') as opened:
                out=ac.run_adapter_check(confirmed_powered_bench=True,out_dir=Path(temp),**args)
                self.assertFalse(out['ok']);opened.assert_not_called();self.assertLockFree()

    def test_failed_cleanup_stops_before_another_open(self):
        ports=[]
        def factory():
            port=bench._Wrap(fail=b'ATPC\r',fail_rx=b'ERROR\r>');ports.append(port);return port
        with tempfile.TemporaryDirectory() as temp:
            out=ac.run_adapter_check(confirmed_powered_bench=True,out_dir=Path(temp),port_factory=factory)
        self.assertFalse(out['ok']);self.assertEqual(len(ports),1);self.assertTrue(ports[0].closed);self.assertTrue(out['simulation'])

    def test_first_close_failure_retained_and_never_repeated_or_reopened(self):
        port=FamilyPort('ELM327 v1.5')
        def bad_close():port.close_count+=1;raise OSError('synthetic first close failure')
        port.close=bad_close
        factory=mock.Mock(return_value=port)
        with tempfile.TemporaryDirectory() as temp:
            out=ac.run_adapter_check(confirmed_powered_bench=True,out_dir=Path(temp),port_factory=factory)
        self.assertFalse(out['ok']);self.assertIn('synthetic first close failure',out['error'])
        factory.assert_called_once();self.assertEqual(port.close_count,1)
        self.assertFalse(out['connections'][0]['portClosed']);self.assertLockFree()
        guard=ac.AtOnlyGuard(port,[])
        for _ in range(2):
            with self.assertRaisesRegex(OSError,'synthetic first close failure'):guard.close()
        self.assertEqual(port.close_count,2,'guard calls lower close only once and retains error')

    def test_changed_adapter_identity_stops_reopen_sequence(self):
        ports=[FamilyPort('ELM327 v1.5'),FamilyPort('ELM327 v2.3')]
        factory=mock.Mock(side_effect=ports)
        with tempfile.TemporaryDirectory() as temp:
            out=ac.run_adapter_check(confirmed_powered_bench=True,out_dir=Path(temp),port_factory=factory)
        self.assertEqual(out['error'],'adapter-identity-changed');self.assertEqual(factory.call_count,2)
        self.assertTrue(all(p.closed for p in ports));self.assertEqual(out['reopenCount'],0)

    def test_cli_requires_stable_selection_and_forwards_rate(self):
        with tempfile.TemporaryDirectory() as temp,contextlib.redirect_stderr(io.StringIO()),mock.patch.object(ac,'run_adapter_check') as run:
            self.assertEqual(ac.main(['--yes-powered-bench','--out',temp]),2);run.assert_not_called()
            run.return_value={'ok':True,'connections':[]}
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(ac.main(['--yes-powered-bench','--out',temp,'--device-id',hosts.VLIKER,
                    '--adapter-model','vLinker','--serial-baud','38400']),0)
            self.assertEqual(run.call_args.kwargs['device_id'],hosts.VLIKER)
            self.assertEqual(run.call_args.kwargs['serial_baud'],38400)

    def test_shared_usb_factory_uses_stable_identity_and_finite_write_timeout(self):
        row={**hosts.TestUsbIdentity.USB,'name':'ELM327 USB'}
        snapshot=hosts.TestUsbIdentity().snap([row]);ports=[FamilyPort('ELM327 v1.5') for _ in range(3)]
        serial=mock.Mock();serial.Serial.side_effect=ports
        with tempfile.TemporaryDirectory() as temp:
            out=ac.run_adapter_check(confirmed_powered_bench=True,out_dir=Path(temp),
                device_id=hosts.usb_device_id(row['pnp']),adapter_model='ELM327',serial_baud=230400,
                serial_module=serial,snapshot=snapshot)
        self.assertTrue(out['ok'],out)
        self.assertEqual(serial.Serial.call_count,3)
        for call in serial.Serial.call_args_list:
            self.assertEqual(call.args,('COM21',230400));self.assertEqual(call.kwargs,{'timeout':.05,'write_timeout':1,'exclusive':True})
        self.assertTrue(all(port.closed for port in ports))

    def test_discovery_failure_never_opens_port(self):
        serial=mock.Mock();listed=mock.Mock();listed.comports.side_effect=OSError('synthetic inventory failure')
        with tempfile.TemporaryDirectory() as temp:
            out=ac.run_adapter_check(confirmed_powered_bench=True,out_dir=Path(temp),device_id=hosts.VLIKER,
                adapter_model='vLinker',serial_module=serial,list_ports=listed)
        self.assertFalse(out['ok']);serial.Serial.assert_not_called()
        self.assertEqual(len(out['connections']),1);self.assertLockFree()
