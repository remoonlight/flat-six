"""Offline qualification / plan. Captures and variants optional."""
from __future__ import annotations

import ast
import json
import sys
import tempfile
import unittest
from pathlib import Path

from scripts.diagnostics.catalog import load_catalog, profile_by_id
from scripts.diagnostics.offline import (
    DEFAULT_VARIANTS,
    build_plan,
    group_measurements,
    load_registry,
    main,
    summary_doc,
)
from scripts.diagnostics.qualification import qualify_identity

REPO = Path(__file__).resolve().parents[3]
OFFLINE_PY = REPO / "scripts" / "diagnostics" / "offline.py"
QUAL_PY = REPO / "scripts" / "diagnostics" / "qualification.py"


def _dme_fp(**over):
    cat = load_catalog()
    c = dict(profile_by_id(cat, "porsche-981-2014-dme")["identityConstraints"])
    ident = {k: c[k] for k in ("dsn", "software", "hardware", "porschePart", "hardwarePart")}
    ident.update(over.pop("identity_over", {}))
    fp = {"generation": "981", "ecuId": 1, "identity": ident}
    fp.update(over)
    return fp


def _gw_fp():
    cat = load_catalog()
    c = dict(profile_by_id(cat, "porsche-981-2014-gateway")["identityConstraints"])
    ident = {
        k: c[k]
        for k in ("dsn", "system", "identification", "porschePart", "hardwarePart", "hardware", "dataRecord")
    }
    return {"generation": "981", "ecuId": 9, "identity": ident}


class TestNoSerial(unittest.TestCase):
    def test_modules_do_not_import_serial(self):
        for path in (OFFLINE_PY, QUAL_PY):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    self.assertFalse(any(a.name.split(".")[0] == "serial" for a in node.names))
                if isinstance(node, ast.ImportFrom) and node.module:
                    self.assertNotEqual(node.module.split(".")[0], "serial")
        sys.modules.pop("serial", None)
        import importlib

        import scripts.diagnostics.offline as off

        importlib.reload(off)
        self.assertNotIn("serial", sys.modules)


class TestIdentity(unittest.TestCase):
    def test_empty_or_partial_catalog_constraints_cannot_match(self):
        for constraints in ({}, {"dsn": "P200"}):
            catalog = load_catalog()
            profile_by_id(catalog, "porsche-981-2014-dme")["identityConstraints"] = constraints
            self.assertEqual(qualify_identity(_dme_fp(), catalog)["status"], "catalog_constraints_invalid")

    def test_ignored_fields_do_not_change_identity_match(self):
        fp = _dme_fp(identity_over={"vin": 123, "extra": [42]})
        result = qualify_identity(fp)
        self.assertEqual(result["status"], "observed_profile_match")
        self.assertIn("vin", result["ignoredFields"])

    def test_mismatched_wire_payload_remains_unsupported(self):
        rec = {"at": 1, "rawSID": 0x22, "wireSID": 0x22,
               "byteOffset": 0, "bitOffset": 0, "fields_status": "ok",
               "formula": "IDENTICAL:BitLength=8,DataType=A_UINT8",
               "disabledreadrequestcandidate": {"payload_hex": "2101", "status": "ok"}}
        result = group_measurements([rec])
        self.assertEqual(result["requestGroups"], [])
        self.assertEqual(result["unsupported"][0]["reason"], "wire-payload-inconsistent")

    def test_full_dme_match_and_static_variant(self):
        q = qualify_identity(_dme_fp())
        self.assertEqual(q["status"], "observed_profile_match")
        self.assertFalse(q["independentLiveVerified"])
        self.assertEqual(q["catalogProfileId"], "porsche-981-2014-dme")
        self.assertEqual(q["variantLink"]["name"], "SDI9_1_981_3_4L_EU5")
        self.assertTrue(q["vinExcluded"])

    def test_full_gw_match(self):
        q = qualify_identity(_gw_fp())
        self.assertEqual(q["status"], "observed_profile_match")
        self.assertEqual(q["variantLink"]["name"], "CAN_CAN_Gateway_A7_1")

    def test_missing_and_mismatch_and_p200_only(self):
        q = qualify_identity({"generation": "981", "ecuId": 1, "identity": {"dsn": "P200"}})
        self.assertEqual(q["status"], "incomplete")
        self.assertIn("software", q["missingRequired"])
        self.assertIsNone(q["catalogProfileId"])
        q2 = qualify_identity(_dme_fp(identity_over={"software": "OTHER"}))
        self.assertEqual(q2["status"], "mismatch")
        self.assertTrue(q2["mismatches"])

    def test_padding_not_substring(self):
        q = qualify_identity(_dme_fp(identity_over={"dsn": "P200  \x00"}))
        self.assertEqual(q["status"], "observed_profile_match")
        q2 = qualify_identity(_dme_fp(identity_over={"dsn": "xP200x"}))
        self.assertEqual(q2["status"], "mismatch")

    def test_wrong_generation_same_fingerprint(self):
        fp = _dme_fp()
        fp["generation"] = "982"
        q = qualify_identity(fp)
        self.assertEqual(q["status"], "wrong_generation")
        self.assertIsNone(q["catalogProfileId"])
        self.assertTrue(q["samePartDoesNotInherit"])

    def test_unknown_ecu(self):
        q = qualify_identity({"generation": "981", "ecuId": 4, "identity": {"dsn": "P200"}})
        self.assertEqual(q["status"], "unknown_ecu")

    def test_interior_nul_and_malformed(self):
        q = qualify_identity(_dme_fp(identity_over={"dsn": "P2\x0000"}))
        self.assertEqual(q["status"], "mismatch")
        self.assertEqual(qualify_identity(["981"], catalog={"profiles": []})["status"], "malformed_fingerprint")
        self.assertEqual(qualify_identity({"generation": "981", "ecuId": 1, "identity": ["x"]})["status"], "malformed_identity")
        self.assertEqual(qualify_identity({"generation": "981", "ecuId": True, "identity": {"dsn": "P200"}})["status"], "malformed_ecu_id")
        self.assertEqual(qualify_identity(_dme_fp(), catalog={"profiles": []})["status"], "catalog_profile_missing")

    def test_plan_generation_mismatch(self):
        q = qualify_identity(_dme_fp(), expected_generation="982")
        self.assertEqual(q["status"], "plan_generation_mismatch")
        plan = build_plan("982", identity=_dme_fp())
        self.assertEqual(plan["identityQualification"]["status"], "plan_generation_mismatch")
        self.assertFalse(plan["identityQualification"]["observedProfileMatch"])


class TestPlan(unittest.TestCase):
    def test_35_groups_both_generations_no_address_inference(self):
        for gen in ("981", "982"):
            plan = build_plan(gen)
            self.assertEqual(plan["groupCount"], 35)
            self.assertEqual(len(plan["groups"]), 35)
            self.assertFalse(plan["executionEnabled"])
            self.assertFalse(plan["liveVerified"])
            ids = [g["ecuId"] for g in plan["groups"]]
            self.assertEqual(len(ids), len(set(ids)))
            for g in plan["groups"]:
                self.assertFalse(g["fittedClaim"])
                self.assertFalse(g["clearCoding"]["enabled"])
                self.assertIsNone(g["clearCoding"]["writePayload"])
                if gen == "982" or g["ecuId"] not in (1, 9):
                    self.assertIsNone(g["diagnosticAddress"])
                if gen == "981" and g["ecuId"] not in (1, 9):
                    self.assertEqual(g.get("addressNote"), "address-not-inferred")
            ov = plan["checklist"]["observedVsUnobserved"]
            if gen == "981":
                self.assertEqual(ov["captureObservedEcuIds981"], [1, 9])
                self.assertEqual(ov["identityQualifiedEcuIds"], [])
            else:
                self.assertEqual(ov["generation982CapturedAddresses"], [])
                self.assertEqual(ov["identityQualifiedEcuIds"], [])

    def test_matched_identity_exposes_only_that_ecu_address(self):
        plan = build_plan("981", identity=_dme_fp())
        by = {g["ecuId"]: g for g in plan["groups"]}
        self.assertEqual(by[1]["diagnosticAddress"]["txId"], "7E0")
        self.assertIsNone(by[9]["diagnosticAddress"])
        self.assertEqual(by[1]["variantLink"]["profile_id"], "9x1:DME_BDE_Continental:SDI9_1_981_3_4L_EU5")
        self.assertIsNone(by[9]["variantLink"])
        self.assertEqual(plan["checklist"]["observedVsUnobserved"]["identityQualifiedEcuIds"], [1])
        self.assertEqual(plan["checklist"]["observedVsUnobserved"]["captureObservedEcuIds981"], [1, 9])
        fo = by[1]["factoryOps"]["read"]
        if isinstance(fo, dict):
            self.assertEqual(plan["checklist"]["factoryProcedure"]["batteryChargerMinimumFromReviewedPages"], fo.get("chargerAmps"))
        self.assertTrue(by[1]["evidence"])
        plan2 = build_plan("982")
        by2 = {g["ecuId"]: g for g in plan2["groups"]}
        self.assertEqual(plan2["checklist"]["observedVsUnobserved"]["captureObservedEcuIds981"], [1, 9])
        self.assertIsNone(by2[1]["diagnosticAddress"])
        dme_coding = by2[1]["factoryOps"]["coding"]
        gw_coding = by2[9]["factoryOps"]["coding"]
        self.assertTrue(dme_coding == "unresolved" or (isinstance(dme_coding, dict) and dme_coding.get("wm") != "903555"))
        if isinstance(gw_coding, dict):
            self.assertEqual(gw_coding.get("wm"), "903555")
            self.assertEqual(plan2["checklist"]["factoryProcedure"]["gatewayReplacementWm903555"]["role"], "gateway-replacement-reference-only")
        self.assertTrue(plan2["checklist"]["factoryProcedure"]["componentProtectionNotUniversalCoding"])
        self.assertTrue(plan2["checklist"]["factoryProcedure"]["doNotPromote991OrClubsportAsProductionDefault"])

    def test_missing_registry_explicit(self):
        info = load_registry(Path(tempfile.mkdtemp()) / "nope.json")
        self.assertFalse(info["present"])
        plan = build_plan("981", registry_info=info)
        self.assertIn("workshop-registry", plan["absentInputs"])
        self.assertEqual(plan["groupCount"], 35)
        self.assertIsNone(plan["checklist"]["factoryProcedure"]["batteryChargerMinimumFromReviewedPages"])
        self.assertTrue(plan["checklist"]["factoryProcedure"]["factsUnknown"])

    def test_measurement_group_dedup_sid31_alias(self):
        recs = [
            {
                "at": 1,
                "name": "a",
                "rawSID": 0x22,
                "wireSID": 0x22,
                "pid": 0xF187,
                "byteOffset": 3,
                "bitOffset": 0,
                "fields_status": "ok",
                "formula": {"text": "IDENTICAL:DataType=A_UINT8,BitLength=8,BitMask=0,HighLow=1"},
                "disabledreadrequestcandidate": {"payload_hex": "22F187", "status": "ok", "executionEnabled": False},
            },
            {
                "at": 2,
                "name": "b",
                "rawSID": 0x22,
                "wireSID": 0x22,
                "pid": 0xF187,
                "byteOffset": 10,
                "bitOffset": 0,
                "fields_status": "ok",
                "formula": {"text": "IDENTICAL:DataType=A_UINT8,BitLength=8,BitMask=0,HighLow=1"},
                "disabledreadrequestcandidate": {"payload_hex": "22F187", "status": "ok", "executionEnabled": False},
            },
            {
                "at": 3,
                "name": "c",
                "rawSID": 0x31,
                "wireSID": 0x22,
                "pid": 0x06F4,
                "byteOffset": 0,
                "bitOffset": 0,
                "fields_status": "ok",
                "formula": {"text": "IDENTICAL:DataType=A_UINT8,BitLength=8,BitMask=0,HighLow=1"},
                "disabledreadrequestcandidate": {
                    "payload_hex": "2206F4",
                    "status": "ok",
                    "rawSID": 0x31,
                    "wireSID": 0x22,
                    "executionEnabled": False,
                },
            },
            {
                "at": 4,
                "name": "missing",
                "rawSID": 0x21,
                "wireSID": None,
                "pid": 1,
                "byteOffset": None,
                "bitOffset": 0,
                "fields_status": "unsupported_sid",
                "formula": {"text": ""},
                "disabledreadrequestcandidate": {"payload_hex": None, "status": "unsupported_sid"},
            },
            {
                "at": 5,
                "name": "mask",
                "rawSID": 0x22,
                "wireSID": 0x22,
                "pid": 1,
                "byteOffset": 0,
                "bitOffset": 0,
                "fields_status": "ok",
                "formula": {
                    "text": "TEXTTABLE:DataType=A_UINT32,[0x00]->0xF000010F;BitLength=8,BitMask=1,HighLow=1"
                },
                "disabledreadrequestcandidate": {"payload_hex": "220001", "status": "ok", "executionEnabled": False},
            },
            {
                "at": 6,
                "name": "sid31-bad",
                "rawSID": 0x31,
                "wireSID": 0x21,
                "pid": 16,
                "byteOffset": 0,
                "bitOffset": 0,
                "fields_status": "ok",
                "formula": {"text": "IDENTICAL:DataType=A_UINT8,BitLength=8,BitMask=0,HighLow=1"},
                "disabledreadrequestcandidate": {"payload_hex": "2110", "status": "ok"},
            },
        ]
        g = group_measurements(recs)
        ats = {r["at"] for r in recs}
        placed = {r["at"] for r in g["missing"]} | {r["at"] for r in g["unsupported"]} | {
            r["at"] for grp in g["requestGroups"] for r in grp["records"]
        }
        self.assertEqual(ats, placed)
        pair = next(x for x in g["requestGroups"] if x["requestHex"] == "22F187")
        self.assertEqual(pair["recordCount"], 2)
        self.assertEqual({r["at"] for r in pair["records"]}, {1, 2})
        self.assertTrue(all(r["formulaOk"] for r in pair["records"]))
        self.assertEqual(pair["minDataBytes"], 11)
        self.assertEqual(pair["minPduBytes"], 14)
        self.assertEqual(pair["minResponseLength"], 11)
        self.assertEqual(pair["minResponseLengthKind"], "exact")
        self.assertTrue(pair["countsContainDuplicates"])
        self.assertGreaterEqual(g["groupedRecordCount"], 3)
        z = next(r for grp in g["requestGroups"] for r in grp["records"] if r["at"] == 3)
        self.assertEqual(z["byteOffset"], 0)
        self.assertEqual(z["bitOffset"], 0)
        self.assertEqual(g["sid31AliasedTo22Count"], 1)
        mask_u = next((u for u in g["unsupported"] if u["at"] == 5), None)
        if mask_u is not None:
            self.assertFalse(mask_u["formulaOk"])
        else:
            self.assertTrue(any(r["at"] == 5 for grp in g["requestGroups"] for r in grp["records"]))
        self.assertTrue(any(u["at"] == 6 for u in g["unsupported"]))
        self.assertTrue(any(m["at"] == 4 for m in g["missing"]))
        self.assertTrue(all(x["candidateDisabled"] for x in g["requestGroups"]))
        self.assertFalse(any(x["executionEnabled"] for x in g["requestGroups"]))

    def test_clear_write_disabled_and_refuse_send(self):
        plan = build_plan("981")
        self.assertIsNone(plan["writePayload"])
        self.assertIn("14", plan["checklist"]["blockedSids"])
        self.assertEqual(main(["plan", "--generation", "981", "--out", "x", "--send", "foo"]), 2)

    def test_cli_identity_errors_nonzero(self):
        td = tempfile.TemporaryDirectory()
        try:
            bad = Path(td.name) / "bad.json"
            bad.write_text("{", encoding="utf-8")
            self.assertEqual(main(["match", "--identity", str(bad)]), 2)
            lst = Path(td.name) / "list.json"
            lst.write_text("[]", encoding="utf-8")
            self.assertEqual(main(["match", "--identity", str(lst)]), 2)
            inc = Path(td.name) / "inc.json"
            inc.write_text(json.dumps({"generation": "981", "ecuId": 1, "identity": {"dsn": "P200"}}), encoding="utf-8")
            self.assertEqual(main(["match", "--identity", str(inc)]), 1)
        finally:
            td.cleanup()

    def test_cli_identity_free_and_match(self):
        td = tempfile.TemporaryDirectory()
        try:
            out = Path(td.name) / "p.json"
            self.assertEqual(main(["plan", "--generation", "981", "--out", str(out)]), 0)
            doc = json.loads(out.read_text(encoding="utf-8"))
            self.assertEqual(doc["groupCount"], 35)
            ident = Path(td.name) / "id.json"
            ident.write_text(json.dumps(_dme_fp()), encoding="utf-8")
            mout = Path(td.name) / "m.json"
            self.assertEqual(main(["match", "--identity", str(ident), "--out", str(mout)]), 0)
            m = json.loads(mout.read_text(encoding="utf-8"))
            self.assertEqual(m["identityQualification"]["status"], "observed_profile_match")
            s = summary_doc()
            self.assertEqual(s["menuEcuCount"], 35)
        finally:
            td.cleanup()


@unittest.skipUnless(DEFAULT_VARIANTS.is_file(), "variants.jsonl absent")
class TestExportedVariant(unittest.TestCase):
    def test_selected_dme_variant_keeps_all_measurement_rows(self):
        pid = "9x1:DME_BDE_Continental:SDI9_1_981_3_4L_EU5"
        plan = build_plan("981", identity=_dme_fp(), variant_id=pid, variants_path=DEFAULT_VARIANTS)
        sel = plan["selectedVariant"]
        self.assertTrue(sel["present"], sel)
        meas = sel["measurements"]
        n = meas["measurementRecordCount"]
        self.assertGreater(n, 0)
        self.assertEqual(meas["groupedRecordCount"] + len(meas["missing"]) + len(meas["unsupported"]), n)
        self.assertFalse(sel["physicalFitClaim"])
        self.assertTrue(meas["candidateDisabled"])


if __name__ == "__main__":
    unittest.main()
