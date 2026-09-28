"""Workshop registry: 35 groups, hash-bound facts, portable synthetics."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.diagnostics import workshop_registry as wr
from scripts.diagnostics.workshop_registry import (
    BASELINE,
    build_registry,
    catalog_observed,
    main,
    sha256_file,
    toc_hits,
    verify_source,
    write_registry,
)

REPO = Path(__file__).resolve().parents[3]
COVERAGE = REPO / "data" / "seed" / "diagnostics" / "coverage-981-982.v1.json"
WIRE981 = Path(r"C:/Users/Ric/Desktop/981/接线图/981.pdf")
WIRE982 = Path(r"C:/Users/Ric/Desktop/981/接线图/982.pdf")
MAN981 = Path(r"C:/Users/Ric/Desktop/981/Boxster BoxsterS BoxsterGTS (981).pdf")
MAN982 = Path(r"C:/Users/Ric/Desktop/981/Porsche 718 Boxster BoxsterS BoxsterGTS (982).pdf")


def _core_originals() -> bool:
    return all(p.is_file() for p in (WIRE981, WIRE982, MAN981, MAN982))


def _missing_build(**extra):
    empty = Path(tempfile.mkdtemp())
    kw = dict(
        wiring_981=empty / "nope.pdf",
        wiring_982=None,
        wiring_gt4=None,
        manual_981=None,
        manual_982=None,
        manual_991=None,
        extract=empty / "extract",
        render_png=False,
    )
    kw.update(extra)
    return build_registry(**kw)


class WorkshopRegistryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.full = build_registry(render_png=False) if _core_originals() else None

    def test_preserves_35_menu_groups_and_dsn(self):
        src = json.loads(COVERAGE.read_text(encoding="utf-8"))["menu_ecus"]
        data = self.full or _missing_build()
        self.assertEqual(len(src), 35)
        self.assertEqual(data["groupCount"], 35)
        by_id = {g["ecuId"]: g for g in data["groups"]}
        for ecu in src:
            g = by_id[int(ecu["ecu_id"])]
            self.assertEqual(g["dsnModules"], list(ecu["dsn_modules"]))
            self.assertFalse(g["fittedClaim"])
            self.assertFalse(g["executionEnabled"])

    @unittest.skipUnless(_core_originals(), "proprietary 981/982 originals absent")
    def test_generation_branches_and_both_diagrams_indexed(self):
        gw = next(g for g in self.full["groups"] if g["ecuId"] == 9)
        a = gw["generations"]["981"]
        b = gw["generations"]["982"]
        self.assertTrue(any(e.get("kind") == "wiring_diagram" and e.get("generation") == "981" for e in a["evidence"]))
        self.assertTrue(any(e.get("kind") == "wiring_diagram" and e.get("generation") == "982" for e in b["evidence"]))
        src = {s["id"]: s for s in self.full["sources"]}
        self.assertTrue(src["wiring-981"]["baselineMatch"])
        self.assertTrue(src["wiring-982"]["baselineMatch"])
        self.assertEqual(src["wiring-982"]["shaSource"], "hashed_this_run")

    @unittest.skipUnless(_core_originals(), "proprietary 981/982 originals absent")
    def test_982_psm_epb_visual_sheets_on_matching_hash(self):
        psm = next(g for g in self.full["groups"] if g["ecuId"] == 5)
        epb = next(g for g in self.full["groups"] if g["ecuId"] == 50)
        v5 = [e for e in psm["generations"]["982"]["evidence"] if e.get("role") == "visual_sheet_verified"]
        v50 = [e for e in epb["generations"]["982"]["evidence"] if e.get("role") == "visual_sheet_verified"]
        self.assertEqual(v5[0]["page1based"], 62)
        self.assertEqual(v50[0]["page1based"], 64)
        self.assertEqual(psm["generations"]["982"]["wiringStatus"], "partial")
        self.assertEqual(epb["generations"]["982"]["wiringStatus"], "partial")
        self.assertTrue(v5[0]["notADiagnosticAddress"])
        self.assertNotIn("txId", v5[0])

    @unittest.skipUnless(_core_originals(), "proprietary 981/982 originals absent")
    def test_ecu9_obd_path_hash_bound(self):
        gw = next(g for g in self.full["groups"] if g["ecuId"] == 9)
        for gen, page, year in (("981", 11, "2013 (D)"), ("982", 12, "2017 (H)")):
            hits = [e for e in gw["generations"][gen]["evidence"] if e.get("kind") == "obd_gateway_pin_path"]
            self.assertEqual(hits[0]["page1based"], page)
            self.assertEqual(hits[0]["printedModelYear"], year)
            self.assertTrue(hits[0]["sourcePinAssignmentsVerified"])
            self.assertFalse(hits[0]["liveContinuityVerified"])
            self.assertTrue(hits[0]["notADiagnosticAddress"])
            self.assertEqual(hits[0]["pinPairs"][0]["fromPin"], "A6")

    def test_no_pin_promoted_to_diagnostic_address(self):
        data = self.full or _missing_build()
        pinish = ("C6", "TP77", "TP19", "端子 15 诊断")
        for g in data["groups"]:
            for gen in g["generations"].values():
                addr = gen["diagnosticAddress"]
                if addr:
                    blob = json.dumps(addr)
                    for tok in pinish:
                        self.assertNotIn(tok, blob)
                for e in gen["evidence"]:
                    if e.get("kind") in ("wiring_diagram", "visual_sheet", "obd_gateway_pin_path"):
                        self.assertTrue(e["notADiagnosticAddress"])
                        self.assertNotIn("txId", e)

    def test_observed_addresses_only_catalog_pairs(self):
        data = self.full or _missing_build()
        seen = []
        for g in data["groups"]:
            for gen_name, gen in g["generations"].items():
                if gen["addressProvenance"] == "observed":
                    seen.append((g["ecuId"], gen_name, gen["diagnosticAddress"]["txId"], gen["diagnosticAddress"]["rxId"]))
                else:
                    self.assertIsNone(gen["diagnosticAddress"])
        self.assertEqual(sorted(seen), [(1, "981", "7E0", "7E8"), (9, "981", "710", "77A")])
        for g in data["groups"]:
            self.assertIsNone(g["generations"]["982"]["diagnosticAddress"])

    def test_address_missing_catalog_no_observed_promotion(self):
        empty = Path(tempfile.mkdtemp())
        cat = empty / "catalog.json"
        cat.write_text('{"schemaVersion":1,"profiles":[]}', encoding="utf-8")
        data = _missing_build(catalog_path=cat)
        gw = next(g for g in data["groups"] if g["ecuId"] == 9)
        self.assertEqual(gw["generations"]["981"]["addressProvenance"], "unobserved")
        self.assertIsNone(gw["generations"]["981"]["diagnosticAddress"])
        self.assertEqual(catalog_observed(cat), {})

    def test_missing_optional_originals_usable_not_fabricated(self):
        data = _missing_build()
        self.assertEqual(data["groupCount"], 35)
        src = {s["id"]: s for s in data["sources"]}
        self.assertEqual(src["wiring-981"]["status"], "missing")
        self.assertEqual(src["wiring-982"]["status"], "missing")
        dme = next(g for g in data["groups"] if g["ecuId"] == 1)
        self.assertEqual(dme["generations"]["981"]["wiringStatus"], "absent")

    @unittest.skipUnless(_core_originals(), "proprietary 981/982 originals absent")
    def test_981_982_preconditions_not_merged(self):
        dme = next(g for g in self.full["groups"] if g["ecuId"] == 1)
        a = dme["generations"]["981"]["factoryOps"]["read"]
        b = dme["generations"]["982"]["factoryOps"]["read"]
        self.assertEqual(a["chargerAmps"], 40)
        self.assertEqual(b["chargerAmps"], 90)
        self.assertFalse(dme["generations"]["981"]["factoryOps"]["executionEnabled"])

    def test_changed_source_hash_does_not_inherit_facts(self):
        empty = Path(tempfile.mkdtemp())
        fake = empty / "Boxster BoxsterS BoxsterGTS (981).pdf"
        fake.write_bytes(b"%PDF-1.4 fake")
        data = _missing_build(manual_981=fake)
        ident = next(s for s in data["sources"] if s["id"] == "manual-981")
        self.assertFalse(ident["baselineMatch"])
        dme = next(g for g in data["groups"] if g["ecuId"] == 1)
        self.assertEqual(dme["generations"]["981"]["factoryOps"]["read"], "unresolved")

    def test_missing_hash_source_does_not_get_verified_obd_pins(self):
        from scripts.diagnostics.workshop_registry import bind_obd_gateway_facts, obd_path_hits

        data = _missing_build()
        src = next(s for s in data["sources"] if s["id"] == "wiring-981")
        self.assertFalse(src.get("baselineMatch"))
        self.assertIsNone(bind_obd_gateway_facts("wiring-981", src))
        gw = next(g for g in data["groups"] if g["ecuId"] == 9)
        self.assertFalse(any(e.get("kind") == "obd_gateway_pin_path" for e in gw["generations"]["981"]["evidence"]))
        self.assertEqual(obd_path_hits(9, "981", {"wiring-981": src}), [])

    def test_same_size_and_pagecount_different_bytes_are_hashed(self):
        d = Path(tempfile.mkdtemp())
        a = d / "982.pdf"
        b = d / "982-mod.pdf"
        a.write_bytes(b"PDF-A" + b"\x00" * 4995)
        b.write_bytes(b"PDF-B" + b"\x00" * 4995)
        self.assertEqual(a.stat().st_size, b.stat().st_size)
        digest_a = sha256_file(a)
        self.assertNotEqual(digest_a, sha256_file(b))
        with patch.dict(wr.BASELINE["wiring-982"], {"sha256": digest_a, "pages": 79, "bytes": a.stat().st_size}):
            with patch.object(wr, "page_count", return_value=79):
                ia = verify_source(a, "wiring-982")
                ib = verify_source(b, "wiring-982")
                data_b = build_registry(
                    wiring_981=None,
                    wiring_982=b,
                    wiring_gt4=None,
                    manual_981=None,
                    manual_982=None,
                    manual_991=None,
                    extract=d / "ex",
                )
        self.assertTrue(ia["baselineMatch"])
        self.assertEqual(ia["shaSource"], "hashed_this_run")
        self.assertFalse(ib["baselineMatch"])
        self.assertEqual(ib["sha256"], sha256_file(b))
        psm = next(g for g in data_b["groups"] if g["ecuId"] == 5)
        self.assertFalse(any(e.get("role") == "visual_sheet_verified" for e in psm["generations"]["982"]["evidence"]))

    def test_visual_override_only_when_hash_matches(self):
        d = Path(tempfile.mkdtemp())
        a = d / "982.pdf"
        a.write_bytes(b"WIRE982-MATCH" + b"\x00" * 100)
        digest_a = sha256_file(a)
        with patch.dict(wr.BASELINE["wiring-982"], {"sha256": digest_a, "pages": 79, "bytes": a.stat().st_size}):
            with patch.object(wr, "page_count", return_value=79):
                ok = build_registry(
                    wiring_981=None,
                    wiring_982=a,
                    wiring_gt4=None,
                    manual_981=None,
                    manual_982=None,
                    manual_991=None,
                    extract=d / "ex",
                )
        psm = next(g for g in ok["groups"] if g["ecuId"] == 5)
        epb = next(g for g in ok["groups"] if g["ecuId"] == 50)
        v5 = [e for e in psm["generations"]["982"]["evidence"] if e.get("role") == "visual_sheet_verified"]
        self.assertEqual(v5[0]["page1based"], 62)
        self.assertEqual(
            [e["page1based"] for e in epb["generations"]["982"]["evidence"] if e.get("role") == "visual_sheet_verified"],
            [64],
        )

    def test_gt4_and_991_isolated(self):
        data = self.full or _missing_build()
        self.assertEqual(data["specialVariants"]["factory991"]["role"], "method_reference_only")
        self.assertTrue(data["specialVariants"]["gt4Clubsport"]["notProductionDefault"])
        for g in data["groups"]:
            self.assertNotIn("991", g["generations"])
            blob = json.dumps(g)
            self.assertNotIn("981GT4_CS.pdf", blob)
            self.assertNotIn("wiring-gt4cs", blob)

    def test_global_code_index_does_not_resolve_every_ecu(self):
        data = self.full or _missing_build()
        cam = next(g for g in data["groups"] if g["ecuId"] == 74)
        self.assertIsNotNone(data.get("globalManualCodeIndex"))
        kinds_981 = {e.get("kind") for e in cam["generations"]["981"]["evidence"]}
        self.assertNotIn("manual_code_index", kinds_981)

    def test_toc_helper_indexes_982_gateway(self):
        hits = toc_hits(
            [[1, "07_1 网关 表单 1", 12], [1, "07_2 网关 表单 2", 13]],
            ("网关",),
            generation="982",
            source_id="wiring-982",
            basename="982.pdf",
        )
        self.assertEqual(len(hits), 2)
        self.assertEqual(hits[0]["page1based"], 12)

    def test_cli_writes_ascii_json_without_originals(self):
        dest = Path(tempfile.mkdtemp()) / "reg.json"
        self.assertEqual(
            main(
                [
                    "--out",
                    str(dest),
                    "--wiring-981",
                    "",
                    "--wiring-982",
                    "",
                    "--wiring-gt4",
                    "",
                    "--manual-981",
                    "",
                    "--manual-982",
                    "",
                    "--manual-991",
                    "",
                ]
            ),
            0,
        )
        parsed = json.loads(dest.read_text(encoding="ascii"))
        self.assertEqual(parsed["groupCount"], 35)
        self.assertNotIn("electronObdCheckout", parsed)

    @unittest.skipUnless(_core_originals(), "proprietary 981/982 originals absent")
    def test_write_roundtrip_group_count(self):
        dest = Path(tempfile.mkdtemp()) / "r.json"
        write_registry(self.full, dest)
        again = json.loads(dest.read_text(encoding="ascii"))
        self.assertEqual(again["groupCount"], 35)
        self.assertEqual(again["specialVariants"]["gt4Clubsport"]["locator"], BASELINE["wiring-gt4cs"]["basename"])


if __name__ == "__main__":
    unittest.main()
