import tempfile
import unittest
from pathlib import Path
from scripts.import_piwis_workshop import build_index


def rule(description, extra="", current="", target_extra="", product="TEST", target_number="TEST-SW", target_link="TEST-ECU"):
    return f"""<FLASHREGEL><BESCHREIBUNG>{description}</BESCHREIBUNG><ISTSTAND>
<PRODUKTSCHLUESSEL>{product}</PRODUKTSCHLUESSEL><AUSSTATTUNG>250&amp;!708</AUSSTATTUNG>
<STEUERGERAETE>{current}</STEUERGERAETE>{extra}</ISTSTAND><SOLLSTAND><STEUERGERAETE>
<STEUERGERAET><LOGICALLINK>{target_link}</LOGICALLINK><PTNR>{target_number}</PTNR>{target_extra}</STEUERGERAET>
</STEUERGERAETE></SOLLSTAND></FLASHREGEL>"""


class ImportTests(unittest.TestCase):
    def run_import(self, rows, additional=None):
        with tempfile.TemporaryDirectory(prefix="piwis-import-test-") as tmp:
            p = Path(tmp)
            for name in ["DME-flash-rules.xml", "GETRIEBE-flash-rules.xml"]:
                (p / name).write_text("<ROOT>" + rows + "</ROOT>", encoding="utf8")
            for name, content in (additional or {}).items():
                q = p / "flash-rules" / name
                q.parent.mkdir(exist_ok=True)
                q.write_text("<ROOT>" + content + "</ROOT>", encoding="utf8")
            return build_index(p)

    def test_excludes_shared_991_and_preserves_raw_conditions(self):
        doc = self.run_import(rule("981S TEST") + rule("982 TEST") + rule("991 TEST") + rule("991II TEST"))
        self.assertEqual([r["generation"] for r in doc["rules"]], ["981", "982", "981", "982"])
        self.assertEqual(doc["rules"][0]["conditions"][1]["values"], ["250&!708"])
        self.assertEqual(len({r["id"] for r in doc["rules"]}), 4)
        self.assertEqual(len(doc["sources"][0]["sha256"]), 64)

    def test_current_ecu_conditions_are_not_silently_dropped(self):
        with self.assertRaisesRegex(ValueError, "Current-ECU"):
            self.run_import(rule("981 TEST", current="<STEUERGERAET>TEST</STEUERGERAET>"))

    def test_unknown_conditions_are_not_silently_dropped(self):
        with self.assertRaisesRegex(ValueError, "Unsupported rule condition"):
            self.run_import(rule("982 TEST", extra="<UNKNOWN>TEST</UNKNOWN>"))
        with self.assertRaisesRegex(ValueError, "Unsupported target condition"):
            self.run_import(rule("982 TEST", target_extra="<UNKNOWN>TEST</UNKNOWN>"))

    def test_hardware_software_conditions_and_product_family_are_preserved(self):
        current = "<STEUERGERAET><LOGICALLINK>AIRBAG</LOGICALLINK><HWTNR>HW1/HW2</HWTNR><PTNR>SW1/SW2</PTNR><SWVERSION>2040</SWVERSION></STEUERGERAET>"
        doc = self.run_import("", {"AIRBAG_9x1.xml": rule("Airbag hardware TEST", current=current, product="F83CA/F83KA")})
        self.assertEqual(len(doc["rules"]), 1)
        self.assertEqual(doc["rules"][0]["generation"], "982")
        self.assertEqual(doc["rules"][0]["familyEvidence"], "product-key")
        self.assertEqual(doc["rules"][0]["currentEcus"][0]["conditions"][0]["values"], ["HW1/HW2"])
        self.assertEqual(doc["rules"][0]["currentEcus"][0]["conditions"][2]["values"], ["2040"])

    def test_no_flash_and_dataset_are_not_firmware_targets(self):
        doc = self.run_import("", {
            "GATEWAY.xml": rule("NO FLASH TEST", product="", target_number="noflash", target_link=""),
            "SCHEINWERFER_LED_LINKS_9X1_DS.xml": rule("Dataset TEST", product="", target_number="", target_extra="<SESSIONNAME>SESD_TEST</SESSIONNAME>"),
        })
        self.assertEqual(len(doc["rules"]), 4)
        blocked = next(r for r in doc["rules"] if r["kind"] == "blocked")
        self.assertIsNone(blocked["targets"][0]["softwarePartNumber"])
        self.assertEqual(blocked["familyEvidence"], "shared-platform")
        dataset = next(r for r in doc["rules"] if r["kind"] == "dataset")
        self.assertEqual(dataset["targets"][0]["session"], "SESD_TEST")
        self.assertIsNone(dataset["targets"][0]["softwarePartNumber"])

    def test_explicit_991_products_cannot_be_mapped_by_a_981_description(self):
        doc = self.run_import(rule("981 misleading", product="F91CA/F91KA"))
        self.assertEqual(doc["rules"], [])


if __name__ == "__main__":
    unittest.main()
