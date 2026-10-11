import tempfile
import unittest
import hashlib
from contextlib import ExitStack
from unittest.mock import patch
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
            self.assertFalse(doc["drivers"]["VNCI"]["supportFilesQualified"])
            self.assertIsNone(doc["drivers"]["VNCI"]["version"])
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

    def test_adb_explicit_bundle_checked_without_execution_or_path_fallback(self):
        with tempfile.TemporaryDirectory() as temp:
            executable=Path(temp)/'adb.exe';executable.write_bytes(b'not executed')
            environment={'PORSCHE981_ADB':str(executable),'PATH':'ignored'}
            with patch('scripts.diagnostics.host_devices.shutil.which',side_effect=AssertionError('no fallback')), \
                 patch('scripts.diagnostics.x431_reference.verify_windows_tools',return_value={'version':'37.0.1'}) as verify, \
                 patch('subprocess.run',side_effect=AssertionError('must not execute')):
                driver=collect_host_devices(platform='win32',env=environment,query=lambda _:([{}],None))['drivers']['X431-tablet']
                verify.assert_called_once_with(str(executable))
                self.assertTrue(driver['installed']);self.assertTrue(driver['supportFilesQualified'])
                self.assertEqual(driver['version'],'37.0.1')
                for key in ('runtimeExecuted','serverStarted','firmwareQualified','vehicleQualified','directProjectHeadTransportImplemented'):
                    self.assertFalse(driver[key])
            executable.unlink()
            with patch('scripts.diagnostics.host_devices.shutil.which',side_effect=AssertionError('no fallback')):
                driver=collect_host_devices(platform='win32',env=environment,query=lambda _:([{}],None))['drivers']['X431-tablet']
                self.assertFalse(driver['installed']);self.assertFalse(driver['supportFilesQualified'])
                self.assertIsNone(driver['version']);self.assertIsNotNone(driver['supportError'])

    def test_adb_path_uses_supplied_environment_and_unverified_version_is_absent(self):
        with tempfile.TemporaryDirectory() as temp:
            executable=Path(temp)/'adb.exe';executable.write_bytes(b'unknown binary')
            with patch('scripts.diagnostics.host_devices.shutil.which',return_value=str(executable)) as which:
                driver=collect_host_devices(platform='win32',env={'PATH':'selected-path'},query=lambda _:([{}],None))['drivers']['X431-tablet']
                which.assert_called_once_with('adb',path='selected-path')
                self.assertTrue(driver['installed']);self.assertFalse(driver['supportFilesQualified'])
                self.assertIsNone(driver['version'])

    def test_exact_configured_roots_hashes_and_file_drift_without_native_loading(self):
        from scripts.diagnostics import vnci, pt3g
        with tempfile.TemporaryDirectory() as temp, ExitStack() as stack:
            root=Path(temp);vroot=root/'VNCI isolated';proot=root/'E70 isolated/X64'
            vroot.mkdir();proot.mkdir(parents=True)
            vhash={};phash={}
            for name in ('PDUAPI_VW.dll', *vnci.SUPPORT_SHA256):
                data=('fixture:'+name).encode();p=vroot/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(data)
                vhash[name]=hashlib.sha256(data).hexdigest()
            for name in pt3g.SUPPORT_SHA256:
                data=b'[T6CONFIG]\n15765StartDetect=0\n' if name in ('PDU_VCI.ini',pt3g.ACTIVE_CONFIG) else ('fixture:'+name).encode()
                p=proot/name;p.write_bytes(data);phash[name]=hashlib.sha256(data).hexdigest()
            stack.enter_context(patch.object(vnci,'LIBRARY_SHA256',vhash.pop('PDUAPI_VW.dll')))
            stack.enter_context(patch.object(vnci,'SUPPORT_SHA256',vhash))
            stack.enter_context(patch.object(pt3g,'SUPPORT_SHA256',phash))
            stack.enter_context(patch.object(vnci.c,'WinDLL',side_effect=AssertionError('must not load driver'),create=True))
            environment={'PORSCHE981_VNCI_ROOT':str(vroot),'PORSCHE981_PT3G_ROOT':str(proot),
                         'PORSCHE981_DEFINITION_ROOT':str(root/'no-default-definitions'),'ProgramData':str(root/'no-default-service')}
            def collect():return collect_host_devices(platform='win32',env=environment,query=lambda _: ([{}],None))['drivers']
            doc=collect()
            for family in ('VNCI','PT3G'):
                self.assertTrue(doc[family]['supportFilesQualified']);self.assertIsNotNone(doc[family]['version'])
                self.assertEqual(doc[family]['versionSource'],'pinned-support-file-hashes')
                self.assertFalse(doc[family]['driverLoaded']);self.assertFalse(doc[family]['firmwareQualified'])
                self.assertFalse(doc[family]['vehicleQualified'])
            (vroot/'MDF_VW.xml').write_bytes(b'drift')
            (proot/pt3g.ACTIVE_CONFIG).write_bytes(b'[T6CONFIG]\n15765StartDetect=1\n')
            doc=collect()
            for family in ('VNCI','PT3G'):
                self.assertTrue(doc[family]['installed']);self.assertFalse(doc[family]['supportFilesQualified'])
                self.assertIsNone(doc[family]['version']);self.assertIsNotNone(doc[family]['supportError'])
            (proot/'pdu2.dll').unlink();self.assertFalse(collect()['PT3G']['installed'])

    def test_roots_match_native_helpers_and_explicit_vendor_override_wins(self):
        from scripts.diagnostics.vnci import vendor_root as vnci_path
        from scripts.diagnostics.pt3g import vendor_root as pt3g_path
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);environment={'PORSCHE981_DEFINITION_ROOT':str(root/'definitions'),
                                       'ProgramData':str(root/'program-data')}
            with patch('scripts.diagnostics.vnci.verify_vendor_files') as v, patch('scripts.diagnostics.pt3g.verify_vendor_files') as p:
                collect_host_devices(platform='win32',env=environment,query=lambda _:([{}],None))
                v.assert_called_once_with(vnci_path(environment));p.assert_called_once_with(pt3g_path(environment))
            with patch('scripts.diagnostics.vnci.verify_vendor_files') as v:
                collect_host_devices(platform='win32',env=environment,vendor_root=root/'explicit',query=lambda _:([{}],None))
                v.assert_called_once_with(root/'explicit')
