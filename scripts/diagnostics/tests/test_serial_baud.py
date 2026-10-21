"""Configured host UART rates: fake byte ports only, no hardware qualification."""
import io
import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

from scripts.diagnostics import connection, internal_stream, sessions, manufacturer_read
from scripts.diagnostics.bench import VirtualElmPort, FakeClock
from scripts.diagnostics.elm import ElmClient, ElmError, adapter_identity_ok, stn_firmware_identity_ok
from scripts.diagnostics.tests import test_connection as hosts
from scripts.diagnostics.tests import test_sessions as lifecycle
from scripts.diagnostics.tests import test_can_monitor as can_fixture
from scripts.diagnostics.tests import test_manufacturer_read as manufacturer


class StnIdentityPort(VirtualElmPort):
    """Vendor-documented local query responses; no real port or ECU."""
    def __init__(self, sti='STN2232 v5.10.3\r>'):
        super().__init__(ecu_map={}, identity='ELM327 v1.4b')
        self.sti = sti

    def write(self, data):
        if data == b'STI\r':
            self.writes.append(data)
            if self.sti is not None:
                self._rx.extend(self.sti.encode('ascii'))
            return len(data)
        return super().write(data)


class SerialBaudTests(unittest.TestCase):
    def test_selected_brand_cannot_be_borrowed_from_prose(self):
        cases = [('OBDKey', 'ELM327 v1.5\rNOT OBDKEY', False),
                 ('OBDLink', 'ELM327 v1.5\rNOT OBDLINK', True),
                 ('OBDLink', 'OBDKey 1.4\rSupports ELM327 commands', False)]
        for model, banner, needs_sti in cases:
            with self.subTest(model=model, banner=banner):
                port = StnIdentityPort('?\r>'); port.identity = banner
                output = io.StringIO()
                self.assertEqual(connection.run_voltage_monitor(hosts.VLIKER, port=port,
                    adapter_model=model, stdout=output, stop_event=threading.Event(), max_samples=1), 1)
                events = [json.loads(line) for line in output.getvalue().splitlines()]
                self.assertEqual(events[0]['error'], 'adapter-model-mismatch')
                self.assertNotIn('handshake', [event['type'] for event in events])
                self.assertEqual(b'STI\r' in port.writes, needs_sti)
                self.assertNotIn(b'ATE0\r', port.writes)
                self.assertEqual(port.ecu_payloads, []); self.assertTrue(port.closed)

    def test_obdlink_mention_does_not_skip_firmware_confirmation(self):
        port = StnIdentityPort(); port.identity = 'ELM327 v1.5\rSupports OBDLink commands'
        client = ElmClient(port, expected_model='OBDLink')
        try:
            self.assertIn('STN2232', client.validate_adapter()['sti'])
            self.assertEqual(port.writes, [b'ATI\r', b'ATDPN\r', b'ATRV\r', b'STI\r'])
            self.assertEqual(port.ecu_payloads, [])
        finally:
            self.assertEqual(client.close_restore()['errors'], [])

    def test_selected_clean_brand_keeps_legacy_spacing(self):
        for model, banner in [('OBDKey', 'OBDKey1.4'), ('OBDKey', 'OBDKey 1.4'),
                              ('OBDLink', 'OBDLink MX+'), ('OBDLink', 'OBDLink v5.10.3')]:
            with self.subTest(model=model, banner=banner):
                port = VirtualElmPort(ecu_map={}, identity='ATI\r' + banner)
                client = ElmClient(port, expected_model=model)
                try:
                    self.assertNotIn('sti', client.validate_adapter())
                    self.assertEqual(port.writes, [b'ATI\r', b'ATDPN\r', b'ATRV\r'])
                    self.assertEqual(port.ecu_payloads, [])
                finally:
                    self.assertEqual(client.close_restore()['errors'], [])

    def test_error_or_prose_with_brand_is_not_an_adapter_identity(self):
        for reply in ['ELM327 v2.3\rERROR\r>', 'OBDLink MX+\r?\r>',
                      'vLinker FS UNABLE TO CONNECT\r>', 'OBDKey 1.4\rNO DATA\r>',
                      'ELM327 v2.3\rSTOPPED\r>', 'STN2232 v5.10.3\rBUFFER FULL\r>',
                      'ELM327 v2.3\rBUS BUSY\r>', 'NOT AN ELM327\r>',
                      'Adapter supports OBDKey commands\r>']:
            with self.subTest(reply=reply):
                self.assertFalse(adapter_identity_ok(reply))

    def test_identity_errors_stop_monitor_before_handshake_or_vehicle_bytes(self):
        for model, banner in [('ELM327', 'ELM327 v2.3\rERROR'),
                              ('OBDLink', 'ELM327 v1.4b\r?'),
                              ('OBDKey', 'OBDKey 1.4\rNO DATA'),
                              ('vLinker', 'vLinker FS\rSTOPPED'),
                              ('OBDLink MX+', 'ELM327 v1.4b\rUNABLE TO CONNECT')]:
            with self.subTest(model=model):
                port = VirtualElmPort(ecu_map={}, identity=banner);out = io.StringIO()
                code = connection.run_voltage_monitor(hosts.VLIKER, port=port, adapter_model=model,
                    stdout=out, stop_event=threading.Event(), max_samples=1)
                self.assertEqual(code, 1)
                events = [json.loads(line) for line in out.getvalue().splitlines()]
                self.assertEqual(events[0]['error'], 'adapter-identity-mismatch')
                self.assertNotIn('handshake', [event['type'] for event in events])
                self.assertNotIn(b'ATE0\r', port.writes)
                self.assertNotIn(b'STI\r', port.writes, 'STI must not override an erroneous ATI')
                self.assertEqual(port.ecu_payloads, []);self.assertTrue(port.closed)

    def test_clean_identity_echoes_and_legacy_version_spacing_stay_supported(self):
        for banner in ['ELM327', 'ELM327v1.5', 'ELM327 v2.3', 'OBDKey1.4', 'OBDKey 1.4',
                       'OBDLink MX+', 'vLinker FS v2.2.4', 'V-LINKER MC+', 'STN2232 v5.10.3']:
            with self.subTest(banner=banner):
                self.assertTrue(adapter_identity_ok('ATI\r\r'+banner+'\r\r>'))

    def test_generic_elm_banner_requires_real_stn_firmware_reply(self):
        for firmware in ['STN1110 v4.0', 'STN1155 v5.6.5', 'STN2232 v5.10.3',
                         'STN2256 v5.10.3', 'STN2310 v5.10.3']:
            with self.subTest(firmware=firmware):
                port = StnIdentityPort('STI\r' + firmware + '\r>')
                client = ElmClient(port, expected_model='OBDLink')
                ident = client.validate_adapter()
                self.assertIn(firmware, ident['sti'])
                self.assertEqual(port.writes, [b'ATI\r', b'ATDPN\r', b'ATRV\r', b'STI\r'])
                self.assertEqual(port.ecu_payloads, [])
                self.assertEqual(client.close_restore()['errors'], [])
                self.assertTrue(port.closed)

    def test_sti_errors_or_ambiguous_identity_never_reach_configuration(self):
        for reply in ['?\r>', 'OK\r>', 'ELM327 v1.5\r>', 'STN2232\r>',
                      'STN2232 v5.10.3\rERROR\r>', 'STN2232 v5.10.3\r?\r>',
                      'prefix STN2232 v5.10.3\r>', 'STN2232 v5.10.3 extra\r>',
                      'STN2232 v5.10.3\rSTN2256 v5.10.3\r>']:
            with self.subTest(reply=reply):
                self.assertFalse(stn_firmware_identity_ok(reply))
                port = StnIdentityPort(reply)
                client = ElmClient(port, expected_model='OBDLink')
                with self.assertRaisesRegex(ElmError, 'adapter-model-mismatch'):
                    client.validate_adapter()
                self.assertEqual(port.writes, [b'ATI\r', b'ATDPN\r', b'ATRV\r', b'STI\r'])
                self.assertEqual(client.close_restore()['errors'], [])
                self.assertEqual(port.ecu_payloads, [])
                self.assertTrue(port.closed)

    def test_direct_modern_stn_identity_does_not_need_second_query(self):
        for firmware in ['STN2232 v5.10.3', 'STN2256 v5.10.3', 'STN2310 v5.10.3']:
            port = VirtualElmPort(ecu_map={}, identity=firmware)
            client = ElmClient(port, expected_model='OBDLink')
            self.assertNotIn('sti', client.validate_adapter())
            self.assertEqual(port.writes, [b'ATI\r', b'ATDPN\r', b'ATRV\r'])
            client.close_restore()

    def test_sti_uses_remaining_budget_and_timeout_never_sends_vehicle_bytes(self):
        clock = FakeClock()
        port = StnIdentityPort(None)
        write = port.write
        def delayed_ati(data):
            if data == b'ATI\r':
                clock.sleep(.12)
            return write(data)
        port.write = delayed_ati
        client = ElmClient(port, expected_model='OBDLink', clock=clock.time, sleeper=clock.sleep)
        with self.assertRaisesRegex(ElmError, 'prompt-timeout'):
            client.validate_adapter(.2)
        self.assertLessEqual(clock.time(), .21)
        self.assertEqual(port.writes, [b'ATI\r', b'ATDPN\r', b'ATRV\r', b'STI\r'])
        client.close_restore()
        self.assertEqual(port.ecu_payloads, [])
        self.assertTrue(port.closed)

    def test_probe_and_monitor_accept_generic_banner_then_close_cleanly(self):
        for kind in ['probe', 'monitor']:
            with self.subTest(kind=kind):
                port = StnIdentityPort()
                if kind == 'probe':
                    out = connection.probe_adapter(hosts.VLIKER, port=port, adapter_model='OBDLink')
                    self.assertTrue(out['ok'], out)
                    self.assertEqual(out['volts'], 12)
                else:
                    out = io.StringIO()
                    status = connection.run_voltage_monitor(hosts.VLIKER, port=port, adapter_model='OBDLink',
                        stdout=out, stop_event=threading.Event(), max_samples=1)
                    self.assertEqual(status, 0, out.getvalue())
                    self.assertIn('handshake', [json.loads(line)['type'] for line in out.getvalue().splitlines()])
                self.assertEqual(port.writes.count(b'STI\r'), 1)
                self.assertEqual(port.ecu_payloads, [])
                self.assertTrue(port.closed)

    def test_stn_reply_does_not_bypass_os_bound_clear_permission(self):
        row = {**hosts.TestUsbIdentity.USB, 'name': 'OBDLink EX USB'}
        serial = mock.Mock()
        with self.assertRaisesRegex(ElmError, 'adapter-clear-not-qualified'):
            connection.open_selected_port(hosts.usb_device_id(row['pnp']),
                snapshot=hosts.TestUsbIdentity().snap([row]), serial_module=serial,
                adapter_model='OBDLink', require_clear_qualified=True)
        serial.Serial.assert_not_called()

    def test_all_elm_families_use_explicit_rate_and_only_existing_monitor_commands(self):
        for model, identity in [('vLinker', 'vLinker FS'), ('OBDLink MX+', 'OBDLink MX+'),
                ('ELM327', 'ELM327 v1.5'), ('OBDKey', 'OBDKey 1.4'), ('OBDLink', 'STN1170 v4.0')]:
            for rate in connection.ELM_SERIAL_BAUDS:
                with self.subTest(model=model, rate=rate):
                    row = {**hosts.TestUsbIdentity.USB, 'name': model + ' USB'}
                    snapshot = hosts.TestUsbIdentity().snap([row])
                    port = VirtualElmPort(ecu_map={}, identity=identity)
                    serial = mock.Mock(); serial.Serial.return_value = port
                    out = io.StringIO()
                    status = connection.run_voltage_monitor(connection.usb_device_id(row['pnp']),
                        snapshot=snapshot, serial_module=serial, adapter_model=model, serial_baud=rate,
                        stdout=out, stop_event=threading.Event(), max_samples=1)
                    self.assertEqual(status, 0, out.getvalue())
                    serial.Serial.assert_called_once_with('COM21', rate, timeout=.05, write_timeout=1.0, exclusive=True)
                    self.assertEqual(port.writes, [b'ATI\r', b'ATDPN\r', b'ATRV\r', b'ATPC\r'])
                    self.assertTrue(port.closed)

    def test_invalid_rates_and_wrong_transport_never_discover_or_open(self):
        with mock.patch.object(connection, 'resolve_device', side_effect=AssertionError('enumerated')), \
                mock.patch.object(connection, 'TcpBytePort', side_effect=AssertionError('socket')):
            for rate in [0, 10400, 115200.0, True, '115200', [], {}, float('nan')]:
                with self.subTest(rate=rate), self.assertRaisesRegex(connection.ElmError, 'serial-baud-invalid'):
                    connection.open_selected_port(hosts.VLIKER, serial_baud=rate)
            for device in ['tcp:127.0.0.1:35000', 'vnci:123', 'pt3g:123', None]:
                with self.assertRaisesRegex(connection.ElmError, 'serial-baud-transport-mismatch'):
                    connection.open_selected_port(device, serial_baud=9600)

    def test_native_monitor_cannot_ignore_serial_rate(self):
        for device in ['vnci:123', 'pt3g:123']:
            out = io.StringIO()
            self.assertEqual(connection.run_voltage_monitor(device, serial_baud=9600, stdout=out), 2)
            self.assertEqual(json.loads(out.getvalue())['error'], 'serial-baud-transport-mismatch')

    def test_stdio_monitor_forwards_rate_and_rejects_malformed_before_worker(self):
        req = {'action': 'monitor', 'deviceId': hosts.VLIKER, 'adapterModel': 'vLinker', 'serialBaud': 38400}
        with mock.patch.object(connection, 'run_voltage_monitor', return_value=0) as worker:
            self.assertEqual(connection.stdio_loop(io.StringIO(json.dumps(req)+'\n'), io.StringIO()), 0)
            self.assertEqual(worker.call_args.kwargs['serial_baud'], 38400)
            for rate in [True, '38400', 10400]:
                worker.reset_mock()
                self.assertEqual(connection.stdio_loop(io.StringIO(json.dumps({**req, 'serialBaud': rate})+'\n'), io.StringIO()), 2)
                worker.assert_not_called()

    def test_live_recovery_preserves_rate_and_requalifies_identity(self):
        fixture = lifecycle.TestDesktopReadLifecycle()
        first, recovered = fixture.breaking_port(), sessions.SessionSimPort(fixture.PROFILE)
        out, opened = fixture.live([first, recovered], serial_baud=230400)
        self.assertTrue(out['ok'], out)
        self.assertEqual(opened.call_count, 2)
        self.assertEqual([c.kwargs['serial_baud'] for c in opened.call_args_list], [230400, 230400])
        self.assertEqual(recovered.ecu_payloads[0], '1089')
        self.assertTrue(first.closed and recovered.closed)
        self.assertEqual(recovered.clear_tx_count, 0)

    def test_session_json_rejects_rates_before_running(self):
        req = {'action': 'run', 'mode': 'live', 'profileId': 'porsche-981-2014-dme',
            'deviceId': hosts.VLIKER, 'confirmedReadOnly': True, 'x431Inactive': True, 'serialBaud': 38400}
        self.assertEqual(sessions._parse_request(json.dumps(req))['serialBaud'], 38400)
        for extra in [{'serialBaud': True}, {'serialBaud': '38400'}, {'deviceId': 'vnci:123'}, {'mode': 'simulation'}]:
            with self.subTest(extra=extra), self.assertRaises(sessions.SessionError):
                sessions._parse_request(json.dumps({**req, **extra}))

    def test_internal_rate_forwarding_does_not_change_receive_only_setup(self):
        clock, output = can_fixture.Clock(), io.StringIO()
        port = can_fixture.Port(clock)
        with mock.patch.object(internal_stream, 'open_selected_port', return_value=(port, 'injected')) as opened:
            status = internal_stream.run_internal_monitor(hosts.MX, 'drive', stdout=output, clock=clock,
                max_reads=100, serial_baud=38400)
        self.assertEqual(status, 0, output.getvalue())
        opened.assert_called_once_with(hosts.MX, serial_baud=38400)
        self.assertIn(b'STCMM 0\r', port.writes)
        self.assertTrue(port.closed)
        with mock.patch.object(internal_stream, 'open_selected_port') as opened:
            self.assertEqual(internal_stream.run_internal_monitor(hosts.MX, 'drive', stdout=io.StringIO(), serial_baud=True), 2)
            opened.assert_not_called()

    def test_manufacturer_factory_uses_rate_for_grouped_read_and_release(self):
        fixture = manufacturer.ReadTests()
        req = {**manufacturer.request(), 'serialBaud': 230400}
        out, ports = fixture.run_session(req=req)
        self.assertTrue(out['ok'], out)
        self.assertEqual([s['value'] for s in out['samples']], [42, 7])
        self.assertEqual(ports[0].ecu_payloads.count('22F100'), 1)
        self.assertTrue(ports[0].closed)
        with mock.patch.object(manufacturer_read, 'verified_profile', side_effect=AssertionError('loaded')):
            out = manufacturer_read.read_manufacturer({**req, 'serialBaud': True})
        self.assertEqual(out['error'], 'serial-baud-invalid')

    def test_js_and_python_rate_choices_and_defaults_match(self):
        import subprocess
        result = subprocess.run(['node', '--input-type=module', '-e',
            'import {ELM_SERIAL_BAUDS,ELM_DEFAULT_BAUDS} from "./apps/desktop/src/obd-connection-logic.mjs";'
            'console.log(JSON.stringify([ELM_SERIAL_BAUDS,ELM_DEFAULT_BAUDS]))'], check=True, capture_output=True, text=True, timeout=10)
        rates, defaults = json.loads(result.stdout)
        self.assertEqual(rates, list(connection.ELM_SERIAL_BAUDS))
        self.assertEqual(defaults, connection.ELM_BAUDS)
