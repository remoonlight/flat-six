"""Workbench JSON API. No serial, no vehicle send."""
from __future__ import annotations

import ast
import functools
import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

from scripts.diagnostics.workbench import DEFAULT_VARIANTS, FLAGS, handle, main
from scripts.diagnostics.catalog import load_catalog, profile_by_id

REPO = Path(__file__).resolve().parents[3]
WB_PY = REPO / "scripts" / "diagnostics" / "workbench.py"
EU5 = "9x1:DME_BDE_Continental:SDI9_1_981_3_4L_EU5"
CRUISE = {
    "at": 4047899,
    "byteOffset": 0,
    "bitOffset": 0,
    "name": "巡航控制",
    "readSID": 0x21, "pid": 1, "read_request_candidate_hex": "2101",
    "formula": {
        "id_hex": "F0000078",
        "text": "TEXTTABLE:DataType=A_UINT32,[0x00]->0xF000010F;[0x01]->0xF0000110;LengthInfo=Standard,BitLength=1,BitMask=0,HighLow=1;",
    },
    "enumText": {"F0000110": "是", "F000010F": "否"},
    "labels": [{"id_hex": "80000018", "text": "巡航控制"}],
    "source": {"file": "9X1_ALLDATA.BIN.dec", "offset": 4035362, "variant": "EU5"},
}


def _dme_fp():
    cat = load_catalog()
    c = dict(profile_by_id(cat, "porsche-981-2014-dme")["identityConstraints"])
    ident = {k: c[k] for k in ("dsn", "software", "hardware", "porschePart", "hardwarePart")}
    return {"generation": "981", "ecuId": 1, "identity": ident}


def _fixture(td: Path) -> Path:
    path = td / "variants.jsonl"
    row = {
        "profile_id": EU5,
        "name": "SDI9_1_981_3_4L_EU5",
        "module": "DME_BDE_Continental",
        "generation": "981",
        "membership": "confirmed",
        "on_target_menu": True,
        "observedcapture": False,
        "accepted": True,
        "pool_records": {
            "identity": {"count": 0, "records": []},
            "measurement": {"count": 1, "records": [{"at": 1, "name": "rpm", "unit": "1/min", "formula": {"text": "IDENTICAL:BitLength=8"}}]},
            "coding": {"count": 1, "records": [CRUISE]},
            "dtc": {"count": 1, "records": [{"at": 9, "code": "P0300", "text": "失火", "numeric_dtc_hex": "0300"}]},
            "routine": {"count": 0, "records": []},
        },
    }
    path.write_text(json.dumps(row, ensure_ascii=False) + "\n", encoding="utf-8")
    return path


def _with_fixture(fn):
    @functools.wraps(fn)
    def wrap(self):
        td = tempfile.TemporaryDirectory()
        try:
            p = _fixture(Path(td.name))
            with mock.patch.dict(os.environ, {"PORSCHE981_VARIANTS": str(p)}):
                fn(self, p)
        finally:
            td.cleanup()

    return wrap


def _flags(d):
    for k, v in FLAGS.items():
        unittest.TestCase().assertEqual(d[k], v)


class TestNoSerial(unittest.TestCase):
    def test_source_never_imports_serial(self):
        tree = ast.parse(WB_PY.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                self.assertFalse(any(a.name.split(".")[0] == "serial" for a in node.names))
            if isinstance(node, ast.ImportFrom) and node.module:
                self.assertNotEqual(node.module.split(".")[0], "serial")
                self.assertNotEqual(node.module, "scripts.diagnostics.live")
                self.assertFalse((node.module or "").endswith(".live"))


class TestValidation(unittest.TestCase):
    def test_invalid_action(self):
        d = handle({"action": "send"})
        self.assertFalse(d["ok"])
        self.assertEqual(d["error"], "invalid_action")
        _flags(d)

    def test_forbidden_path_send_formula(self):
        for extra in ({"path": "x"}, {"send": True}, {"formula": "IDENTICAL"}, {"variantsPath": "x"}):
            d = handle({"action": "summary", **extra})
            self.assertEqual(d["error"], "forbidden_field", extra)

    def test_wrong_generation(self):
        d = handle({"action": "plan", "generation": "991"})
        self.assertEqual(d["error"], "wrong_generation")

    def test_cap_limits(self):
        d = handle({"action": "variants", "generation": "981", "limit": 101})
        self.assertEqual(d["error"], "cap_limit")
        d = handle({"action": "records", "profileId": "x", "category": "dtc", "search": "z" * 81})
        self.assertEqual(d["error"], "cap_limit")
        d = handle({"action": "decode", "profileId": "x", "category": "coding", "recordAt": 1, "dataHex": "AA" * 3000})
        self.assertEqual(d["error"], "cap_limit")

    def test_malformed_raw_formula_rejected_as_forbidden(self):
        d = handle({"action": "decode", "formula": "LINEAR:Xc=1", "dataHex": "01"})
        self.assertEqual(d["error"], "forbidden_field")


class TestSummaryPlanMatchReplay(unittest.TestCase):
    def test_summary_does_not_pretend_optional_seeds(self):
        d = handle({"action": "summary"})
        self.assertTrue(d["ok"])
        self.assertEqual(d["menuEcuCount"], 35)
        self.assertEqual(len(d["menuEcus"]), 35)
        proto = d["protocolInventory"]
        val = d["valueSupport"]
        self.assertIn("present", proto)
        self.assertIn("present", val)
        if proto["present"]:
            self.assertTrue(proto.get("ready"))
            self.assertEqual(len(proto.get("groups") or []), 35)
            self.assertEqual((proto.get("counts") or {}).get("menuGroups"), 35)
        else:
            self.assertTrue(d["protocolNotReady"])
        if val["present"]:
            self.assertIn("coding", val.get("decodeCounts") or {})
        else:
            self.assertTrue(d["valueSupportNotReady"])
        _flags(d)

    def test_plan_981(self):
        d = handle({"action": "plan", "generation": "981"})
        self.assertTrue(d["ok"])
        self.assertEqual(d["groupCount"], 35)
        self.assertIsNone(d["writePayload"])
        self.assertIn("sourceEvidence", d)
        self.assertTrue(d["carChecklist"]["vehicleActionsDisabled"])

    def test_match_and_generation_mismatch(self):
        d = handle({"action": "match", "identity": _dme_fp()})
        self.assertTrue(d["ok"])
        self.assertEqual(d["identityQualification"]["status"], "observed_profile_match")
        bad = dict(_dme_fp())
        bad["generation"] = "982"
        d2 = handle({"action": "match", "identity": bad})
        self.assertEqual(d2["identityQualification"]["status"], "wrong_generation")

    def test_replay_offline_flags(self):
        d = handle({"action": "replay"})
        self.assertTrue(d["ok"], d)
        self.assertEqual(d["summary"]["realCount"], 17)
        self.assertEqual(d["summary"]["syntheticCount"], 17)
        self.assertTrue(d["summary"]["vinRedacted"])
        self.assertFalse(d["executionEnabled"])
        kinds = {c["evidenceKind"] for c in d["cases"]}
        self.assertEqual(kinds, {"capture_replay", "synthetic"})


class TestFixturePools(unittest.TestCase):
    @_with_fixture
    def test_unknown_profile_and_record(self, _p):
        d = handle({"action": "records", "generation": "981", "profileId": "nope", "category": "coding"})
        self.assertEqual(d["error"], "unknown_profile")
        d = handle({"action": "decode", "generation": "981", "profileId": EU5, "category": "coding", "recordAt": 1, "dataHex": "01"})
        self.assertEqual(d["error"], "unknown_record")

    @_with_fixture
    def test_variants_and_records_page(self, _p):
        d = handle({"action": "variants", "generation": "981", "ecuId": 1, "offset": 0, "limit": 10})
        self.assertTrue(d["ok"])
        self.assertEqual(d["total"], 1)
        self.assertEqual(d["items"][0]["membership"], "confirmed")
        r = handle({"action": "records", "generation": "981", "ecuId": 1, "profileId": EU5, "category": "dtc", "search": "P0300", "limit": 10})
        self.assertEqual(r["total"], 1)
        self.assertEqual(r["items"][0]["dtc"]["code"], "P0300")
        self.assertEqual(r["items"][0]["dtc"]["textStatus"], "present")

    @_with_fixture
    def test_missing_binary(self, _p):
        d = handle({"action": "decode", "generation": "981", "profileId": EU5, "category": "coding", "recordAt": 4047899})
        self.assertEqual(d["error"], "missing_binary")
        d = handle({"action": "decode", "generation": "981", "profileId": EU5, "category": "coding", "recordAt": 4047899, "dataHex": "GG"})
        self.assertEqual(d["error"], "missing_binary")

    @_with_fixture
    def test_decode_01_and_preview_a5(self, _p):
        d = handle({"action": "decode", "generation": "981", "profileId": EU5, "category": "coding", "recordAt": 4047899, "dataHex": "01"})
        self.assertTrue(d["ok"], d)
        self.assertEqual(d["text"], "是")
        self.assertIsNone(d["writePayload"])
        p = handle(
            {
                "action": "preview",
                "generation": "981",
                "profileId": EU5,
                "category": "coding",
                "recordAt": 4047899,
                "dataHex": "A5",
                "rawValue": 0,
            }
        )
        self.assertTrue(p["ok"], p)
        self.assertEqual(p["afterHex"], "A4")
        self.assertEqual(p["changedBitMaskHex"], "01")
        self.assertIsNone(p["writePayload"])
        badg = handle({"action": "decode", "generation": "982", "profileId": EU5, "category": "coding", "recordAt": 4047899, "dataHex": "01"})
        self.assertEqual(badg["error"], "generation_profile_mismatch")
        badp = handle({"action": "preview", "generation": "981", "profileId": EU5, "category": "measurement", "recordAt": 4047899, "dataHex": "A5", "rawValue": 0})
        self.assertEqual(badp["error"], "preview_coding_only")

    @_with_fixture
    def test_coding_options_are_defined_labels_with_preserved_bits(self, _p):
        doc = handle({"action": "coding-options", "generation": "981", "profileId": EU5,
            "category": "coding", "recordAt": 4047899, "dataHex": "A5"})
        self.assertTrue(doc["ok"], doc)
        self.assertEqual(doc["options"], [{"rawValue": 0, "label": "否"}, {"rawValue": 1, "label": "是"}])
        self.assertEqual(doc["decoded"]["text"], "是")
        self.assertFalse(doc["executionEnabled"])

    @_with_fixture
    def test_full_catalogue_has_no_invented_match_or_vehicle_response(self, _p):
        units = handle({"action": "catalog-units", "generation": "981"})
        self.assertTrue(units["ok"], units)
        dme = next(unit for unit in units["units"] if unit["ecuId"] == 1)
        self.assertEqual(dme["variants"][0]["status"], "catalog-unqualified")
        doc = handle({"action": "catalog-parameters", "generation": "981", "ecuId": 1, "profileId": EU5})
        self.assertTrue(doc["ok"], doc)
        self.assertEqual(doc["total"], 1)
        self.assertEqual(doc["items"][0]["decodedSampleCount"], 0)
        self.assertFalse(doc["items"][0]["decoderReady"], "unresolved wire definition remains disabled")
        other = handle({"action": "catalog-units", "generation": "982"})
        self.assertEqual(other["units"], [])

    @_with_fixture
    def test_coding_block_binding_and_filtered_fields(self, _p):
        request = {"generation": "981", "profileId": EU5, "category": "coding", "recordAt": 4047899,
                   "dataHex": "A5", "expectedReadRequestHex": "2101"}
        self.assertTrue(handle({**request, "action": "coding-options"})['ok'])
        self.assertTrue(handle({**request, "action": "preview", "rawValue": 0})['ok'])
        for wrong in ('2102', '220001'):
            for action in ('coding-options', 'preview'):
                self.assertEqual(handle({**request, "action": action, "rawValue": 0,
                    "expectedReadRequestHex": wrong})['error'], 'coding_field_block_mismatch')
            filtered = handle({**request, "action": "records", "expectedReadRequestHex": wrong})
            self.assertEqual(filtered['items'], [])
        self.assertEqual(handle({**request, "action": "records"})['total'], 1)
        for malformed in ('21', '210001', '2210', '22F00Z', 2101):
            self.assertEqual(handle({**request, "action": "preview", "rawValue": 0,
                "expectedReadRequestHex": malformed})['error'], 'coding_block_request_invalid')
        variant = json.loads(_p.read_text(encoding='utf8'))
        variant['pool_records']['coding']['records'][0]['enumText']['F000010F'] = ''
        _p.write_text(json.dumps(variant), encoding='utf8')
        self.assertEqual(handle({**request, "action": "preview", "rawValue": 0})['error'], 'coding_value_not_defined')

    def test_cli_and_no_vin_in_stdout(self):
        stdin = io.StringIO(json.dumps({"action": "summary"}))
        buf = io.StringIO()
        with mock.patch("sys.stdin", stdin), redirect_stdout(buf):
            code = main()
        self.assertEqual(code, 0)
        text = buf.getvalue()
        self.assertNotRegex(text, r"(?<![A-Z0-9])[A-HJ-NPR-Z0-9]{17}(?![A-Z0-9])")
        self.assertTrue(json.loads(text)["ok"])


@unittest.skipUnless(DEFAULT_VARIANTS.is_file(), "variants.jsonl absent")
class TestRealEu5(unittest.TestCase):
    def test_real_decode_preview(self):
        d = handle({"action": "decode", "generation": "981", "profileId": EU5, "category": "coding", "recordAt": 4047899, "dataHex": "01"})
        self.assertTrue(d["ok"], d)
        self.assertEqual(d["text"], "是")
        p = handle(
            {
                "action": "preview",
                "generation": "981",
                "profileId": EU5,
                "category": "coding",
                "recordAt": 4047899,
                "dataHex": "A5",
                "rawValue": 0,
            }
        )
        dtc = handle({"action": "records", "generation": "981", "ecuId": 1, "profileId": EU5, "category": "dtc", "search": "P000A", "limit": 5})
        self.assertTrue(dtc["ok"], dtc)
        self.assertGreaterEqual(dtc["total"], 1)
        self.assertEqual(dtc["items"][0]["dtc"]["code"], "P000A")
        man = dtc["items"][0]["manual"]
        self.assertEqual(man["exactHits"], [])
        rel = man["relatedBaseCodeHits"]
        self.assertTrue(any(h.get("rawCode") == "P000A00" and 3841 in (h.get("pages") or []) for h in rel), rel)
        self.assertTrue(any(h.get("caymanBody") and h.get("bodyApplicability") == "cayman-coupe" for h in rel), rel)
        self.assertFalse(any("991" in str(h.get("sourceId") or "") for h in rel))
        self.assertTrue(any((n.get("itemsPreview") or []) for h in rel for n in (h.get("needs") or [])), rel)
        self.assertTrue(all(h.get("relation") == "base_code_only" for h in rel))
        self.assertTrue((dtc.get("systemEvidence") or {}).get("registry"))
        coding = handle({"action": "records", "generation": "981", "ecuId": 1, "profileId": EU5, "category": "coding", "search": "4047899", "limit": 3})
        self.assertTrue(coding["ok"], coding)
        self.assertNotIn("manual", coding["items"][0])
        self.assertTrue(p["ok"], p)
        self.assertEqual(p["afterHex"], "A4")
        pdu = handle(
            {
                "action": "decode",
                "generation": "981",
                "ecuId": 1,
                "profileId": EU5,
                "category": "coding",
                "recordAt": 4047899,
                "responseMode": "pdu",
                "dataHex": "610101",
            }
        )
        self.assertTrue(pdu["ok"], pdu)
        self.assertEqual(pdu["text"], "是")
        self.assertEqual(pdu.get("error"), None)
        self.assertNotEqual(pdu.get("reason"), "unsupported_read_sid")
        still = handle(
            {
                "action": "decode",
                "generation": "981",
                "ecuId": 1,
                "profileId": EU5,
                "category": "coding",
                "recordAt": 4047899,
                "dataHex": "01",
            }
        )
        self.assertTrue(still["ok"], still)
        self.assertEqual(still["text"], "是")


if __name__ == "__main__":
    unittest.main()
