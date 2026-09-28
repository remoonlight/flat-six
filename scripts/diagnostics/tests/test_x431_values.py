from __future__ import annotations

import io
import json
import struct
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from scripts.diagnostics.x431_formula import formula_from_record, parse_formula_text
from scripts.diagnostics.x431_values import (
    FLAGS,
    classify_decode,
    classify_preview,
    decode_record,
    main,
    preview_coding,
)

EU5_CRUISE = {
    "at": 4047899,
    "byteOffset": 0,
    "bitOffset": 0,
    "formulaID": "F0000078",
    "formula": {
        "id_hex": "F0000078",
        "text": "TEXTTABLE:DataType=A_UINT32,[0x00]->0xF000010F;[0x01]->0xF0000110;LengthInfo=Standard,BitLength=1,BitMask=0,HighLow=1;",
    },
    "enumText": {"F0000110": "是", "F000010F": "否"},
    "labels": [
        {"id_hex": "F001F517", "text": "编码"},
        {"id_hex": "80000018", "text": "巡航控制"},
    ],
}

LINEAR_V = {
    "byteOffset": 0,
    "bitOffset": 0,
    "formula": (
        "LINEAR:Lower=null,Upper=255,BaseDataType=A_UINT32,Xa=-0.0,Xb=1.0,Xc=0.2,"
        "BitLength=8,BitMask=0,HighLow=1,Encoding=Undefined,Radix=10,DataType=A_FLOAT64,Precision=1;"
    ),
    "unit": "V",
    "name": "电源电压（控制单元）",
}

LINEAR_T = {
    "byteOffset": 0,
    "bitOffset": 0,
    "formula": (
        "LINEAR:Lower=null,Upper=null,BaseDataType=A_UINT32,Xa=100.0,Xb=2.0,Xc=1.0,"
        "BitLength=8,BitMask=0,HighLow=1,Encoding=Undefined,Radix=10,DataType=A_FLOAT64,Precision=1;"
    ),
    "name": "车外温度",
}

LINEAR_ANG = {
    "byteOffset": 0,
    "bitOffset": 0,
    "formula": (
        "LINEAR:Lower=null,Upper=null,BaseDataType=A_UINT32,Xa=32.768,Xb=1.0,Xc=0.001,"
        "BitLength=16,BitMask=0,HighLow=1,Encoding=Undefined,Radix=10,DataType=A_FLOAT64,Precision=2;"
    ),
    "name": "SubIndexNum=2#0xA0,0x00,0x03,0xE4#0xF0,0x01,0x9C,0xC9",
}


def _flags_ok(d: dict) -> None:
    for k, v in FLAGS.items():
        unittest.TestCase().assertEqual(d[k], v)


class TestFormulaParse(unittest.TestCase):
    def test_linear_coeffs(self):
        p = parse_formula_text(LINEAR_V["formula"])
        self.assertTrue(p["ok"])
        self.assertEqual(p["kind"], "LINEAR")
        self.assertEqual(float(p["xc"]), 0.2)
        self.assertEqual(p["bitLength"], 8)

    def test_texttable_and_ic(self):
        p = parse_formula_text(
            "TEXTTABLE:DataType=A_UINT32,[0x00]->0xF0001915;[0x01]->0x8100074A;"
            "LengthInfo=Standard,BitLength=8,BitMask=0,HighLow=1;Ic:[0x07,0xFF]->0xF0005B27"
        )
        self.assertTrue(p["ok"])
        self.assertEqual(p["maps"][0], (0, 0, "F0001915"))
        self.assertEqual(p["ic"][0], (7, 255, "F0005B27"))


class TestExtractAndLinear(unittest.TestCase):
    def test_one_byte_lsb(self):
        rec = {
            "byteOffset": 0,
            "bitOffset": 2,
            "formula": "IDENTICAL:Standar,BitMask=0,BaseDataType=A_UINT32,BitLength=1,Encoding=Undefined,HighLow=1,Radix=10,DataType=A_UINT32;",
        }
        d = decode_record(rec, b"\x04")
        self.assertTrue(d["ok"])
        self.assertEqual(d["raw"], 1)
        _flags_ok(d)

    def test_voltage_hand(self):
        # (10 * 0.2 - 0) / 1 = 2.0
        d = decode_record(LINEAR_V, b"\x0a")
        self.assertTrue(d["ok"], d)
        self.assertEqual(d["raw"], 10)
        self.assertEqual(float(d["phys"]), 2.0)
        self.assertEqual(d["display"], "2.0")

    def test_temp_hand(self):
        # (110 * 1 - 100) / 2 = 5
        d = decode_record(LINEAR_T, bytes([110]))
        self.assertTrue(d["ok"], d)
        self.assertEqual(float(d["phys"]), 5.0)

    def test_be16_highlow1(self):
        # 0x8000 * 0.001 - 32.768 = 0
        d = decode_record(LINEAR_ANG, b"\x80\x00")
        self.assertTrue(d["ok"], d)
        self.assertEqual(d["raw"], 0x8000)
        self.assertEqual(float(d["phys"]), 0.0)
        self.assertIsNone(d["displayName"])
        self.assertTrue(d["rawName"].startswith("SubIndexNum="))

    def test_le16_highlow0(self):
        rec = dict(LINEAR_ANG)
        rec["formula"] = rec["formula"].replace("HighLow=1", "HighLow=0")
        d = decode_record(rec, b"\x00\x80")
        self.assertTrue(d["ok"], d)
        self.assertEqual(d["raw"], 0x8000)

    def test_zero_den(self):
        rec = dict(LINEAR_V)
        rec["formula"] = rec["formula"].replace("Xb=1.0", "Xb=0.0")
        d = decode_record(rec, b"\x01")
        self.assertFalse(d["ok"])
        self.assertEqual(d["reason"], "zero_denominator")

    def test_truncated(self):
        d = decode_record(LINEAR_ANG, b"\x80")
        self.assertFalse(d["ok"])
        self.assertEqual(d["reason"], "truncated")

    def test_cross_byte(self):
        rec = {
            "byteOffset": 0,
            "bitOffset": 6,
            "formula": "IDENTICAL:Standar,BitMask=0,BaseDataType=A_UINT32,BitLength=4,Encoding=Undefined,HighLow=1,Radix=10,DataType=A_UINT32;",
        }
        d = decode_record(rec, b"\xff\xff")
        self.assertTrue(d["ok"], d)
        self.assertEqual(d["raw"], 0xF)

    def test_string_identical_iso8859(self):
        rec = {
            "byteOffset": 0,
            "bitOffset": 0,
            "formula": "IDENTICAL:Standar,BitMask=0,BaseDataType=A_ASCIISTRING,BitLength=24,Encoding=ISO-8859-2,HighLow=1,Radix=10,DataType=A_UNICODE2STRING;",
        }
        d = decode_record(rec, b"ABC")
        self.assertTrue(d["ok"], d)
        self.assertEqual(d["text"], "ABC")
        self.assertEqual(d["raw"], "414243")

    def test_compucode_formel54(self):
        rec = {
            "byteOffset": 0,
            "bitOffset": 0,
            "formula": "COMPUCODE:Standar,BaseDataType=A_UINT32,BitLength=24,Encoding=Undefined,HighLow=1,EntryPoint=execute,CodeFile=Formel54.class;",
        }
        d = decode_record(rec, b"\x00\x01\x02")
        self.assertTrue(d["ok"], d)
        self.assertEqual(d["value"], 0x0102)
        self.assertIsNone(d["writePayload"])

    def test_ic_rejects(self):
        rec = {
            "byteOffset": 0,
            "bitOffset": 0,
            "formula": {
                "text": "TEXTTABLE:DataType=A_UINT32,[0x00]->0xF0001915;LengthInfo=Standard,BitLength=8,BitMask=0,HighLow=1;Ic:[0x07,0xFF]->0xF0005B27"
            },
            "enumText": {"F0005B27": "保留"},
        }
        d = decode_record(rec, b"\x10")
        self.assertFalse(d["ok"])
        self.assertEqual(d["reason"], "invalid_reserved")
        self.assertEqual(d["icText"], "保留")


class TestEu5Coding(unittest.TestCase):
    def test_cruise_yes_no(self):
        no = decode_record(EU5_CRUISE, b"\x00")
        yes = decode_record(EU5_CRUISE, b"\x01")
        self.assertTrue(no["ok"], no)
        self.assertEqual(no["text"], "否")
        self.assertTrue(yes["ok"], yes)
        self.assertEqual(yes["text"], "是")
        self.assertEqual(yes["displayName"], "巡航控制")
        _flags_ok(yes)

    def test_preview_preserves_other_bits(self):
        orig = b"\xa5"
        # bit0 only; raw 1
        out = preview_coding(EU5_CRUISE, orig, 1)
        self.assertTrue(out["ok"], out)
        self.assertEqual(out["afterHex"], "A5")  # already 1
        out0 = preview_coding(EU5_CRUISE, orig, 0)
        self.assertTrue(out0["ok"], out0)
        self.assertEqual(bytes.fromhex(out0["afterHex"]), b"\xa4")
        self.assertEqual(out0["changedBitMaskHex"], "01")
        self.assertIsNone(out0["writePayload"])
        self.assertFalse(out0["executionEnabled"])

    def test_preview_rejects_range_and_bad_raw(self):
        rec = dict(EU5_CRUISE)
        rec["formula"] = dict(rec["formula"])
        rec["formula"]["text"] = (
            "TEXTTABLE:DataType=A_UINT32,[0x00,0x03]->0xF000010F;LengthInfo=Standard,BitLength=2,BitMask=0,HighLow=1;"
        )
        rec["bitOffset"] = 0
        bad = preview_coding(rec, b"\x00", 1)
        self.assertFalse(bad["ok"])
        self.assertEqual(bad["reason"], "preview_range_enum")
        no = preview_coding(EU5_CRUISE, b"\x00", 2)
        self.assertFalse(no["ok"])
        self.assertEqual(no["reason"], "preview_raw_not_allowed_singleton")

    def test_preview_acc_bit1(self):
        rec = dict(EU5_CRUISE)
        rec["at"] = 4047949
        rec["bitOffset"] = 1
        orig = b"\x00"
        out = preview_coding(rec, orig, 1)
        self.assertTrue(out["ok"], out)
        self.assertEqual(bytes.fromhex(out["afterHex"]), b"\x02")
        self.assertEqual(out["changedBitMaskHex"], "02")


class TestExplicitDataNoHeaderGuess(unittest.TestCase):
    def test_offset_is_into_given_bytes(self):
        rec = dict(LINEAR_V)
        rec["byteOffset"] = 2
        d = decode_record(rec, b"\x22\x00\x0a")
        self.assertTrue(d["ok"], d)
        self.assertEqual(d["raw"], 10)


class TestSignedDomainAndParse(unittest.TestCase):
    def test_parses_neg_hex_literal(self):
        p = parse_formula_text(
            "IDENTICAL:Standar,BitMask=0,BaseDataType=A_INT8,BitLength=8,"
            "Encoding=Undefined,HighLow=1,Radix=10,Lower=0x-80,Upper=0x7F,DataType=A_INT8;"
        )
        self.assertTrue(p["ok"], p)
        self.assertEqual(p["lower"], -128)
        self.assertEqual(p["upper"], 127)
        self.assertTrue(p["signedLiterals"])

    def test_signed_ic_uses_sign_extended_compare(self):
        rec = {
            "byteOffset": 0,
            "bitOffset": 0,
            "formula": (
                "TEXTTABLE:DataType=A_INT8,[0x00]->0xF0000001;"
                "LengthInfo=Standard,BitLength=8,BitMask=0,HighLow=1,Encoding=Undefined;"
                "Ic:[0x-80]->0xF0005B27"
            ),
            "enumText": {"F0005B27": "保留"},
        }
        d = decode_record(rec, b"\x80")
        self.assertFalse(d["ok"])
        self.assertEqual(d["reason"], "invalid_reserved")
        self.assertEqual(d["raw"], 0x80)
        self.assertEqual(d["value"], -128)
        self.assertEqual(d["icText"], "保留")

    def test_signed_bounds_not_unsigned_raw(self):
        rec = {
            "byteOffset": 0,
            "bitOffset": 0,
            "formula": (
                "IDENTICAL:Standar,BitMask=0,BaseDataType=A_INT8,BitLength=8,"
                "Encoding=Undefined,HighLow=1,Radix=10,Lower=0x-80,Upper=0x7F,DataType=A_INT8;"
            ),
        }
        d = decode_record(rec, b"\x80")
        self.assertTrue(d["ok"], d)
        self.assertEqual(d["raw"], 0x80)
        self.assertEqual(d["value"], -128)
        high = decode_record(rec, b"\x7f")
        self.assertTrue(high["ok"], high)
        self.assertEqual(high["value"], 127)

    def test_rejects_reversed_range_and_overlap(self):
        rev = parse_formula_text(
            "TEXTTABLE:DataType=A_UINT32,[0x10,0x01]->0xF0000001;BitLength=8,BitMask=0,HighLow=1;"
        )
        self.assertFalse(rev["ok"])
        self.assertEqual(rev["reason"], "malformed_range_reversed")
        ov = parse_formula_text(
            "TEXTTABLE:DataType=A_UINT32,[0x00,0x05]->0xF0000001,[0x03,0x08]->0xF0000002;"
            "BitLength=8,BitMask=0,HighLow=1;"
        )
        self.assertFalse(ov["ok"])
        self.assertEqual(ov["reason"], "malformed_map_overlap")

    def test_rejects_garbage_fields_and_dup_and_leftover(self):
        self.assertEqual(parse_formula_text("LINEAR:Lower=garbage,Xa=1,Xb=1,Xc=1,BitLength=8;")["reason"], "malformed_bound")
        self.assertEqual(parse_formula_text("IDENTICAL:BitMask=garbage,BitLength=8,BaseDataType=A_UINT32;")["reason"], "malformed_bitmask")
        self.assertEqual(
            parse_formula_text("IDENTICAL:BitLength=8,BitLength=16,BaseDataType=A_UINT32;")["reason"],
            "duplicate_key",
        )
        self.assertEqual(
            parse_formula_text("TEXTTABLE:[nope]->0xF0000001;BitLength=8;")["reason"],
            "malformed_mapping",
        )
        self.assertEqual(parse_formula_text("LINEAR:Xa=NaN,Xb=1,Xc=1,BitLength=8;")["reason"], "malformed_coeff")
        self.assertEqual(parse_formula_text("LINEAR:Xa=1e99,Xb=1,Xc=1,BitLength=8;")["reason"], "malformed_coeff")

    def test_encoding_whitelist_and_width32(self):
        iso = {
            "byteOffset": 0,
            "bitOffset": 0,
            "formula": (
                "LINEAR:Lower=null,Upper=255,BaseDataType=A_UINT32,Xa=0,Xb=1,Xc=1,"
                "BitLength=8,BitMask=0,HighLow=1,Encoding=ISO-8859-2,Radix=10;"
            ),
        }
        d = decode_record(iso, b"\x01")
        self.assertFalse(d["ok"])
        self.assertEqual(d["reason"], "encoding_string_unproven")
        wide = {
            "byteOffset": 0,
            "bitOffset": 0,
            "formula": "IDENTICAL:BitMask=0,BaseDataType=A_UINT64,BitLength=64,Encoding=Undefined,HighLow=1;",
        }
        w = decode_record(wide, bytes(8))
        self.assertFalse(w["ok"])
        self.assertEqual(w["reason"], "width_gt_32_unproven")
        i64 = {
            "byteOffset": 0,
            "bitOffset": 0,
            "formula": "IDENTICAL:BitMask=0,BaseDataType=A_INT64,BitLength=32,Encoding=Undefined,HighLow=1;",
        }
        self.assertEqual(decode_record(i64, bytes(4))["reason"], "width_gt_32_unproven")


class TestStrictApiAndLabels(unittest.TestCase):
    def test_rejects_coerced_offsets(self):
        rec = dict(LINEAR_V)
        rec["byteOffset"] = 0.0
        rec["bitOffset"] = 0
        self.assertEqual(decode_record(rec, b"\x0a")["reason"], "malformed_offset")
        rec = dict(LINEAR_V)
        rec["byteOffset"] = True
        rec["bitOffset"] = 0
        self.assertEqual(decode_record(rec, b"\x0a")["reason"], "malformed_offset")
        rec = dict(LINEAR_V)
        rec["byteOffset"] = "0"
        rec["bitOffset"] = 0
        self.assertEqual(decode_record(rec, b"\x0a")["reason"], "malformed_offset")
        rec = dict(LINEAR_V)
        rec["byteOffset"] = 0
        rec["bitOffset"] = -1
        self.assertEqual(decode_record(rec, b"\x0a")["reason"], "malformed_offset")

    def test_preview_rejects_bad_raw_and_ic(self):
        self.assertEqual(preview_coding(EU5_CRUISE, b"\x00", True)["reason"], "malformed_raw_value")
        self.assertEqual(preview_coding(EU5_CRUISE, b"\x00", 1.0)["reason"], "malformed_raw_value")
        self.assertEqual(preview_coding(EU5_CRUISE, b"\x00", "1")["reason"], "malformed_raw_value")
        rec = dict(EU5_CRUISE)
        rec["formula"] = dict(rec["formula"])
        rec["formula"]["text"] = (
            "TEXTTABLE:DataType=A_UINT32,[0x00]->0xF000010F;[0x01]->0xF0000110;"
            "LengthInfo=Standard,BitLength=1,BitMask=0,HighLow=1;Ic:[0x01]->0xF0005B27"
        )
        bad = preview_coding(rec, b"\x00", 1)
        self.assertFalse(bad["ok"])
        self.assertEqual(bad["reason"], "invalid_reserved")

    def test_empty_vs_absent_enum(self):
        rec = dict(EU5_CRUISE)
        rec["enumText"] = {"F000010F": ""}
        empty = decode_record(rec, b"\x00")
        self.assertTrue(empty["ok"], empty)
        self.assertEqual(empty["text"], "")
        self.assertEqual(empty["textStatus"], "empty")
        rec2 = dict(EU5_CRUISE)
        rec2["enumText"] = {}
        miss = decode_record(rec2, b"\x00")
        self.assertTrue(miss["ok"], miss)
        self.assertIsNone(miss["text"])
        self.assertEqual(miss["textStatus"], "absent")
        self.assertNotEqual(empty["textStatus"], miss["textStatus"])

    def test_audit_separates_decode_and_preview(self):
        rec = dict(LINEAR_V)
        _t, parsed = formula_from_record(rec)
        d = classify_decode(parsed, 0, 0)
        p = classify_preview(parsed, 0, 0)
        self.assertTrue(d["ok"])
        self.assertFalse(p["ok"])
        self.assertEqual(p["reason"], "preview_inverse_ambiguous")
        _t2, parsed2 = formula_from_record(EU5_CRUISE)
        self.assertTrue(classify_decode(parsed2, 0, 0)["ok"])
        self.assertTrue(classify_preview(parsed2, 0, 0)["ok"])

    def test_cli_json_nonzero(self):
        buf = io.StringIO()
        with self.assertRaises(SystemExit) as cm:
            with redirect_stdout(buf):
                main([])
        self.assertEqual(cm.exception.code, 2)
        payload = json.loads(buf.getvalue())
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["reason"], "cli_error")
        self.assertFalse(payload["executionEnabled"])
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "v.jsonl"
            p.write_text("{}\n", encoding="utf-8")
            buf2 = io.StringIO()
            with redirect_stdout(buf2):
                rc = main(
                    [
                        "decode",
                        "--variants",
                        str(p),
                        "--profile-id",
                        "missing",
                        "--at",
                        "1",
                        "--data-hex",
                        "ZZ",
                    ]
                )
            self.assertEqual(rc, 2)
            bad = json.loads(buf2.getvalue())
            self.assertFalse(bad["ok"])
            self.assertIn(bad["reason"], {"cli_bad_hex", "cli_not_found"})


class AcceptanceBoundaryTests(unittest.TestCase):
    def test_unconsumed_formula_text_is_rejected(self):
        formula = "TEXTTABLE:BitLength=1,Encoding=Undefined;[0]->0xF1;[1]->0xF2"
        for bad in (formula + ";garbage", formula + "ZZ"):
            record = {"formula": bad, "byteOffset": 0, "bitOffset": 0}
            self.assertFalse(decode_record(record, b"\x01")["ok"])
            self.assertFalse(preview_coding(record, b"\xA5", 0)["ok"])

    def test_text_type_cannot_be_previewed_as_numeric_enum(self):
        record = {
            "formula": "TEXTTABLE:BitLength=1,Encoding=Undefined,BaseDataType=A_ASCIISTRING;[0]->0xF1;[1]->0xF2",
            "byteOffset": 0, "bitOffset": 0,
        }
        self.assertEqual(decode_record(record, b"\x01")["reason"], "type_non_numeric")
        self.assertFalse(preview_coding(record, b"\xA5", 0)["ok"])

    def test_signed_preview_uses_same_enum_domain_as_decode(self):
        record = {
            "formula": "TEXTTABLE:BitLength=8,Encoding=2C;[255]->0xF1",
            "byteOffset": 0, "bitOffset": 0,
        }
        self.assertFalse(decode_record(record, b"\xff")["ok"])
        self.assertFalse(preview_coding(record, b"\x00", 255)["ok"])


class CompletionSourceTests(unittest.TestCase):
    def test_string_truncation_and_invalid(self):
        rec = {
            "byteOffset": 0,
            "bitOffset": 0,
            "formula": "IDENTICAL:Standar,BitMask=0,BaseDataType=A_ASCIISTRING,BitLength=32,Encoding=ISO-8859-2,HighLow=1,Radix=10,DataType=A_UNICODE2STRING;",
        }
        d = decode_record(rec, b"AB\x00Z")
        self.assertTrue(d["ok"], d)
        self.assertEqual(d["text"], "AB")
        ascii_rec = dict(rec)
        ascii_rec["formula"] = rec["formula"].replace("ISO-8859-2", "ASCII")
        bad = decode_record(ascii_rec, b"A\xff\x00B")
        self.assertFalse(bad["ok"])
        self.assertEqual(bad["reason"], "encoding_invalid")

    def test_bcd_invalid_nibble(self):
        rec = {
            "byteOffset": 0,
            "bitOffset": 0,
            "formula": "IDENTICAL:Standar,BitMask=0,BaseDataType=A_UINT32,BitLength=16,Encoding=BCD-P,HighLow=1,Radix=10,DataType=A_UINT32;",
        }
        ok = decode_record(rec, b"\x12\x34")
        self.assertTrue(ok["ok"], ok)
        self.assertEqual(ok["value"], 1234)
        bad = decode_record(rec, b"\x1A\x00")
        self.assertFalse(bad["ok"])
        self.assertEqual(bad["reason"], "encoding_bcd_invalid")

    def test_mask_holes_and_preview_other_bits(self):
        rec = {
            "byteOffset": 0,
            "bitOffset": 0,
            "formula": "IDENTICAL:Standar,BitMask=05,BaseDataType=A_UINT32,BitLength=8,Encoding=Undefined,HighLow=1,Radix=10,DataType=A_UINT32;",
        }
        d = decode_record(rec, b"\xff")
        self.assertTrue(d["ok"], d)
        self.assertEqual(d["raw"], 5)
        out = preview_coding(rec, b"\xf0", 5)
        self.assertTrue(out["ok"], out)
        self.assertEqual(bytes.fromhex(out["afterHex"]), b"\xf5")
        self.assertEqual(out["changedBitMaskHex"], "05")
        self.assertIsNone(out["writePayload"])

    def test_piecewise_boundary_divzero_interp(self):
        rec = {
            "byteOffset": 0,
            "bitOffset": 0,
            "formula": (
                "SCALE-LINEAR:Lower=0x00,Upper=0x9F,Xa=0.0,Xb=1.0,Xc=1.0;"
                "Lower=0xA0,Upper=0x320,Xa=-0.0,Xb=1.0,Xc=0.0175;"
                "BaseDataType=A_UINT32,BitLength=16,BitMask=0,HighLow=1,Encoding=Undefined,Radix=10,DataType=A_FLOAT64,Precision=3;"
            ),
        }
        a = decode_record(rec, b"\x00\x9f")
        self.assertTrue(a["ok"], a)
        self.assertEqual(float(a["phys"]), 0x9F)
        b = decode_record(rec, b"\x00\xa0")
        self.assertTrue(b["ok"], b)
        self.assertAlmostEqual(float(b["phys"]), 0xA0 * 0.0175)
        z = dict(rec)
        z["formula"] = rec["formula"].replace("Xb=1.0,Xc=0.0175", "Xb=0.0,Xc=0.0175")
        dz = decode_record(z, b"\x00\xa0")
        self.assertEqual(dz["reason"], "zero_denominator")
        tab = {
            "byteOffset": 0,
            "bitOffset": 0,
            "formula": (
                "TAB-INTP:Lower=0x00,Upper=0x2D,Xa=-0.0,Xb=45.0,Xc=100.0;"
                "Lower=0x2D,Upper=0x56,Xa=445.0,Xb=41.0,Xc=101.0;"
                "BaseDataType=A_UINT32,BitLength=8,BitMask=0,HighLow=1,Radix=10,DataType=A_FLOAT64,Precision=0;"
            ),
        }
        t0 = decode_record(tab, b"\x00")
        self.assertTrue(t0["ok"], t0)
        self.assertEqual(float(t0["phys"]), 0.0)
        t45 = decode_record(tab, b"\x2d")
        self.assertTrue(t45["ok"], t45)
        self.assertEqual(float(t45["phys"]), 100.0)

    def test_preview_roundtrip_cross_byte(self):
        rec = {
            "byteOffset": 0,
            "bitOffset": 6,
            "formula": "TEXTTABLE:DataType=A_UINT32,[0x00]->0xF1;[0x0F]->0xF2;LengthInfo=Standard,BitLength=4,BitMask=0,HighLow=1;",
        }
        orig = b"\x03\xfc"
        out = preview_coding(rec, orig, 0xF)
        self.assertTrue(out["ok"], out)
        after = bytes.fromhex(out["afterHex"])
        back = decode_record(rec, after)
        self.assertTrue(back["ok"], back)
        self.assertEqual(back["raw"], 0xF)
        self.assertEqual(after[0] & 0x3F, orig[0] & 0x3F)

    def test_hostile_malformed_no_exception(self):
        cases = [
            ({}, b""),
            ({"formula": "LINEAR:Xa=1e99,Xb=1,Xc=1,BitLength=8;", "byteOffset": 0, "bitOffset": 0}, b"\x00"),
            ({"formula": "NOPE:BitLength=8;", "byteOffset": 0, "bitOffset": 0}, b"\x00"),
            ({"formula": "TEXTTABLE:[nope]->0x1;BitLength=8;", "byteOffset": 0, "bitOffset": 0}, b"\x00"),
        ]
        for rec, data in cases:
            d = decode_record(rec, data)
            self.assertFalse(d["ok"], d)
            self.assertIn("reason", d)
            self.assertIsNone(d["writePayload"])
            self.assertFalse(d["executionEnabled"])

    def test_bitmask_hex_without_prefix(self):
        rec = {
            "byteOffset": 0,
            "bitOffset": 0,
            "formula": (
                "LINEAR:Lower=0,Upper=null,BaseDataType=A_UINT32,Xa=-0.0,Xb=1.0,Xc=1.0,"
                "BitLength=8,BitMask=7F,HighLow=1,Encoding=Undefined,Radix=10,DataType=A_FLOAT64,Precision=0;"
            ),
        }
        d = decode_record(rec, b"\xff")
        self.assertTrue(d["ok"], d)
        self.assertEqual(d["raw"], 0x7F)

    def test_float32_linear_iso_ignored(self):
        rec = {
            "byteOffset": 0,
            "bitOffset": 0,
            "formula": (
                "LINEAR:Lower=null,Upper=null,BaseDataType=A_FLOAT32,Xa=-0.0,Xb=1.0,Xc=0.01,"
                "BitLength=32,BitMask=0,HighLow=1,Encoding=ISO-8859-1,Radix=10,DataType=A_FLOAT64,Precision=2;"
            ),
        }
        d = decode_record(rec, struct.pack(">f", 100.0))
        self.assertTrue(d["ok"], d)
        self.assertAlmostEqual(float(d["phys"]), 1.0)
        rec = {
            "byteOffset": 0,
            "bitOffset": 0,
            "formula": (
                "LINEAR:Lower=null,Upper=null,BaseDataType=A_INT32,Xa=-2.22045e-14,Xb=1.0,Xc=0.00152588,"
                "BitLength=16,BitMask=0,HighLow=1,Encoding=2C,Radix=10,DataType=A_FLOAT64,Precision=3;"
            ),
        }
        d = decode_record(rec, b"\x00\x01")
        self.assertTrue(d["ok"], d)

    def test_getx_modify_source_oracles(self):
        def ident(bl, off, hl):
            return {
                "byteOffset": 0,
                "bitOffset": off,
                "formula": (
                    f"IDENTICAL:Standar,BitMask=0,BaseDataType=A_UINT32,BitLength={bl},"
                    f"Encoding=Undefined,HighLow={hl},Radix=10,DataType=A_UINT32;"
                ),
            }

        d = decode_record(ident(12, 4, 1), bytes.fromhex("ABCD"))
        self.assertTrue(d["ok"], d)
        self.assertEqual(d["raw"], 0xABC)
        d = decode_record(ident(10, 2, 1), bytes.fromhex("ABCD"))
        self.assertTrue(d["ok"], d)
        self.assertEqual(d["raw"], 0x2F3)
        d = decode_record(ident(16, 4, 1), bytes.fromhex("ABCDEF"))
        self.assertTrue(d["ok"], d)
        self.assertEqual(d["raw"], 0xBCDE)
        d = decode_record(ident(9, 0, 1), bytes.fromhex("ABCD"))
        self.assertTrue(d["ok"], d)
        self.assertEqual(d["raw"], 0xABCD & 0x1FF)
        d = decode_record(ident(31, 1, 1), bytes.fromhex("ABCDEF01"))
        self.assertTrue(d["ok"], d)
        self.assertEqual(d["raw"], (0xABCDEF01 >> 1) & ((1 << 31) - 1))
        d = decode_record(ident(12, 4, 0), bytes.fromhex("ABCD"))
        self.assertTrue(d["ok"], d)
        self.assertEqual(d["raw"], (0xCDAB >> 4) & 0xFFF)

    def test_compucode_named_formels(self):
        def rec(name, dt="A_FLOAT32"):
            return {
                "byteOffset": 0,
                "bitOffset": 0,
                "formula": (
                    f"COMPUCODE:Standar,BaseDataType=A_UINT32,BitLength=16,Encoding=Undefined,"
                    f"HighLow=1,EntryPoint=execute,CodeFile={name}.class,Radix=10,DataType={dt},Precision=3;"
                ),
            }

        f21 = decode_record(rec("Formel21"), b"\x02\x0a")
        self.assertTrue(f21["ok"], f21)
        self.assertAlmostEqual(float(f21["phys"]), 0.02)
        f119 = decode_record(rec("Formel119"), b"\x02\x0a")
        self.assertTrue(f119["ok"], f119)
        self.assertAlmostEqual(float(f119["phys"]), 0.2)
        f33 = decode_record(rec("Formel33"), b"\x04\x01")
        self.assertTrue(f33["ok"], f33)
        self.assertAlmostEqual(float(f33["phys"]), 25.0)
        z = decode_record(rec("Formel33"), b"\x00\x32")
        self.assertEqual(z["reason"], "zero_denominator")
        f5 = decode_record(rec("Formel5"), b"\x02\x64")
        self.assertTrue(f5["ok"], f5)
        self.assertAlmostEqual(float(f5["phys"]), 0.0)
        unknown = decode_record(rec("Formel99"), b"\x00\x00")
        self.assertEqual(unknown["reason"], "kind_COMPUCODE")

    def test_minmax_zero_requires_terminator(self):
        rec = {
            "byteOffset": 0,
            "bitOffset": 0,
            "formula": (
                "IDENTICAL:MinMax,BitMask=0,BaseDataType=A_ASCIISTRING,Encoding=ISO-8859-1,"
                "HighLow=1,Radix=10,DataType=A_UNICODE2STRING,Termination=ZERO,MaxLength=8;"
            ),
        }
        miss = decode_record(rec, b"ABC")
        self.assertFalse(miss["ok"])
        self.assertEqual(miss["reason"], "missing_terminator")
        ok = decode_record(rec, b"ABC\x00")
        self.assertTrue(ok["ok"], ok)
        self.assertEqual(ok["text"], "ABC")

    def test_bcd_up_unproven_and_float32_overflow(self):
        up = {
            "byteOffset": 0,
            "bitOffset": 0,
            "formula": "IDENTICAL:Standar,BitMask=0,BaseDataType=A_UINT32,BitLength=8,Encoding=BCD-UP,HighLow=1;",
        }
        self.assertEqual(decode_record(up, b"\x12")["reason"], "encoding_bcd_unproven")
        huge = {
            "byteOffset": 0,
            "bitOffset": 0,
            "formula": (
                "LINEAR:Lower=null,Upper=null,BaseDataType=A_UINT32,Xa=0,Xb=1,Xc=1e40,"
                "BitLength=8,BitMask=0,HighLow=1,Encoding=Undefined,Radix=10,DataType=A_FLOAT32,Precision=2;"
            ),
        }
        d = decode_record(huge, b"\xff")
        self.assertFalse(d["ok"])
        self.assertEqual(d["reason"], "out_of_range")
        self.assertIsNone(d["writePayload"])

    def test_preview_mask_unrepresentable_and_texttable(self):
        rec = {
            "byteOffset": 0,
            "bitOffset": 0,
            "formula": "IDENTICAL:Standar,BitMask=05,BaseDataType=A_UINT32,BitLength=8,Encoding=Undefined,HighLow=1,Radix=10,DataType=A_UINT32;",
        }
        bad = preview_coding(rec, b"\xf0", 2)
        self.assertFalse(bad["ok"])
        self.assertEqual(bad["reason"], "preview_raw_unrepresentable")
        ok = preview_coding(rec, b"\xf0", 5)
        self.assertTrue(ok["ok"], ok)
        self.assertEqual(bytes.fromhex(ok["afterHex"]), b"\xf5")
        back = decode_record(rec, bytes.fromhex(ok["afterHex"]))
        self.assertEqual(back["raw"], 5)
        tt = {
            "byteOffset": 0,
            "bitOffset": 0,
            "formula": "TEXTTABLE:DataType=A_UINT32,[0x00]->0xF1;[0x01]->0xF2;LengthInfo=Standard,BitLength=8,BitMask=05,HighLow=1;",
        }
        silent = preview_coding(tt, b"\xf0", 3)
        self.assertEqual(silent["reason"], "preview_raw_unrepresentable")
        mapped = preview_coding(tt, b"\xf0", 1)
        self.assertTrue(mapped["ok"], mapped)
        self.assertEqual(decode_record(tt, bytes.fromhex(mapped["afterHex"]))["raw"], 1)
        self.assertEqual(bytes.fromhex(mapped["afterHex"])[0] & 0xFA, 0xF0 & 0xFA)

    def test_preview_bcd_and_float_nan_domain(self):
        rec = {
            "byteOffset": 0,
            "bitOffset": 0,
            "formula": "IDENTICAL:Standar,BitMask=0,BaseDataType=A_UINT32,BitLength=8,Encoding=BCD-P,HighLow=1,Radix=10,DataType=A_UINT32;",
        }
        self.assertEqual(preview_coding(rec, b"\x12", 0x1A)["reason"], "encoding_bcd_invalid")
        ok = preview_coding(rec, b"\x12", 0x34)
        self.assertTrue(ok["ok"], ok)
        self.assertEqual(ok["afterHex"], "34")
        d = decode_record(rec, b"\x34")
        self.assertEqual(d["raw"], 0x34)
        self.assertEqual(d["value"], 34)
        fl = {
            "byteOffset": 0,
            "bitOffset": 0,
            "formula": "IDENTICAL:Standar,BitMask=0,BaseDataType=A_FLOAT32,BitLength=32,Encoding=Undefined,HighLow=1,DataType=A_FLOAT32;",
        }
        nan_bits = int.from_bytes(bytes.fromhex("7FC00000"), "big")
        self.assertEqual(preview_coding(fl, bytes(4), nan_bits)["reason"], "nan")
        inf_bits = int.from_bytes(bytes.fromhex("7F800000"), "big")
        self.assertEqual(decode_record(fl, bytes.fromhex("7F800000"))["reason"], "nan")

    def test_texttable_unknown_type_and_compucode_entrypoint(self):
        rec = {
            "byteOffset": 0,
            "bitOffset": 0,
            "formula": "TEXTTABLE:DataType=A_MYSTERY,[0x01]->0xF1;BitLength=8,HighLow=1;",
        }
        d = decode_record(rec, b"\x01")
        self.assertFalse(d["ok"])
        self.assertEqual(d["reason"], "type_unproven")
        _t, parsed = formula_from_record(rec)
        self.assertEqual(classify_decode(parsed, 0, 0)["reason"], "type_unproven")
        undef = {
            "byteOffset": 0,
            "bitOffset": 0,
            "formula": "IDENTICAL:Standar,BitMask=0,BaseDataType=A_ASCIISTRING,BitLength=8,Encoding=Undefined,HighLow=1;",
        }
        self.assertEqual(decode_record(undef, b"A")["reason"], "encoding_string_unproven")
        _t2, p2 = formula_from_record(undef)
        self.assertEqual(classify_decode(p2, 0, 0)["reason"], "encoding_string_unproven")
        cc = {
            "byteOffset": 0,
            "bitOffset": 0,
            "formula": "COMPUCODE:Standar,BaseDataType=A_UINT32,BitLength=24,Encoding=Undefined,HighLow=1,EntryPoint=run,CodeFile=Formel54.class;",
        }
        self.assertEqual(decode_record(cc, b"\x00\x00\x00")["reason"], "kind_COMPUCODE")

    def test_minmax_length_bounds(self):
        rec = {
            "byteOffset": 0,
            "bitOffset": 0,
            "formula": (
                "IDENTICAL:MinMax,BitMask=0,BaseDataType=A_ASCIISTRING,Encoding=ISO-8859-1,"
                "HighLow=1,Termination=ZERO,MinLength=2,MaxLength=4;"
            ),
        }
        self.assertEqual(decode_record(rec, b"ABCDE\x00")["reason"], "overlong")
        self.assertEqual(decode_record(rec, b"A\x00")["reason"], "truncated")
        ok = decode_record(rec, b"AB\x00")
        self.assertTrue(ok["ok"], ok)
        self.assertEqual(ok["text"], "AB")
        bad = parse_formula_text(
            "IDENTICAL:MinMax,MinLength=-1,MaxLength=4,BaseDataType=A_ASCIISTRING,Termination=ZERO,Encoding=ISO-8859-1;"
        )
        self.assertFalse(bad["ok"])
        contra = parse_formula_text(
            "IDENTICAL:MinMax,MinLength=5,MaxLength=2,BaseDataType=A_ASCIISTRING,Termination=ZERO,Encoding=ISO-8859-1;"
        )
        self.assertFalse(contra["ok"])


if __name__ == "__main__":
    unittest.main()
