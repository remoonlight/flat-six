"""Support-copy regressions: complete pins, real config name, no shared changes."""
import hashlib
import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch

from scripts.diagnostics import pt3g, pt3g_preparation as prep


class SupportPreparationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.source = self.base / 'installed' / 'X64'; self.source.mkdir(parents=True)
        self.output = self.base / '.local' / 'prepared'
        self.enabled = b'#ini\r\n[T6CONFIG]\r\n15765StartDetect=1\r\nSN=10001\r\n'
        self.disabled = self.enabled.replace(b'StartDetect=1', b'StartDetect=0')
        self.contents = {'PDU_VCI.dll': b'pinned-library', '../E70_PT3G_SERVICE.exe': b'pinned-service',
                         'PDU_VCI.ini': self.enabled}
        self.pins = {name: hashlib.sha256(self.disabled if name == 'PDU_VCI.ini' else raw).hexdigest()
                     for name, raw in self.contents.items()}
        self.pins[pt3g.ACTIVE_CONFIG] = self.pins['PDU_VCI.ini']
        for name, raw in self.contents.items(): (self.source / name).write_bytes(raw)
        for target, value in (('scripts.diagnostics.pt3g.SUPPORT_SHA256', self.pins),
                              ('scripts.diagnostics.pt3g_preparation.SUPPORT_SHA256', self.pins),
                              ('scripts.diagnostics.pt3g_preparation.SOURCE_INI_SHA256', hashlib.sha256(self.enabled).hexdigest()),
                              ('scripts.diagnostics.pt3g_preparation.ROOT', self.base)):
            p = patch(target, value); p.start(); self.addCleanup(p.stop)

    def test_complete_copy_adds_actual_config_and_preserves_every_source_byte(self):
        (self.source / 'private-extra.json').write_text('not part of support')
        with patch.object(pt3g, 'NativeAdapter') as native:
            report = prep.prepare_support(self.source, self.output)
        native.assert_not_called()
        driver = self.output / 'X64'
        self.assertEqual((driver / 'PDU_VCI.ini').read_bytes(), self.disabled)
        self.assertEqual((driver / pt3g.ACTIVE_CONFIG).read_bytes(), self.disabled)
        self.assertEqual((self.output / 'source-config/PDU_VCI.ini').read_bytes(), self.enabled)
        self.assertFalse((driver / 'private-extra.json').exists())
        self.assertEqual((self.output / 'E70_PT3G_SERVICE.exe').read_bytes(), b'pinned-service')
        for name, raw in self.contents.items(): self.assertEqual((self.source / name).read_bytes(), raw)
        self.assertFalse(report['driverLoaded']); self.assertFalse(report['serviceChanged'])
        self.assertFalse(report['vehicleTransportQualified']); self.assertEqual(report['vehicleRequestsSent'], 0)
        pt3g.verify_vendor_files(driver)
        with self.assertRaisesRegex(ValueError, 'already-exists'):
            prep.prepare_support(self.source, self.output)

    def test_already_disabled_source_and_known_active_config_are_preserved(self):
        (self.source / 'PDU_VCI.ini').write_bytes(self.disabled)
        (self.source / pt3g.ACTIVE_CONFIG).write_bytes(self.enabled)
        prep.prepare_support(self.source, self.output)
        self.assertEqual((self.output / 'source-config' / pt3g.ACTIVE_CONFIG).read_bytes(), self.enabled)
        self.assertEqual((self.output / 'X64' / pt3g.ACTIVE_CONFIG).read_bytes(), self.disabled)

    def test_drifted_dll_or_config_rejected_before_output_creation(self):
        for name in ('PDU_VCI.dll', 'PDU_VCI.ini', pt3g.ACTIVE_CONFIG):
            original = (self.source / name).read_bytes() if (self.source / name).exists() else None
            (self.source / name).write_bytes(b'unknown')
            with self.assertRaisesRegex(ValueError, 'source-not-qualified'):
                prep.prepare_support(self.source, self.output)
            self.assertFalse(self.output.exists())
            if original is None: (self.source / name).unlink()
            else: (self.source / name).write_bytes(original)

    def test_missing_service_or_symlink_rejected_without_loading(self):
        service = self.source / '../E70_PT3G_SERVICE.exe'; service.unlink()
        with self.assertRaisesRegex(ValueError, 'source-file-invalid'):
            prep.prepare_support(self.source, self.output)
        self.assertFalse(self.output.exists())
        service.write_bytes(b'pinned-service')
        dll = self.source / 'PDU_VCI.dll'; dll.unlink(); dll.symlink_to(service)
        with self.assertRaisesRegex(ValueError, 'source-file-invalid'):
            prep.prepare_support(self.source, self.output)
        self.assertFalse(self.output.exists())

    def test_public_output_is_rejected(self):
        with self.assertRaisesRegex(ValueError, 'output-must-be-private'):
            prep.prepare_support(self.source, self.base / 'public')
        self.assertFalse((self.base / 'public').exists())

    def test_source_change_during_copy_never_publishes_success_manifest(self):
        original_verify = prep.verify_vendor_files
        def verify_and_change(root):
            original_verify(root)
            (self.source / 'PDU_VCI.dll').write_bytes(b'changed after copy')
        with patch.object(prep, 'verify_vendor_files', side_effect=verify_and_change):
            with self.assertRaisesRegex(ValueError, 'source-changed'):
                prep.prepare_support(self.source, self.output)
        self.assertFalse((self.output / 'manifest.json').exists())


if __name__ == '__main__': unittest.main()
