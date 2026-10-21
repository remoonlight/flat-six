"""Multi-mode head identification never authorizes an ELM/native port open."""
import unittest
from unittest import mock
from scripts.diagnostics import connection as con
from scripts.diagnostics.tests import test_connection as hosts


class CantiecarInventoryTests(unittest.TestCase):
    def test_name_does_not_claim_elm_mode(self):
        for name in ['CANtieCAR USB', 'CANtieCAR Bluetooth', 'cantiecar v4']:
            self.assertEqual(con.classify_brand(name, None), 'CANtieCAR')
            self.assertTrue(con.is_obd_name(name))
        self.assertNotIn('CANtieCAR', con.ELM_MODELS)

    def test_bluetooth_is_listed_without_opening_or_claiming_mode(self):
        snap = hosts._snap(serial=[hosts.MX_SERIAL], bluetooth=[{**hosts.MX_BT, 'friendlyName': 'CANtieCAR v4'}])
        serial = mock.Mock()
        rows = con.enumerate_devices(snap)['devices']
        row = next(r for r in rows if r['id'] == hosts.MX)
        self.assertEqual(row['brand'], 'CANtieCAR');self.assertTrue(row['available'])
        self.assertIn('模式', row['guidance']);serial.Serial.assert_not_called()

    def test_usb_known_name_cannot_be_overridden_as_elm(self):
        row = {**hosts.TestUsbIdentity.USB, 'name': 'CANtieCAR USB'}
        snap = hosts.TestUsbIdentity().snap([row]);did = con.usb_device_id(row['pnp'])
        records = con.enumerate_devices(snap)['devices'];head = next(r for r in records if r['id'] == did)
        self.assertEqual(head['brand'], 'CANtieCAR');self.assertIn('模式', head['guidance'])
        for model in [None, 'CANtieCAR', 'ELM327', 'vLinker', 'OBDLink MX+']:
            serial = mock.Mock()
            with self.subTest(model=model), self.assertRaisesRegex(con.ElmError, 'adapter-mode-not-qualified'):
                con.open_selected_port(did, snapshot=snap, serial_module=serial, adapter_model=model)
            serial.Serial.assert_not_called()

    def test_explicit_unqualified_mode_never_opens_tcp_or_discovers_serial(self):
        with mock.patch.object(con, 'TcpBytePort', side_effect=AssertionError('network opened')), \
                mock.patch.object(con, 'resolve_device', side_effect=AssertionError('discovered')):
            for did in [hosts.MX, 'usb:'+'a'*64, 'tcp:192.0.2.1:35000']:
                with self.assertRaisesRegex(con.ElmError, 'adapter-mode-not-qualified'):
                    con.open_selected_port(did, adapter_model='CANtieCAR')
