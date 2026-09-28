#!/usr/bin/env python3
"""Protocol inventory: DME/GW agreement, no 982 inheritance, no invented addresses."""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
if not __package__:
    sys.path.insert(0, str(HERE))

if __package__:
    from .protocol_inventory import (
        EU5,
        GETODX,
        GW_A71,
        TARGET_ECU_COUNT,
        build_inventory,
        canonical_dumps,
        inspect_native,
        read_dsn_selectors,
        run,
        stream_variants,
    )
else:
    from protocol_inventory import (
        EU5,
        GETODX,
        GW_A71,
        TARGET_ECU_COUNT,
        build_inventory,
        canonical_dumps,
        inspect_native,
        read_dsn_selectors,
        run,
        stream_variants,
    )
DEC = ROOT / ".local/x431-re/2026-09-27-decode/file-loader/decoded/DSN.BIN.dec"

COV = ROOT / "data/seed/diagnostics/coverage-981-982.v1.json"
CAT = ROOT / "data/seed/diagnostics/catalog.v1.json"
WS = ROOT / "data/seed/diagnostics/workshop-registry.v1.json"
AGREE = ROOT / ".local/x431-re/2026-09-27-981982/validation/agreement.json"
PKG = ROOT / ".local/x431-re/2026-09-27-protocol/package"


def _ns(**kw):
    d = {
        "repo": str(ROOT),
        "coverage": str(COV),
        "catalog": str(CAT),
        "workshop": str(WS),
        "variants": "",
        "source_root": "",
        "decoded_root": "",
        "agreement": "",
    }
    d.update(kw)
    return type("A", (), d)()


def _synthetic_jsonl(path: Path) -> None:
    eu5 = {
        "profile_id": f"9x1:DME_BDE_Continental:{EU5}",
        "module": "DME_BDE_Continental",
        "name": EU5,
        "off": 4035362,
        "membership": "confirmed",
        "generation": "981",
        "physical_fit": "confirmed_generation_label",
        "odx_pointers": {
            "0x40a250": 4035382,
            "0x40a254": 4036449,
            "0x40a258": 4047744,
            "0x40a25c": 4049181,
            "0x40a260": 4054984,
        },
        "pools": {k: {"decoded": 1} for k in GETODX.values()},
        "pool_records": {
            "identity": {
                "records": [
                    {"derived_request_hex": "1A9F"},
                    {"derived_request_hex": "1A95"},
                ]
            },
            "dtc": {"service_header": 24, "records": []},
            "coding": {
                "records": [
                    {"byteOffset": 0, "bitOffset": 0, "writePayload": None, "executionEnabled": False}
                ]
            },
        },
        "executionEnabled": False,
        "writePayload": None,
    }
    gw = {
        "profile_id": f"9x1:CAN_CAN_Gateway:{GW_A71}",
        "module": "CAN_CAN_Gateway",
        "name": GW_A71,
        "off": 930019,
        "membership": "candidate",
        "generation": None,
        "physical_fit": "unverified",
        "odx_pointers": {
            "0x40a250": 930039,
            "0x40a254": 930649,
            "0x40a258": 963422,
            "0x40a25c": 971295,
            "0x40a260": 974017,
        },
        "pools": {k: {"decoded": 1} for k in GETODX.values()},
        "pool_records": {
            "identity": {"records": [{"derived_request_hex": "22F1A2"}]},
            "dtc": {"service_header": 24, "records": []},
            "coding": {"records": []},
        },
        "executionEnabled": False,
        "writePayload": None,
    }
    fake_982 = {
        "profile_id": "9x1:DME_BDE_Continental:SDI9_1_982_dummy",
        "module": "DME_BDE_Continental",
        "name": "SDI9_1_982_dummy",
        "off": 1,
        "membership": "confirmed",
        "generation": "982",
        "physical_fit": "confirmed_generation_label",
        "odx_pointers": {
            "0x40a250": 21,
            "0x40a254": 30,
            "0x40a258": 40,
            "0x40a25c": 50,
            "0x40a260": 60,
        },
        "pools": {k: {"decoded": 0} for k in GETODX.values()},
        "pool_records": {"identity": {"records": []}, "dtc": {"service_header": 24, "records": []}, "coding": {"records": []}},
        "executionEnabled": False,
        "writePayload": None,
    }
    door = {
        "profile_id": "9x1:TUER_VORNE:TUER_VORNE_BaseSys",
        "module": "TUER_VORNE",
        "name": "TUER_VORNE_BaseSys",
        "off": 99,
        "membership": "candidate",
        "generation": None,
        "physical_fit": "unverified",
        "odx_pointers": {"0x40a250": 119, "0x40a254": 120, "0x40a258": 121, "0x40a25c": 122, "0x40a260": 123},
        "pools": {k: {"decoded": 0} for k in GETODX.values()},
        "pool_records": {"identity": {"records": []}, "dtc": {"service_header": 24, "records": []}, "coding": {"records": []}},
        "executionEnabled": False,
        "writePayload": None,
    }
    with path.open("w", encoding="utf-8") as f:
        for row in (eu5, gw, fake_982, door):
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


class MissingSourceTests(unittest.TestCase):
    def test_source_absent_still_35_groups_and_unclaimed(self):
        inv = run(_ns())
        self.assertEqual(len(inv["groups"]), TARGET_ECU_COUNT)
        self.assertFalse(inv["sourceChecked"])
        self.assertFalse(inv["executionEnabled"])
        self.assertIsNone(inv["writePayload"])
        note = " ".join(inv["notes"])
        self.assertIn("not checked", note.lower())
        ids = [g["ecuId"] for g in inv["groups"]]
        self.assertEqual(ids, sorted(ids))
        self.assertNotIn(991, ids)

    def test_deterministic(self):
        a = canonical_dumps(run(_ns()))
        b = canonical_dumps(run(_ns()))
        self.assertEqual(a, b)


class PartitionAndJoinTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.jsonl = Path(self.tmp.name) / "v.jsonl"
        _synthetic_jsonl(self.jsonl)
        self.inv = run(_ns(variants=str(self.jsonl)))

    def tearDown(self):
        self.tmp.cleanup()

    def test_count_partition_and_native_offset_joins(self):
        self.assertEqual(len(self.inv["groups"]), 35)
        self.assertEqual(self.inv["counts"]["streamedVariants"], 4)
        dme = next(g for g in self.inv["groups"] if g["ecuId"] == 1)
        eu5 = next(v for v in dme["variants"] if v["name"] == EU5)
        self.assertEqual(eu5["off"] + 20, eu5["odxPointers"]["0x40a250"])
        self.assertEqual(GETODX["0x40a250"], "identity")
        self.assertEqual(eu5["odxPointers"]["0x40a260"], 4054984)
        gw = next(g for g in self.inv["groups"] if g["ecuId"] == 9)
        a71 = next(v for v in gw["variants"] if v["name"] == GW_A71)
        self.assertEqual(a71["off"] + 20, a71["odxPointers"]["0x40a250"])

    def test_no_982_inheritance_and_no_invented_address(self):
        dme = next(g for g in self.inv["groups"] if g["ecuId"] == 1)
        self.assertEqual(dme["generations"]["981"]["address"]["txId"], "7E0")
        self.assertIsNone(dme["generations"]["982"]["address"])
        fake = next(v for v in dme["variants"] if "982" in v["name"])
        self.assertEqual(fake["generation"], "982")
        addrs = []
        for g in self.inv["groups"]:
            for gen in g["generations"].values():
                a = gen.get("address")
                if a:
                    addrs.append((g["ecuId"], a["txId"], a["rxId"]))
                self.assertFalse(gen["fittedClaim"])
                self.assertIsNone(gen["writePayload"])
        self.assertEqual(set(addrs), {(1, "7E0", "7E8"), (9, "710", "77A")})
        blob = canonical_dumps(self.inv)
        self.assertNotIn("7E1", blob)
        self.assertNotIn('"18FF"', blob)
        self.assertIsNone(dme["services"]["dtcRead"]["nativeTemplate"]["fullPayloadHex"])
        self.assertIsNone(dme["services"]["codingWrite"]["requestHex"])
        self.assertFalse(dme["services"]["dtcRead"]["executionEnabled"])

    def test_no_offtarget_991_groups(self):
        labels = " ".join(g["label"] for g in self.inv["groups"])
        self.assertNotIn("Taycan", labels)
        self.assertEqual(self.inv["scope"]["referenceOnly"], ["991", "GT4CS"])
        door30 = next(g for g in self.inv["groups"] if g["ecuId"] == 30)
        door31 = next(g for g in self.inv["groups"] if g["ecuId"] == 31)
        self.assertEqual(door30["streamedVariantCount"], 1)
        self.assertEqual(door31["streamedVariantCount"], 1)
        self.assertEqual(door30["variants"][0]["name"], door31["variants"][0]["name"])



class AgreementTests(unittest.TestCase):
    @unittest.skipUnless(AGREE.is_file(), "agreement.json missing")
    def test_dme_gw_agreement_exact_no_conflict(self):
        inv = run(_ns(agreement=str(AGREE)))
        self.assertEqual(inv["agreement"]["EXACT_MATCH"], 21)
        self.assertEqual(inv["agreement"]["CONFLICT"], 0)
        dme = next(g for g in inv["groups"] if g["ecuId"] == 1)
        gw = next(g for g in inv["groups"] if g["ecuId"] == 9)
        self.assertEqual(dme["services"]["dtcRead"]["observed"]["requestHex"], "1800FF00")
        self.assertEqual(gw["services"]["dtcRead"]["observed"]["requestHex"], "190208")
        self.assertEqual(dme["services"]["dtcRead"]["observed"]["scope"], "981-DME-only")
        self.assertEqual(gw["services"]["dtcRead"]["observed"]["scope"], "981-Gateway-only")
        cat = json.loads(CAT.read_text(encoding="utf-8"))
        dme_ids = {o["requestHex"] for p in cat["profiles"] if p["ecu"] == "DME" for o in p["operations"]}
        self.assertIn("1A9F", dme_ids)
        self.assertIn("1800FF00", dme_ids)


class RealNativeTests(unittest.TestCase):
    @unittest.skipUnless((PKG / "libPORSCHE_COMM.so").is_file(), "native package missing")
    def test_native_symbols_and_no_hardcoded_dtc_payload_in_so(self):
        n = inspect_native(PKG)
        self.assertTrue(n["checked"])
        self.assertEqual(n["symbols"]["libPORSCHE_LINK.so"]["PorscheLink"]["va"], "0x17654")
        self.assertEqual(n["symbols"]["libPORSCHE_DTCF.so"]["PorscheReadDtc"]["fileOff"], 88776)
        lit = n["literalPayloads"]["libPORSCHE_COMM.so"]
        self.assertEqual(lit["1800FF00"], -1)
        self.assertEqual(lit["190208"], -1)
        hits = (n.get("commSid19Immediate") or {}).get("hits") or []
        self.assertTrue(any(h["text"] == "movs r1, #0x19" for h in hits))


class DsnSelectorTests(unittest.TestCase):
    @unittest.skipUnless(DEC.is_file(), "DSN.BIN.dec missing")
    def test_p200_and_a71_at_agreement_offsets(self):
        d = read_dsn_selectors(DEC)
        self.assertTrue(d["rows"][EU5]["match"])
        self.assertEqual(d["rows"][EU5]["ident"], "P200")
        self.assertTrue(d["rows"][GW_A71]["match"])
        self.assertEqual(d["rows"][GW_A71]["ident"], "A7.1")


class StreamHelperTests(unittest.TestCase):
    def test_stream_header_partition(self):
        tmp = Path(tempfile.mkdtemp()) / "v.jsonl"
        _synthetic_jsonl(tmp)
        s = stream_variants(tmp)
        self.assertEqual(s["count"], 4)
        self.assertEqual(s["dtcServiceHeaderCounts"]["24"], 4)
        self.assertEqual(s["membership"]["confirmed"], 2)
        self.assertEqual(s["membership"]["candidate"], 2)


class BuildInventoryDisabledTests(unittest.TestCase):
    def test_operational_flags(self):
        cov = json.loads(COV.read_text(encoding="utf-8"))
        cat = json.loads(CAT.read_text(encoding="utf-8"))
        ws = json.loads(WS.read_text(encoding="utf-8"))
        inv = build_inventory(
            repo=ROOT,
            coverage=cov,
            workshop=ws,
            catalog=cat,
            variants=None,
            native={"checked": False},
            literals={},
            dsn={"present": False},
            agreement={"present": False},
            sources=[],
        )
        self.assertFalse(inv["executionEnabled"])
        self.assertIsNone(inv["writePayload"])
        self.assertFalse(inv["groups"][0]["executionEnabled"])
        self.assertIsNone(inv["groups"][0]["writePayload"])
        wf = inv["groups"][0]["generations"]["981"]["workflow"]
        self.assertEqual(wf["init"]["can"]["class"], "cached-reference")
        self.assertEqual(inv["groups"][0]["services"]["negativeResponse"]["projectParser"]["class"], "static-source-confirmed")


class CorrectionTests(unittest.TestCase):
    def test_empty_catalog_has_no_hardcoded_address(self):
        empty = Path(tempfile.mkdtemp()) / "cat.json"
        empty.write_text(json.dumps({"schemaVersion": 1, "profiles": []}), encoding="utf-8")
        inv = run(_ns(catalog=str(empty)))
        for g in inv["groups"]:
            for gen in g["generations"].values():
                self.assertIsNone(gen.get("address"))
            blob = json.dumps(g["workflow"].get("addressing"))
            self.assertNotIn("7E0", blob)

    def test_empty_source_dir_not_source_checked(self):
        d = Path(tempfile.mkdtemp())
        n = inspect_native(d)
        self.assertTrue(n["present"])
        self.assertFalse(n["checked"])
        inv = run(_ns(source_root=str(d)))
        self.assertFalse(inv["sourceChecked"])
        self.assertEqual(inv["groups"][0]["generations"]["981"]["workflow"]["init"]["link"]["class"], "cached-reference")

    def test_malformed_dsn_not_confirmed(self):
        p = Path(tempfile.mkdtemp()) / "DSN.BIN.dec"
        p.write_bytes(b"\x00" * 200000)
        d = read_dsn_selectors(p)
        self.assertFalse(d["rows"][EU5]["match"])
        self.assertNotEqual(d["rows"][EU5]["class"], "static-source-confirmed")

    def test_identity_not_shared_to_unrelated_variant(self):
        tmp = Path(tempfile.mkdtemp()) / "v.jsonl"
        _synthetic_jsonl(tmp)
        inv = run(_ns(variants=str(tmp)))
        dme = next(g for g in inv["groups"] if g["ecuId"] == 1)
        self.assertEqual(dme["generations"]["981"]["partSoftwareConstraints"]["appliesToVariants"], [EU5])
        self.assertNotIn("SDI9_1_982_dummy", dme["generations"]["981"]["partSoftwareConstraints"]["appliesToVariants"])
        self.assertIsNone(dme["generations"]["982"]["partSoftwareConstraints"]["fields"])

    def test_no_source_unavailable_gap_class(self):
        tmp = Path(tempfile.mkdtemp()) / "v.jsonl"
        _synthetic_jsonl(tmp)
        inv = run(_ns(variants=str(tmp)))
        blob = canonical_dumps(inv)
        self.assertNotIn("source-unavailable", blob)

    @unittest.skipUnless((PKG / "libPORSCHE_COMM.so").is_file(), "native package missing")
    def test_transcan_and_uds_strip_layouts(self):
        n = inspect_native(PKG)
        self.assertTrue(n["checked"])
        t = n["deep"]["transCan"]["tableHi"]["bytes"]
        self.assertEqual(t, [5, 7, 3, 2, 4, 6, 1, 0])
        self.assertEqual(n["deep"]["udsRead"]["afterCommReturn"]["sid22"]["skipBytes"], 2)
        self.assertEqual(n["deep"]["udsRead"]["afterCommReturn"]["sid1Aor21"]["skipBytes"], 1)
        self.assertEqual(n["deep"]["udsRead"]["requestPack"]["sid22"]["internalLength"], 4)
        self.assertNotIn("pciLen", n["deep"]["udsRead"]["requestPack"]["sid22"])
        self.assertEqual(n["deep"]["udsRead"]["commCall"]["resolves"], "PorscheSendCommandByIDEX")
        self.assertEqual(n["deep"]["coding"]["security"]["class"], "runtime-seed-needed")
        self.assertEqual(n["deep"]["canPara"]["remaining"]["class"], "timing-labels-not-identified")
        self.assertNotIn("62+DID", n["deep"]["udsRead"]["positiveLayoutStripped"]["sid22"])
        self.assertEqual(n["deep"]["udsRead"]["positiveLayoutStripped"]["positiveSidBeforeStrip"]["class"], "exact-branch-unknown")
        self.assertTrue(n["deep"]["dtcTraces"]["PorscheReadDtc"]["indirectSlots"])


if __name__ == "__main__":
    unittest.main()
