"""Application PDU normalizer. No serial."""
from __future__ import annotations

import ast
import unittest
from pathlib import Path

from scripts.diagnostics.offline import DEFAULT_VARIANTS, find_variant
from scripts.diagnostics.response_values import (
    decode_application_response,
    normalize_response,
    request_from_record,
)
from scripts.diagnostics.x431_values import classify_decode
from scripts.diagnostics.x431_formula import formula_from_record

REPO = Path(__file__).resolve().parents[3]
SRC = REPO / "scripts" / "diagnostics" / "response_values.py"
EU5 = "9x1:DME_BDE_Continental:SDI9_1_981_3_4L_EU5"


def _rec22(**over):
    rec = {
        "at": 1,
        "rawSID": 0x22,
        "wireSID": 0x22,
        "byteOffset": 0,
        "bitOffset": 0,
        "fields_status": "ok",
        "formula": {"text": "IDENTICAL:DataType=A_UINT8,BitLength=8,BitMask=0,HighLow=1"},
        "disabledreadrequestcandidate": {"payload_hex": "22F187", "status": "ok"},
    }
    rec.update(over)
    return rec


class TestNoSerial(unittest.TestCase):
    def test_source_never_imports_serial(self):
        tree = ast.parse(SRC.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                self.assertFalse(any(a.name.split(".")[0] == "serial" for a in node.names))
            if isinstance(node, ast.ImportFrom) and node.module:
                self.assertNotEqual(node.module.split(".")[0], "serial")


class TestNormalize(unittest.TestCase):
    def test_invalid_mode_not_inferred(self):
        d = normalize_response(b"\x62\xf1\x87\x00", _rec22(), "auto")
        self.assertFalse(d["ok"])
        self.assertEqual(d["reason"], "invalid_response_mode")
        self.assertFalse(d.get("inferred"))

    def test_data_mode_passthrough(self):
        d = normalize_response(b"\x01", _rec22(), "data")
        self.assertTrue(d["ok"])
        self.assertEqual(d["payload"], b"\x01")
        self.assertEqual(d["headerLen"], 0)

    def test_empty_pdu(self):
        d = normalize_response(b"", _rec22(), "pdu")
        self.assertEqual(d["reason"], "empty_pdu")

    def test_wrong_sid(self):
        d = normalize_response(b"\x50\xf1\x87\x00", _rec22(), "pdu")
        self.assertEqual(d["reason"], "wrong_sid")

    def test_wrong_did(self):
        d = normalize_response(b"\x62\xf1\x88\x00", _rec22(), "pdu")
        self.assertEqual(d["reason"], "wrong_did")

    def test_truncated(self):
        d = normalize_response(b"\x62\xf1", _rec22(), "pdu")
        self.assertEqual(d["reason"], "truncated")

    def test_positive_full_pdu(self):
        d = normalize_response(b"\x62\xf1\x87\xab", _rec22(), "pdu")
        self.assertTrue(d["ok"], d)
        self.assertEqual(d["payload"], b"\xab")
        self.assertEqual(d["headerLen"], 3)
        self.assertFalse(d["isotpStripped"])

    def test_nrc78_pending(self):
        d = normalize_response(b"\x7f\x22\x78", _rec22(), "pdu")
        self.assertFalse(d["ok"])
        self.assertEqual(d["reason"], "pending")
        self.assertEqual(d["nrc"], 0x78)
        self.assertTrue(d["pending"])
        self.assertFalse(d["decoded"])

    def test_negative_keep_nrc(self):
        d = normalize_response(b"\x7f\x22\x31", _rec22(), "pdu")
        self.assertEqual(d["reason"], "negative")
        self.assertEqual(d["nrc"], 0x31)

    def test_negative_wrong_sid(self):
        d = normalize_response(b"\x7f\x21\x31", _rec22(), "pdu")
        self.assertEqual(d["reason"], "negative_wrong_sid")

    def test_lid_21(self):
        rec = _rec22(
            rawSID=0x21,
            wireSID=0x21,
            disabledreadrequestcandidate={"payload_hex": "2110", "status": "ok"},
        )
        d = normalize_response(b"\x61\x10\x02", rec, "pdu")
        self.assertTrue(d["ok"], d)
        self.assertEqual(d["payload"], b"\x02")
        bad = normalize_response(b"\x61\x11\x02", rec, "pdu")
        self.assertEqual(bad["reason"], "wrong_lid")

    def test_request_len_rejects_multi_did(self):
        rec = _rec22(disabledreadrequestcandidate={"payload_hex": "22F187F191", "status": "ok"})
        self.assertEqual(request_from_record(rec)["reason"], "request_len_not_single_did")

    def test_sid31_alias_must_be_22(self):
        rec = _rec22(rawSID=0x31)
        self.assertTrue(request_from_record(rec)["ok"])
        bad = _rec22(rawSID=0x31, wireSID=0x21, disabledreadrequestcandidate={"payload_hex": "2110", "status": "ok"})
        self.assertEqual(request_from_record(bad)["reason"], "sid31-wire-or-payload-inconsistent")

    def test_coding_readsid_schema_rejects_companion_and_bool(self):
        rec = {
            "readSID": 0x3B,
            "pid": 1,
            "read_request_candidate_hex": "3B01",
            "companionSID": 0x3B,
        }
        self.assertEqual(request_from_record(rec)["reason"], "unsupported_read_sid")
        rec["readSID"] = True
        rec["read_request_candidate_hex"] = "2101"
        self.assertEqual(request_from_record(rec)["reason"], "malformed_read_sid")
        rec = {"readSID": 0x21, "pid": True, "read_request_candidate_hex": "2101"}
        self.assertEqual(request_from_record(rec)["reason"], "malformed_pid")
        rec = {"readSID": 0x21, "pid": 2, "read_request_candidate_hex": "2101"}
        self.assertEqual(request_from_record(rec)["reason"], "pid_mismatch")
        rec = {"readSID": 0x22, "pid": 1, "read_request_candidate_hex": "2101"}
        self.assertEqual(request_from_record(rec)["reason"], "read_sid_payload_mismatch")
        rec = {"derived_request_hex": "2101", "readSID": 0x21, "pid": 1}
        self.assertEqual(request_from_record(rec)["reason"], "no_read_candidate")


class RequestMetadataBindingTests(unittest.TestCase):
    def test_wire_schema_does_not_hide_conflicting_pid_or_read_sid(self):
        for metadata,reason in (({'pid':0xF188},'pid_mismatch'),({'pid':True},'malformed_pid'),({'readSID':0x21},'read_sid_payload_mismatch'),({'readSID':True},'malformed_read_sid'),({'rawSID':0x2E},'unsupported_raw_read_sid'),({'rawSID':0x21},'raw-wire-sid-inconsistent'),({'rawSID':'34'},'malformed_read_sid')):
            with self.subTest(metadata=metadata):self.assertEqual(request_from_record(_rec22(**metadata))['reason'],reason)

    def test_declared_candidates_must_all_agree_and_malformed_primary_not_repaired(self):
        for change,reason in (({'read_request_candidate_hex':'22F188'},'read_candidate_conflict'),({'requestHex':'22F188'},'read_candidate_conflict'),({'read_request_candidate_hex':'garbage'},'malformed_read_candidate'),({'disabledreadrequestcandidate':{'payload_hex':'garbage'},'read_request_candidate_hex':'22F187'},'malformed_read_candidate')):
            with self.subTest(change=change):self.assertEqual(request_from_record(_rec22(**change))['reason'],reason)

    def test_consistent_redundant_metadata_and_explicit_source_alias_preserved(self):
        record=_rec22(rawSID=0x31,readSID=0x22,pid=0xF187,read_request_candidate_hex='22f187',requestHex='22 F1 87')
        result=request_from_record(record);self.assertTrue(result['ok']);self.assertTrue(result['alias31to22'])
        self.assertEqual(result['request'],bytes.fromhex('22F187'))


@unittest.skipUnless(DEFAULT_VARIANTS.is_file(), "variants.jsonl absent")
class TestEu5SyntheticPdu(unittest.TestCase):
    def test_real_eu5_record_synthetic_positive_prefix(self):
        row = find_variant(DEFAULT_VARIANTS, EU5)
        self.assertIsNotNone(row)
        recs = ((row.get("pool_records") or {}).get("measurement") or {}).get("records") or []
        picked = None
        req_ok = None
        for rec in recs:
            req = request_from_record(rec)
            if not req.get("ok"):
                continue
            _, parsed = formula_from_record(rec)
            if not classify_decode(parsed, rec.get("byteOffset"), rec.get("bitOffset")).get("ok"):
                continue
            picked = rec
            req_ok = req
            break
        self.assertIsNotNone(picked)
        reqb = req_ok["request"]
        span = (picked.get("byteOffset") or 0) + 8
        pos = (req_ok["sid"] + 0x40) & 0xFF
        pdu = bytes([pos]) + reqb[1:] + bytes(span)
        pdu_out = decode_application_response(picked, pdu, "pdu", source="synthetic")
        self.assertEqual(pdu_out.get("source"), "synthetic")
        self.assertEqual(pdu_out.get("responseMode"), "pdu")
        self.assertFalse(pdu_out.get("isotpStripped"))
        self.assertNotEqual(pdu_out.get("reason"), "wrong_sid")
        data_out = decode_application_response(picked, pdu[3:], "data")
        if pdu_out.get("ok"):
            self.assertTrue(data_out.get("ok"), data_out)

    def test_real_eu5_coding_pdu_610101(self):
        row = find_variant(DEFAULT_VARIANTS, EU5)
        rec = next(
            r
            for r in ((row.get("pool_records") or {}).get("coding") or {}).get("records") or []
            if r.get("at") == 4047899
        )
        self.assertEqual(rec.get("readSID"), 33)
        self.assertIsNone(rec.get("wireSID"))
        self.assertEqual(rec.get("companionSID"), 59)
        self.assertEqual(rec.get("read_request_candidate_hex"), "2101")
        req = request_from_record(rec)
        self.assertTrue(req["ok"], req)
        self.assertEqual(req["sid"], 0x21)
        self.assertNotEqual(req["sid"], rec.get("companionSID"))
        yes = decode_application_response(rec, bytes.fromhex("610101"), "pdu")
        self.assertTrue(yes.get("ok"), yes)
        self.assertEqual(yes.get("text"), "是")
        self.assertEqual(yes.get("headerLen"), 2)
        data = decode_application_response(rec, bytes.fromhex("01"), "data")
        self.assertTrue(data.get("ok"), data)
        self.assertEqual(data.get("text"), "是")
        self.assertEqual(normalize_response(bytes.fromhex("610201"), rec, "pdu")["reason"], "wrong_lid")
        pend = normalize_response(bytes.fromhex("7F2178"), rec, "pdu")
        self.assertEqual(pend["reason"], "pending")
        self.assertEqual(pend.get("nrc"), 0x78)


if __name__ == "__main__":
    unittest.main()
