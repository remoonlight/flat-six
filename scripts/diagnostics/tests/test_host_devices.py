import tempfile
import unittest
from pathlib import Path
from scripts.diagnostics.host_devices import collect_host_devices


class HostDevicesTests(unittest.TestCase):
    def test_present_usb_service_and_actual_installed_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for filename in ("E70_PT3G_SERVICE.exe", "X64/PDU_VCI.dll", "X64/pdu2.dll"):
                file = root / "PORSCHE-VCI" / filename
                file.parent.mkdir(parents=True, exist_ok=True)
                file.write_bytes(b"test fixture; never loaded")
            (root / "PDUAPI_VW.dll").write_bytes(b"not loaded")
            commands = []
            def query(script):
                commands.append(script)
                return [{"usb": [{"Status": "OK", "InstanceId": "USB\\confirmed"}],
                         "services": [{"Name": "VciToolServerPORSCHE", "State": "Running"}]}], None
            doc = collect_host_devices(platform="win32", query=query, program_data=root, vendor_root=root)
            self.assertTrue(doc["drivers"]["PT3G"]["installed"])
            self.assertTrue(doc["drivers"]["VNCI"]["installed"])
            self.assertEqual(doc["services"][0]["State"], "Running")
            self.assertIn("-PresentOnly", commands[0])
            for mutation in ("Start-Service", "Set-", "adb", "Serial", "Connect"):
                self.assertNotIn(mutation, commands[0])

    def test_failed_usb_enumeration_does_not_invent_online_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            doc = collect_host_devices(platform="win32", query=lambda _: (None, "powershell-timeout"),
                                       program_data=tmp, vendor_root=tmp)
            self.assertEqual(doc["usb"], [])
            self.assertEqual(doc["errors"], ["powershell-timeout"])
            self.assertFalse(doc["drivers"]["PT3G"]["installed"])

    def test_other_platforms_do_not_probe(self):
        doc = collect_host_devices(platform="linux", query=lambda _: self.fail("must not probe"))
        self.assertEqual(doc["drivers"], {})
