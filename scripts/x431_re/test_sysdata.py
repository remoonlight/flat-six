#!/usr/bin/env python3
"""SYS_DATA exact GetSubSys/GetDtcData/GetCommData; malformed; missing; hash."""
from __future__ import annotations

import hashlib
import tempfile
import unittest
from pathlib import Path

from scripts.x431_re.sysdata import (
    EXPECTED_BIN_SHA,
    FAMILY_9X1,
    FILE_LOADER,
    _u32,
    capture_vs_static,
    canonical_dumps,
    find_named_variant,
    load_sysdata,
    parse_cmd,
    parse_comm,
    parse_dtc_table,
    parse_payload,
    parse_subsys,
    parse_systems,
    recover_logical,
)

ROOT = Path(__file__).resolve().parents[2]
PKG = ROOT / ".local/x431-re/2026-09-27-protocol/package/PORSCHE_SYS_DATA.BIN"
SCRATCH = ROOT / ".local/x431-re/2026-09-27-offline-completion/protocol/PORSCHE_SYS_DATA.logical.dec"
COORD = ROOT / ".local/x431-re/2026-09-27-offline-completion/coordinator-sys-data.logical.dec"
EU5 = "SDI9_1_981_3_4L_EU5"
GW_A71 = "CAN_CAN_Gateway_A7_1"


class SysdataTests(unittest.TestCase):
    def test_file_loader_repo_relative(self):
        self.assertEqual(FILE_LOADER, ROOT / ".local/x431-re/2026-09-27-decode/file-loader")

    @unittest.skipUnless(PKG.is_file(), "SYS_DATA missing")
    def test_recover_and_exact_eu5_gw(self):
        rec = recover_logical(PKG, SCRATCH)
        self.assertTrue(rec["ok"])
        self.assertEqual(rec["sha256"], EXPECTED_BIN_SHA)
        self.assertTrue(SCRATCH.is_file())
        self.assertEqual(SCRATCH.read_bytes(), COORD.read_bytes())
        sys = load_sysdata(PKG, scratch=None)
        self.assertTrue(sys["checked"])
        self.assertEqual(sys["systemCount"], 35)
        self.assertEqual(sys["familyIdQueried"], FAMILY_9X1)
        self.assertFalse(sys["xmlDriveLinks"]["presentInTree"])
        self.assertEqual(sys["xmlDriveLinks"]["class"], "missing-external-source")
        dme = sys["byEcuId"][1]
        self.assertEqual(dme["variantCount"], 2)
        self.assertEqual(dme["links"][0]["service"]["name"], "DME_BDE_Continental_BaseSys")
        self.assertEqual(dme["links"][1]["service"]["name"], "MED17_1_11_BaseSys")
        for ln in dme["links"]:
            d11 = ln["comm"]["derived11bit"]
            self.assertEqual(d11["txHex"], "7E0")
            self.assertEqual(d11["rxHex"], "7E8")
            self.assertEqual(d11["formula"], "stored_u32_le>>5")
            self.assertFalse(d11["hardwareConversionNativeConfirmed"])
            self.assertEqual(d11["class"], "derived-candidate")
            self.assertNotIn("lsb3Unresolved", d11)
        eu5 = find_named_variant(dme, EU5)
        self.assertIsNotNone(eu5)
        self.assertEqual(eu5["dtcTableOff"], 0x329ED)
        self.assertFalse(eu5["fittedClaim"])
        self.assertTrue(eu5["baseSys"] is False)
        reads = [c["wireHex"] for c in eu5["dtc"]["read"] if c.get("wireHex")]
        self.assertEqual(reads, ["1089", "1800FF00"])
        self.assertEqual(eu5["dtc"]["dtcRead"]["wireHex"], "1800FF00")
        self.assertEqual(eu5["dtc"]["sessionPrecondition"][0]["wireHex"], "1089")
        self.assertEqual(eu5["dtc"]["dtcClear"]["wireHex"], "14FF00")
        self.assertNotIn("19022C", reads)
        self.assertIsNone(eu5["writePayload"])
        base = find_named_variant(dme, "DME_BDE_Continental_BaseSys")
        self.assertEqual(base["dtcTableOff"], 0x32A0D)
        self.assertTrue(base["baseSys"])
        gw = sys["byEcuId"][9]
        self.assertEqual(gw["variantCount"], 1)
        d11 = gw["links"][0]["comm"]["derived11bit"]
        self.assertEqual(d11["txHex"], "710")
        self.assertEqual(d11["rxHex"], "77A")
        self.assertEqual(d11["storedRxHex"], "0000EF40")
        a71 = find_named_variant(gw, GW_A71)
        self.assertEqual(a71["dtcTableOff"], 0x329CD)
        gw_reads = [c["wireHex"] for c in a71["dtc"]["read"] if c.get("wireHex")]
        self.assertEqual(gw_reads, ["1003", "190208"])
        self.assertEqual(a71["dtc"]["dtcRead"]["wireHex"], "190208")
        self.assertNotIn("1800FF00", gw_reads)
        self.assertEqual(a71["dtc"]["dtcClear"]["wireHex"], "14FFFFFF")
        pdk = sys["byEcuId"][2]
        pd11 = pdk["links"][0]["comm"]["derived11bit"]
        self.assertEqual(pd11["txHex"], "71E")
        self.assertEqual(pd11["rxHex"], "788")
        gate = capture_vs_static(sys)
        self.assertTrue(gate["dmeExact"])
        self.assertTrue(gate["gwExact"])
        self.assertFalse(gate["dmeHasAdjacent19022C"])
        self.assertFalse(gate["gwHasAdjacent1800FF00"])
        self.assertTrue(gate["dmeVariantIndexNotEu5Guess"])
        self.assertEqual(sys["linkCount"], 40)
        self.assertEqual(sys["kindCounts"].get(0x10), 35)
        self.assertEqual(sys["kindCounts"].get(0x30), 4)
        self.assertEqual(sys["kindCounts"].get(0x50), 1)
        for eid in (30, 31, 76, 78):
            comm = sys["byEcuId"][eid]["links"][0]["comm"]
            self.assertEqual(comm["protocolKind"], 0x30)
            self.assertEqual(comm["class"], "static-source-confirmed")
            self.assertEqual(comm["pairCount"], 3)
            self.assertTrue(comm["selectedPairUnknown"])
            self.assertIsNone(comm["pairChoice"]["selectedPair"])
            self.assertNotIn("txHex", comm["derived11bit"])
            self.assertTrue(comm["notNamedUds"])
            self.assertEqual(len(comm["pairs"]), 3)
            self.assertIsNone(comm["pairs"][0]["role"])
            self.assertEqual(comm["derived11bit"]["class"], "derived-candidate")
            self.assertFalse(comm["derived11bit"]["hardwareConversionNativeConfirmed"])
        k50 = sys["byEcuId"][88]["links"][0]["comm"]
        self.assertEqual(k50["protocolKind"], 0x50)
        self.assertEqual(k50["class"], "static-source-confirmed")
        self.assertEqual(k50["pairCount"], 1)
        self.assertEqual(k50["pairs"][0]["mode"], 5)
        self.assertIsNotNone(k50["pairs"][0]["mode5Extra"])
        self.assertEqual(k50["derived11bit"]["class"], "derived-candidate")
        self.assertFalse(k50["selectedPairUnknown"])
        self.assertEqual(k50["derived11bit"]["formula"], "stored_u32_le>>5")

    @unittest.skipUnless(PKG.is_file(), "SYS_DATA missing")
    def test_bind_only_target_names(self):
        sys = load_sysdata(PKG, target_names=[EU5, GW_A71])
        eu5 = find_named_variant(sys["byEcuId"][1], EU5)
        base = find_named_variant(sys["byEcuId"][1], "DME_BDE_Continental_BaseSys")
        self.assertTrue(eu5["bindTarget"])
        self.assertFalse(base["bindTarget"])
        self.assertFalse(eu5["fittedClaim"])
        self.assertFalse(base["fittedClaim"])

    @unittest.skipUnless(PKG.is_file(), "SYS_DATA missing")
    def test_deterministic(self):
        a = load_sysdata(PKG)
        b = load_sysdata(PKG)
        self.assertEqual(canonical_dumps(a["byEcuId"][1]), canonical_dumps(b["byEcuId"][1]))

    def test_missing_source(self):
        d = load_sysdata(Path(tempfile.mkdtemp()) / "no.bin")
        self.assertFalse(d["present"])
        self.assertEqual(d["class"], "missing-external-source")

    def test_modified_hash(self):
        p = Path(tempfile.mkdtemp()) / "PORSCHE_SYS_DATA.BIN"
        p.write_bytes(b"YZJM" + b"\x00" * 80)
        d = load_sysdata(p)
        self.assertTrue(d["present"])
        self.assertFalse(d["checked"])
        self.assertEqual(d["class"], "not-confirmed")
        self.assertNotEqual(hashlib.sha256(p.read_bytes()).hexdigest(), EXPECTED_BIN_SHA)

    def test_malformed_payload(self):
        with self.assertRaises(ValueError):
            parse_payload(b"\x22")
        with self.assertRaises(ValueError):
            parse_payload(bytes([1, 13, 0, 0, 0, 1]))

    def test_malformed_subsys_count_string_negative(self):
        with self.assertRaises(ValueError):
            parse_subsys(bytes([200]), 0)
        blob = bytes([1, 10, 0]) + b"AB"
        with self.assertRaises(ValueError):
            parse_subsys(blob, 0)
        name = b"X\x00"
        slots = b"\xff\xff\xff\xff" + b"\x00" * 20
        neg = bytes([1, 2, 0]) + name + slots
        with self.assertRaises(ValueError):
            parse_subsys(neg, 0)

    def test_cmd_n_ff_unsupported(self):
        rec = parse_cmd(bytes([0x14, 0xFF, 0xFF, 0x00]), 0)
        self.assertEqual(rec["class"], "unsupported-structured")
        self.assertEqual(rec["payloadLen"], 0xFF)
        self.assertNotIn("wireHex", rec)

    def test_cmd_n0_session(self):
        rec = parse_cmd(bytes([0x10, 0x89, 0x00]), 0)
        self.assertEqual(rec["wireHex"], "1089")
        self.assertEqual(rec["role"], "sessionPrecondition")
        self.assertEqual(rec["payloadLen"], 0)

    def test_negative_offset_all_entry_parsers(self):
        with self.assertRaises(ValueError):
            _u32(b"\x00" * 8, -1)
        with self.assertRaises(ValueError):
            parse_cmd(b"\x10\x03\x00", -1)
        with self.assertRaises(ValueError):
            parse_comm(b"\x10\x03\x01" + b"\x00" * 40, -1, 20)
        with self.assertRaises(ValueError):
            parse_dtc_table(b"\x00" * 32, -1)
        with self.assertRaises(ValueError):
            parse_subsys(b"\x00" * 32, -1)
        with self.assertRaises(ValueError):
            parse_systems(b"\x00" * 32, -1)

    def test_cmd_ptr_zero_absent(self):
        blob = bytes([3]) + b"\x00" * 10 + bytes([1]) + b"\x00" * 4 + b"\x00" * 4
        rec = parse_dtc_table(blob, 0)
        self.assertEqual(rec["readCount"], 1)
        self.assertIsNone(rec["read"][0])

    def test_embedded_nul_name_rejected(self):
        name = b"A\x00B\x00"
        blob = bytes([1, 4, 0]) + name + b"\x00" * 24
        with self.assertRaises(ValueError):
            parse_subsys(blob, 0)

    def test_synthetic_multipair_and_truncation(self):
        pair = (
            b"\x00\xfc\x00\x00\x00\xfd\x00\x00\x03\x00"
            + b"\x00\xe2\x00\x00\x40\xef\x00\x00\x03\x00"
        )
        blob = b"\x10\x03\x02" + pair + b"\x00" + b"\x00" * 12
        rec = parse_comm(blob, 0, len(blob))
        self.assertEqual(rec["pairCount"], 2)
        self.assertTrue(rec["selectedPairUnknown"])
        self.assertEqual(rec["pairs"][0]["derived11bit"]["txHex"], "7E0")
        self.assertEqual(rec["pairs"][1]["derived11bit"]["rxHex"], "77A")
        self.assertNotIn("txHex", rec["derived11bit"])
        with self.assertRaises(ValueError):
            parse_comm(blob[:-4], 0, len(blob) - 4)


if __name__ == "__main__":
    unittest.main()
