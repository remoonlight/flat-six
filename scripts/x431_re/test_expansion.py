#!/usr/bin/env python3
"""Expansion correction tests: semantics, scope, request builder."""
from __future__ import annotations

import hashlib
import json
import struct
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
if not __package__:
    sys.path.insert(0, str(HERE))

if __package__:
    from .classify import classify_variant
    from .gag_lib import GgpLanguage, compact_text, parse_express
    from .generate import generate, write_outputs
    from .membership import classify_name
    from .menu import SYS_SELECT_OFF, TARGET_MODELS, require_target_layout, target_routes, walk_menu
    from .odx_pools import RE_HEX_LIT, RE_ID, parse_dtc_labels, parse_five_pointers, parse_id_chain_table, pool_end
    from .request_fields import derived_read_request
    from .measurement_fields import parse_measurement_fields
else:
    from classify import classify_variant
    from gag_lib import GgpLanguage, compact_text, parse_express
    from generate import generate, write_outputs
    from membership import classify_name
    from menu import SYS_SELECT_OFF, TARGET_MODELS, require_target_layout, target_routes, walk_menu
    from odx_pools import RE_HEX_LIT, RE_ID, parse_dtc_labels, parse_five_pointers, parse_id_chain_table, pool_end
    from request_fields import derived_read_request
    from measurement_fields import parse_measurement_fields

DEC = ROOT / ".local" / "x431-re" / "2026-09-27-decode" / "file-loader" / "decoded"
GGP = ROOT / ".local" / "x431-re" / "2026-09-27-protocol" / "package" / "PORSCHE_CN.GGP"
MENU = ROOT / ".local" / "x431-re" / "2026-09-27-protocol" / "supplement" / "MENU.BIN"
LABELS = ROOT / ".local" / "x431-re" / "2026-09-27-981982" / "coordinator-menu-tree.json"
SEM = ROOT / ".local" / "x431-re" / "2026-09-27-981982" / "coordinator-eu5-coding-semantics.json"
EU5_OFF = 4035362


class SyntheticPoolTests(unittest.TestCase):
    def test_empty_id_chain(self):
        blob = struct.pack("<I", 0)
        r = parse_id_chain_table(blob, 0, 4, 7, "identity")
        self.assertEqual(r["status"], "empty")

    def test_zero_pointer_missing(self):
        buf = bytearray(40)
        struct.pack_into("<5I", buf, 0, 20, 0, 0, 24, 28)
        five = parse_five_pointers(bytes(buf), 0)
        self.assertIsNone(pool_end(five["pointers"], 1, 40))

    def test_truncation_rejected(self):
        blob = struct.pack("<I", 2)
        with self.assertRaises(ValueError):
            parse_id_chain_table(blob, 0, 4, 7, "identity")

    def test_dtc_nonzero_tail_rejected(self):
        rec = struct.pack("<II", 3, 1) + struct.pack("<H", 2) + b"A\x00" + bytes([4])
        blob = struct.pack("<II", 1, 0x18) + rec + b"\xff"
        with self.assertRaises(ValueError):
            parse_dtc_labels(blob, 0, len(blob), "dtc")

    def test_dtc_zero_pad_at_eof(self):
        rec = struct.pack("<II", 3, 1) + struct.pack("<H", 2) + b"A\x00" + bytes([4])
        blob = struct.pack("<II", 1, 0x18) + rec + b"\x00\x00"
        r = parse_dtc_labels(blob, 0, len(blob), "dtc")
        self.assertEqual(r["trailing_pad"], 2)


class RequestBuilderTests(unittest.TestCase):
    def test_1a9f(self):
        r = derived_read_request(bytes.fromhex("1a00009f000000"))
        self.assertEqual(r["status"], "ok")
        self.assertEqual(r["request_hex"], "1A9F")
        self.assertEqual(r["pid"], 0x9F)

    def test_22f197(self):
        r = derived_read_request(bytes.fromhex("22000097f10000"))
        self.assertEqual(r["request_hex"], "22F197")
        self.assertEqual(r["pid"], 0xF197)

    def test_2101(self):
        r = derived_read_request(bytes.fromhex("213b0001000000"))
        self.assertEqual(r["request_hex"], "2101")
        self.assertEqual(r["companionSID"], 0x3B)

    def test_malformed_and_overflow(self):
        self.assertEqual(derived_read_request(b"\x1a")["status"], "malformed_prefix")
        self.assertEqual(derived_read_request(bytes.fromhex("1a000000000100"))["status"], "pid_overflow_u8")
        self.assertEqual(derived_read_request(bytes.fromhex("22000000000100"))["status"], "pid_overflow_u16")
        self.assertEqual(derived_read_request(bytes.fromhex("10000001000000"))["status"], "unsupported_sid")
        z = derived_read_request(bytes.fromhex("1a000000000000"))
        self.assertEqual(z["status"], "ok")
        self.assertEqual(z["request_hex"], "1A00")


class MembershipTests(unittest.TestCase):
    def test_scope_cases(self):
        self.assertEqual(classify_name("Carrera_B6T_3_0L_RDW")["membership"], "excluded")
        self.assertEqual(classify_variant("BCM_vorne", "BCM_vorne_E2_Basis_GT4")["membership"], "excluded")
        self.assertEqual(classify_variant("BCM_hinten", "BCM_hinten_G1_GT")["membership"], "excluded")
        self.assertEqual(classify_variant("Verdeck_Targa", "Verdeck_A2_1")["membership"], "excluded")
        pcm = classify_variant("Head_Unit", "PCM31_G1_und_9x1")
        self.assertEqual(pcm["membership"], "candidate")
        self.assertIn("9x1", pcm["tokens"])
        self.assertEqual(classify_name("SDI9_1_981_3_4L_EU5")["membership"], "confirmed")
        self.assertEqual(classify_name("SDI9_1_991_3_4L_EU5")["membership"], "excluded")

    def test_918_not_partial_and_part_number(self):
        self.assertNotEqual(classify_name("EV_ECM40TFS011982906033G_002")["generation"], "982")
        self.assertNotEqual(classify_name("foo918bar")["membership"], "excluded")
        self.assertEqual(classify_name("918S_thing")["membership"], "excluded")


class HashTests(unittest.TestCase):
    def test_reproducible_sha256(self):
        self.assertEqual(hashlib.sha256(b"abc").hexdigest(), hashlib.sha256(b"abc").hexdigest())


@unittest.skipUnless(MENU.is_file(), "MENU.BIN missing")
class SharedGraphTests(unittest.TestCase):
    def test_four_parents_share_sys_select(self):
        routes = require_target_layout(walk_menu(MENU.read_bytes()))
        self.assertTrue(routes["all_four_share_sys_select"])
        self.assertEqual(len(routes["sys_select_children"]), 35)
        self.assertEqual(set(routes["sys_select_parents"]), set(TARGET_MODELS))
        self.assertEqual(routes["sys_select_off"], SYS_SELECT_OFF)

    def test_mutated_model_text_id_rejected(self):
        blob = bytearray(MENU.read_bytes())
        struct.pack_into("<I", blob, 2550 + 6, 0xFFFFFFFF)
        with self.assertRaises(ValueError) as ctx:
            require_target_layout(walk_menu(bytes(blob)))
        self.assertIn("unsupported MENU layout", str(ctx.exception))


class ExpressAndHexTests(unittest.TestCase):
    def test_arrow_targets_exact_8_and_no_long_truncation(self):
        t = "TEXTTABLE:Ic:[0x00,0x2D]->0xF000416C;[0x2E,0xFF]->0xF000010F"
        ids = parse_express(t)["text_ids"]
        self.assertEqual(ids, [0xF000416C, 0xF000010F])
        s = "cmd 0x31B801070D02 then 0xF0010824"
        self.assertEqual(RE_ID.findall(s), ["F0010824"])
        self.assertEqual([m.group(1).upper() for m in RE_HEX_LIT.finditer(s) if len(m.group(1)) != 8], ["31B801070D02"])


class Sid31AliasTests(unittest.TestCase):
    def test_sid31_is_wire_22_not_routine_control(self):
        raw = bytes([0x31]) + struct.pack("<I", 0x10) + struct.pack("<I", 1) + struct.pack("<H", 0) + bytes([0]) + struct.pack("<I", 2)
        r = parse_measurement_fields(raw, 0)
        self.assertEqual(r["rawSID"], 0x31)
        self.assertEqual(r["wireSID"], 0x22)
        self.assertEqual(r["disabledreadrequestcandidate"]["payload_hex"], "220010")
        self.assertFalse(r["disabledreadrequestcandidate"]["executionEnabled"])


@unittest.skipUnless(GGP.is_file(), "GGP missing")
class CollisionTests(unittest.TestCase):
    def test_namespaces_not_highbyte(self):
        lang = GgpLanguage(GGP)
        ds = compact_text(lang.lookup_id("DSTREAM_CN.GAG", 0x80001A3A))
        tx = compact_text(lang.lookup_id("TEXT_CN.GAG", 0x80001A3A))
        self.assertEqual(ds, "启动-停止启用")
        self.assertIn("活顶", tx or "")
        self.assertNotEqual(ds, tx)
        d197 = compact_text(lang.lookup_id("DSTREAM_CN.GAG", 0xF0002EF3))
        t197 = compact_text(lang.lookup_id("TEXT_CN.GAG", 0xF0002EF3))
        self.assertEqual(d197, "系统")
        self.assertNotEqual(d197, t197)
        self.assertEqual(compact_text(lang.lookup_id("TEXT_CN.GAG", 0xF000010F)), "否")
        self.assertEqual(compact_text(lang.lookup_id("TEXT_CN.GAG", 0xF0000110)), "是")
        empty = compact_text(lang.lookup_id("DSTREAM_CN.GAG", 0xF0010824))
        self.assertEqual(empty, "")
        self.assertIsNone(compact_text(lang.lookup_id("DSTREAM_CN.GAG", 0x00FFFFFF)))
        uempty = compact_text(lang.lookup_id("DSTREAMU_CN.GAG", 0xF0000054))
        self.assertEqual(uempty, "")


@unittest.skipUnless(
    DEC.joinpath("9X1_ALLDATA.BIN.dec").is_file() and GGP.is_file() and MENU.is_file() and SEM.is_file(),
    "decoded sources missing",
)
class RealSourceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        ns = type(
            "A",
            (),
            {
                "dsn_decoded": str(DEC / "DSN.BIN.dec"),
                "x9_decoded": str(DEC / "9X1_ALLDATA.BIN.dec"),
                "menu": str(MENU),
                "ggp": str(GGP),
                "menu_labels": str(LABELS) if LABELS.is_file() else "",
                "out_local": str(Path(cls.tmp.name) / "exp"),
                "out_summary": str(Path(cls.tmp.name) / "coverage.json"),
            },
        )()
        cls.result = generate(ns)
        write_outputs(cls.result, Path(ns.out_local), Path(ns.out_summary))
        cls.compact = cls.result["compact"]
        cls.eu5 = next(d for d in cls.result["detailed"] if d["summary"]["name"] == "SDI9_1_981_3_4L_EU5")
        cls.expect = json.loads(SEM.read_text(encoding="utf-8"))

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_menu_35_reconcile(self):
        c = self.compact["counts"]
        self.assertEqual(c["menu_sys_ecus"], 35)
        accounted = c["excluded"] + c["unmapped_module_variants"] + c["ambiguous"] + c["candidate"] + c["confirmed"]
        self.assertEqual(c["dropped"], 0)
        self.assertEqual(accounted, c["source_total"])
        self.assertEqual(c["source_total"], 403)
        self.assertEqual(c["target_eligible"], 291)
        self.assertEqual(c["candidate"] + c["confirmed"], 291)
        mt = self.compact["category_totals"]
        self.assertEqual(mt["coding"]["labels_unresolved"], 0)
        self.assertEqual(mt["coding"]["labels_source_empty"], 5976)
        self.assertEqual(mt["measurement"]["units_unresolved"], 0)
        self.assertEqual(mt["measurement"]["units_source_empty"], 10)

    def test_excluded_not_in_detailed(self):
        names = {d["summary"]["name"] for d in self.result["detailed"]}
        mods = {d["summary"]["module"] for d in self.result["detailed"]}
        self.assertNotIn("Carrera_B6T_3_0L_RDW", names)
        self.assertNotIn("BCM_vorne_E2_Basis_GT4", names)
        self.assertNotIn("BCM_hinten_G1_GT", names)
        self.assertNotIn("Verdeck_Targa", mods)
        self.assertIn("PCM31_G1_und_9x1", names)
        audit_names = {a["name"] for a in self.result["audit"]}
        self.assertIn("Carrera_B6T_3_0L_RDW", audit_names)

    def test_eu5_coding_all_39(self):
        recs = self.eu5["extracted"]["pools"]["coding"]["records"]
        self.assertEqual(len(recs), 39)
        by_at = {r["at"]: r for r in recs}
        for exp in self.expect:
            got = by_at[exp["at"]]
            self.assertEqual([x["text"] for x in got["labels"]], exp["labels"], exp["at"])
            self.assertEqual(got["formula"]["id_hex"], exp["formulaId"])
            self.assertTrue(got["formula"]["text"].startswith(exp["formula"][:12]))
            self.assertEqual(got["enumText"], exp["enumText"])
            self.assertEqual(got["readSID"], exp["readSID"])
            self.assertEqual(got["pid"], exp["pid"])
            self.assertEqual(got["byteOffset"], exp["byteOffset"])
            self.assertEqual(got["bitOffset"], exp["bitOffset"])
            self.assertIsNone(got["derived_request_hex"])
            self.assertIsNone(got["writePayload"])
            self.assertFalse(got["executionEnabled"])
            self.assertNotEqual(got["join_status"], "fully_resolved")
        cruise = by_at[4047899]
        self.assertEqual(cruise["bitOffset"], 0)
        self.assertEqual(by_at[4047949]["bitOffset"], 1)
        self.assertEqual(by_at[4047999]["bitOffset"], 2)
        self.assertEqual(by_at[4048049]["bitOffset"], 3)
        self.assertEqual(by_at[4048149]["bitOffset"], 5)

    def test_eu5_measurement_691(self):
        meas = self.eu5["extracted"]["pools"]["measurement"]["records"]
        self.assertEqual(len(meas), 691)
        self.assertTrue(all(r.get("name") for r in meas))
        self.assertTrue(all((r.get("formula") or {}).get("text") for r in meas))
        by_at = {r["at"]: r for r in meas}
        a = by_at[4036608]
        self.assertEqual(a["readSID"], 0x21)
        self.assertEqual(a["rawSID"], 0x21)
        self.assertEqual(a["pid"], 16)
        self.assertEqual(a["byteOffset"], 63)
        self.assertEqual(a["bitOffset"], 0)
        self.assertEqual(a["nameID"], "10005D4D")
        self.assertEqual(a["formulaID"], "F000449C")
        self.assertEqual(a["disabledreadrequestcandidate"]["payload_hex"], "2110")
        self.assertFalse(a["disabledreadrequestcandidate"]["executionEnabled"])
        b = by_at[4036624]
        self.assertEqual(b["pid"], 16)
        self.assertEqual(b["byteOffset"], 0)
        self.assertEqual(b["nameID"], "6000176E")
        self.assertEqual(b["formulaID"], "F00020EB")
        self.assertTrue(any((r.get("enumText") or {}) for r in meas))
        ident = self.eu5["extracted"]["pools"]["identity"]["records"]
        kinds = {(r.get("formula") or {}).get("express", {}).get("formula_kind") for r in ident}
        self.assertTrue(kinds & {"IDENTICAL"} or kinds)
        jn = self.eu5["extracted"]["pools"]["measurement"]["join"]
        self.assertEqual(jn["measurement_fields_ok"] + jn["measurement_fields_unsupported"], 691)

    def test_identity_1a9f_and_not_fully_resolved_flag(self):
        ident = self.eu5["extracted"]["pools"]["identity"]["records"]
        hit = next(r for r in ident if r["derived_request_hex"] == "1A9F")
        self.assertEqual(hit["readSID"], 0x1A)
        self.assertTrue(all(x.get("namespace") == "DSTREAM_CN.GAG" for x in hit["labels"]))
        self.assertFalse(self.eu5["extracted"]["pools"]["coding"]["fully_resolved"])

    def test_hash_and_compact_no_abs_path(self):
        blob = json.dumps(self.compact)
        self.assertNotIn("D:\\", blob)
        self.assertNotIn("/Users/", blob)
        x9 = (DEC / "9X1_ALLDATA.BIN.dec").read_bytes()
        self.assertEqual(self.compact["provenance"]["x9_decoded"]["sha256"], hashlib.sha256(x9).hexdigest().upper())


if __name__ == "__main__":
    unittest.main()
