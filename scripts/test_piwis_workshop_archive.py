import tempfile
import hashlib
import json
import unittest
import zipfile
from pathlib import Path

from scripts.archive_piwis_workshop import SOURCE_PATHS, file_metadata, odx_metadata, safe_relative, selection, verify_archive


class ArchiveTests(unittest.TestCase):
    def inventory(self, names):
        return {"files": [{"relativePath": p} for p in [*SOURCE_PATHS.values(), *names]]}

    def test_other_model_and_part_number_prefix_are_excluded(self):
        rules = [{"id": "981-rule", "targets": [{"softwarePartNumber": "9816186011", "session": None}]}]
        result = selection(self.inventory(["flash-data/flash-data/FL_9816186011_0001.pdx",
                                           "flash-data/flash-data/FL_991_DME_98161860115_0001.pdx",
                                           "flash-data/flash-data/FL_981_Kombiinstrument_0373.pdx"]), {"rules": rules})
        containers = [f for f in result if f["relativePath"].endswith(".pdx")]
        self.assertEqual(len(containers), 2)
        self.assertEqual(containers[0]["matchedRuleIds"], ["981-rule"])
        self.assertEqual(containers[1]["selectionBasis"], "explicit-family-filename")

    def test_dataset_session_requires_exact_container_name(self):
        rules = [{"id": "dataset", "targets": [{"softwarePartNumber": None, "session": "SESD_000_TEST_P105"}]},
                 {"id": "blocked", "targets": [{"softwarePartNumber": None, "session": None}]}]
        result = selection(self.inventory(["flash-data/flash-data/DB_000_TEST_P105_container.pdx",
                                           "flash-data/flash-data/DB_000_TEST_P105_991_container.pdx"]), {"rules": rules})
        containers = [f for f in result if f["relativePath"].endswith(".pdx")]
        self.assertEqual(len(containers), 1)
        self.assertEqual(containers[0]["matchedRuleIds"], ["dataset"])

    def test_shared_body_candidate_does_not_import_991_only_systems(self):
        result = selection(self.inventory(["flash-data/flash-data/FL_BCM_HINTEN_7PP907279BF_2900_00_container.pdx",
                                           "flash-data/flash-data/FL_9x1_Hinterachslenkung_99133105707_1008.pdx",
                                           "flash-data/flash-data/FL_9x1_PDCC_99161810709_10F0.pdx",
                                           "flash-data/flash-data/FL_9x1_TARGA_99161824103_2200.pdx"]), {"rules": []})
        containers = [f for f in result if f["relativePath"].endswith(".pdx")]
        self.assertEqual(len(containers), 1)
        self.assertEqual(containers[0]["selectionBasis"], "shared-platform-system-unverified")
        self.assertEqual(containers[0]["sharedSystem"]["logicalLink"], "BCM_HINTEN")

    def test_archive_path_cannot_escape_or_use_windows_drive(self):
        for name in ["../other.pdx", "/other.pdx", "C:/other.pdx", "a\\other.pdx", "a\nother.pdx"]:
            with self.subTest(name=name), self.assertRaises(ValueError):
                safe_relative(name)

    def test_odx_template_history_is_kept_separate_from_export_time(self):
        raw = b'''<ODX MODEL-VERSION="2.1.0"><!--created by exporter on 2022-11-21T06:02:55.967000-->
<ADMIN-DATA><DOC-REVISIONS><DOC-REVISION><DATE>2008-06-19T15:04:29+02:00</DATE>
<REVISION-LABEL>1.0</REVISION-LABEL><STATE>draft</STATE></DOC-REVISION></DOC-REVISIONS></ADMIN-DATA>
<PARTNUMBER>98164110400</PARTNUMBER><OWN-IDENT><SHORT-NAME>ApplSwVerCount1</SHORT-NAME>
<IDENT-VALUE>0941</IDENT-VALUE></OWN-IDENT><SESSION><SHORT-NAME>SES_TEST</SHORT-NAME></SESSION>
<SESSION-DESC><SHORT-NAME>SESD_TEST</SHORT-NAME><SESSION-SNREF SHORT-NAME="SES_TEST"/></SESSION-DESC></ODX>'''
        info = odx_metadata(raw, "test.odx-f")
        self.assertEqual(info["exportedAt"], "2022-11-21T06:02:55.967000")
        self.assertEqual(info["documentRevisions"][0]["date"], "2008-06-19T15:04:29+02:00")
        self.assertEqual(info["ownIdentifiers"][0]["values"], ["0941"])
        self.assertEqual(info["sessionDescriptions"], [{"name": "SESD_TEST", "sessionReference": "SES_TEST"}])
        self.assertNotIn("firmwareReleaseDate", info)

    def test_incomplete_pdx_is_rejected_without_extracting_payloads(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "incomplete.pdx"
            with zipfile.ZipFile(path, "w") as package:
                package.writestr("index.xml", "<CATALOG><FILE>missing.bin</FILE></CATALOG>")
            with self.assertRaisesRegex(ValueError, "member missing"):
                file_metadata(path)
            self.assertEqual([p.name for p in Path(tmp).iterdir()], ["incomplete.pdx"])

    def test_archive_verification_rejects_changed_original(self):
        with tempfile.TemporaryDirectory() as tmp:
            archive = Path(tmp)
            (archive / "files").mkdir()
            (archive / "files/test.xml").write_bytes(b"changed")
            original = b"initial"
            f = {"relativePath": "test.xml", "sourcePath": "source/test.xml", "bytes": len(original),
                 "sha256": hashlib.sha256(original).hexdigest(), "metadata": {}}
            (archive / "manifest.json").write_text(json.dumps({"files": [f]}))
            (archive / "source-hashes.json").write_text(json.dumps([f]))
            (archive / "flash-index.json").write_text(json.dumps({"rules": []}))
            with self.assertRaisesRegex(ValueError, "integrity failure"):
                verify_archive(archive)


if __name__ == "__main__":
    unittest.main()
