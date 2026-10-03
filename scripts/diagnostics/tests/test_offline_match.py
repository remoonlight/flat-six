"""Offline matching boundaries. Synthetic fixtures; no private logs or hardware."""
import ast
import copy
import hashlib
from pathlib import Path
import struct
import tempfile
import unittest

from scripts.diagnostics.catalog import load_catalog
from scripts.diagnostics.offline_match import (
    explicit_address_pairs, match_parameter, qualify_saved_identity, request_groups,
    selector_candidates, selector_hits, verify_frames, verify_source,
)


def record(**overrides):
    rec = {"at": 12, "name": "test", "rawSID": 0x21, "wireSID": 0x21,
        "byteOffset": 0, "bitOffset": 0, "fields_status": "ok",
        "formula": {"text": "IDENTICAL:DataType=A_UINT8,BitLength=8,BitMask=0,HighLow=1"},
        "disabledreadrequestcandidate": {"payload_hex": "2110", "status": "ok"}}
    rec.update(overrides)
    return rec


def variant(**overrides):
    return {"profile_id": "variant:one", "module": "PDK", "generation": None, **overrides}


def group(pdus, **overrides):
    return {"groupId": "g1", "txId": 0x71E, "rxId": 0x788, "requestHex": "2110",
        "samples": [{"pduHex": p, "responseFrameId": f"r{i}"} for i, p in enumerate(pdus)], **overrides}


def selector_blob(name="PDK_A3_ohne_Quersperre", text="0x42 10"):
    raw = name.encode() + b"\0"
    selector = struct.pack("<HH", 1, len(text) + 1) + text.encode() + b"\0"
    return struct.pack("<H", len(raw)) + raw + struct.pack("<I", 128) + bytes(128 - 6 - len(raw)) + selector


class MatchTests(unittest.TestCase):
    def test_shared_response_is_one_group_multiple_fields(self):
        gg = [group(["61102A2B", "61102A2B", "61102C2D"])]
        a = match_parameter(variant(), record(), gg, identity_qualified=True)
        b = match_parameter(variant(), record(byteOffset=1, at=13), gg, identity_qualified=True)
        self.assertEqual(a["status"], "offline-matched")
        self.assertEqual(a["captureGroupIds"], b["captureGroupIds"])
        self.assertEqual(a["decodedSampleCount"], 3)
        self.assertEqual(len(a["examples"]), 2)
        self.assertFalse(a["independentFieldValidated"])
        self.assertFalse(a["executionEnabled"])
        self.assertFalse(a["liveApproved"])
        self.assertIsNone(a["writePayload"])

    def test_unqualified_decode_remains_candidate(self):
        got = match_parameter(variant(), record(), [group(["61102A"])], selector_candidate=True)
        self.assertEqual(got["status"], "capture-associated-candidate")

    def test_other_generation_never_matched(self):
        got = match_parameter(variant(generation="982"), record(), [group(["61102A"])], identity_qualified=True)
        self.assertEqual(got["status"], "excluded-982")

    def test_wrong_echo_truncation_and_partial_decode(self):
        for pdu in ("6111FF", "621010FF", "6110"):
            got = match_parameter(variant(), record(), [group([pdu])], identity_qualified=True)
            self.assertEqual(got["status"], "response-decode-failed")
            self.assertEqual(got["decodedSampleCount"], 0)
        got = match_parameter(variant(), record(), [group(["61102A", "6110"])], identity_qualified=True)
        self.assertEqual(got["status"], "partial-decode")

    def test_missing_response_not_live(self):
        got = match_parameter(variant(), record(), [], selector_candidate=True)
        self.assertEqual(got["status"], "selector-candidate-response-missing")
        self.assertEqual(got["decodedSampleCount"], 0)

    def test_undefined_requests_not_invented(self):
        got = match_parameter(variant(), record(disabledreadrequestcandidate={}), [])
        self.assertEqual(got["status"], "request-unresolved")
        self.assertIsNone(got["requestHex"])

    def test_selector_binary_identity_not_ascii_hex(self):
        name = "PDK_A3_ohne_Quersperre"
        blob = selector_blob(name)
        hits = selector_hits(blob, name)
        self.assertEqual(hits[0]["identityDataHex"], "4210")
        variants = [variant(name=name, module="PDK")]
        groups = [group(["5A9F4210"], requestHex="1A9F")]
        matches = selector_candidates(variants, groups, blob, {"PDK": 0x71E})
        self.assertIn("variant:one", matches)
        self.assertFalse(matches["variant:one"]["executionEnabled"])
        self.assertEqual(selector_hits(selector_blob(text="050018"), name)[0]["identityDataHex"], "303530303138")
        self.assertEqual(selector_hits(blob[:20], name), [])

    def test_same_dsn_other_address_not_joined(self):
        blob = selector_blob()
        matches = selector_candidates([variant(name="PDK_A3_ohne_Quersperre")],
            [group(["5A9F4210"], requestHex="1A9F", txId=0x7E0)], blob, {"PDK": 0x71E})
        self.assertEqual(matches, {})

    def test_airbag_entire_compound_selector(self):
        variants = [variant(name="Airbag_A2_6_9x1", module="Airbag")]
        groups = [group(["62F1A2303030303038"], requestHex="22F1A2", txId=0x715),
                  group(["62F19E4169726261672E00"], requestHex="22F19E", txId=0x715)]
        matches = selector_candidates(variants, groups, selector_blob("Airbag_A2_6_9x1", "000008#Airbag."), {"Airbag":0x715})
        got = matches["variant:one"]
        self.assertEqual(got["selectorMatchKind"], "dsn-and-system")
        self.assertEqual(got["unresolvedSelectorConditions"], [])
        self.assertFalse(got["executionEnabled"])
        groups[0] = group(["62F1A2303030303037"], requestHex="22F1A2", txId=0x715)
        got = selector_candidates(variants, groups, selector_blob("Airbag_A2_6_9x1", "000008#Airbag."), {"Airbag":0x715})
        self.assertNotIn("variant:one", got)

    def test_pcm_system_name_does_not_prove_version_prefix(self):
        name = "EV_MUHig6C3Gen2AW7_001"
        variants = [variant(name=name, module="MIB")]
        groups = [group(["62F1A2303031323436"], requestHex="22F1A2", txId=0x773),
            group(["62F19E" + b"EV_MUHig6C3Gen2AW7\0".hex()], requestHex="22F19E", txId=0x773)]
        got = selector_candidates(variants, groups, selector_blob(name,"EV_MUHig6C3Gen2AW7#001"), {"MIB":0x773})
        self.assertEqual(got["variant:one"]["selectorMatchKind"], "system-only")
        self.assertEqual(got["variant:one"]["unresolvedSelectorConditions"], ["001"])
        param = match_parameter(variants[0],record(),[],selector_candidate=True,selector_partial=True)
        self.assertEqual(param["status"],"system-candidate-response-missing")

    def test_gateway_compound_preserves_padding(self):
        name = "GW_A7"
        variants = [variant(name=name,module="GW")]
        groups = [group(["62F1A2" + b"A7.1  ".hex()],requestHex="22F1A2",txId=0x710),
            group(["62F19E" + b"CAN/CAN Gateway\0".hex()],requestHex="22F19E",txId=0x710)]
        got = selector_candidates(variants,groups,selector_blob(name,"A7.1  #CAN/CAN Gateway"),{"GW":0x710})
        self.assertEqual(got["variant:one"]["selectorMatchKind"],"dsn-and-system")

    def test_full_selector_supersedes_weaker_family_candidate(self):
        first = selector_blob("GW_A7", "A7.1  #CAN/CAN Gateway")
        second = bytearray(selector_blob("GW_A3", "CAN/CAN Gateway#003"))
        struct.pack_into("<I",second,2+len("GW_A3")+1,128+len(first))
        variants = [variant(name="GW_A7",module="GW",profile_id="strong"),
                    variant(name="GW_A3",module="GW",profile_id="weak")]
        groups = [group(["62F1A2"+b"A7.1  ".hex()],requestHex="22F1A2",txId=0x710),
                  group(["62F19E"+b"CAN/CAN Gateway\0".hex()],requestHex="22F19E",txId=0x710)]
        got = selector_candidates(variants,groups,first+second,{"GW":0x710})
        self.assertIn("strong",got)
        self.assertNotIn("weak",got)

    def test_capture_partitions_preserved(self):
        frames = {f"q{i}": {"stream": [i % 2, "host_to_controller", 2], "frameSha256": "q"} for i in range(4)}
        rows = [{"requestFrameId": f"q{i}", "requestRecord": i, "txId": 0x71E if i < 3 else 0x7E0,
            "requestHex": "2110", "status": "negative", "returnShape": {"rxId": 0x788}} for i in range(4)]
        gg = request_groups(rows, frames, 1)
        self.assertEqual(len(gg), 4)
        self.assertEqual(sum(g["statusCounts"]["negative"] for g in gg), 4)

    def test_addresses_explicit_and_conflicts_rejected(self):
        self.assertEqual(explicit_address_pairs([{"requestAddress": "unknown-as-fact", "responseAddress": "unknown"},
            {"requestAddress": "710", "responseAddress": "77A"}]), {0x710: 0x77A})
        with self.assertRaisesRegex(ValueError, "conflicting"):
            explicit_address_pairs([{"requestAddress": "710", "responseAddress": r} for r in ("77A", "718")])

    def test_source_hash_mismatch_refused(self):
        with tempfile.TemporaryDirectory() as temp:
            p = Path(temp) / "source"; p.write_bytes(b"original")
            verify_source(p, hashlib.sha256(b"original").hexdigest())
            p.write_bytes(b"changed")
            with self.assertRaisesRegex(ValueError, "source-hash-mismatch"):
                verify_source(p, hashlib.sha256(b"original").hexdigest())

    def test_corrupted_and_duplicate_frames_refused(self):
        raw = bytes.fromhex("55AAF0F80003112103")
        checksum = 0
        for b in raw[2:]:
            checksum ^= b
        raw += bytes([checksum])
        frame = {"frameId": "a", "frameHex": raw.hex(), "frameSha256": hashlib.sha256(raw).hexdigest(),
            "stream": [1, "host_to_controller", 2], "length": len(raw), "sequenceByte": 17, "commandByte": 33}
        verify_frames([frame])
        with self.assertRaisesRegex(ValueError, "duplicate"):
            verify_frames([frame, frame])
        for key, value in (("frameSha256", "0" * 64), ("sequenceByte", 18), ("length", len(raw) + 1)):
            with self.assertRaisesRegex(ValueError, "invalid-frame"):
                verify_frames([{**frame, key: value}])

    def test_duplicate_definitions_share_logical_key(self):
        a = match_parameter(variant(), record(), [group(["61102A"])], identity_qualified=True)
        b = match_parameter(variant(), record(at=13), [group(["61102A"])], identity_qualified=True)
        self.assertNotEqual(a["parameterId"], b["parameterId"])
        self.assertEqual(a["logicalParameterKey"], b["logicalParameterKey"])

    def test_no_transport_or_database_imports(self):
        src = Path(__file__).parents[1] / "offline_match.py"
        tree = ast.parse(src.read_text(encoding="utf8"))
        for node in ast.walk(tree):
            names = [a.name for a in node.names] if isinstance(node, ast.Import) else [node.module or ""] if isinstance(node, ast.ImportFrom) else []
            for name in names:
                self.assertNotIn(name.split(".")[-1], {"serial", "transport", "sqlite3", "sessions", "vlinker", "vnci"})


class IdentityTests(unittest.TestCase):
    def test_raw_identity_decoded_without_cached_result(self):
        catalog = load_catalog()
        profile = catalog["profiles"][0]
        manifest = {"mode": "live", "simulation": False, "profileId": profile["id"], "results": []}
        raw = [{"dir": "tx", "cmd": "ATSH 7E0"}, {"dir": "tx", "cmd": "ATCRA 7E8"}]
        fields = {"dme-dsn": "dsn", "dme-software": "software", "dme-hardware": "hardware",
            "dme-porsche-part": "porschePart", "dme-hardware-part": "hardwarePart"}
        for op in profile["operations"]:
            if op["id"] not in fields:
                continue
            pdu = bytes.fromhex(op["positivePrefixHex"]) + op["expectedValue"].encode("ascii")
            if len(pdu) <= 7:
                response = "7E8 " + " ".join(f"{b:02X}" for b in bytes([len(pdu)]) + pdu) + "\r>"
            else:
                first = bytes([0x10, len(pdu)]) + pdu[:6]
                parts = [first] + [bytes([0x21 + n // 7]) + pdu[6 + n:13 + n] for n in range(0, len(pdu) - 6, 7)]
                response = "\r".join("7E8 " + " ".join(f"{b:02X}" for b in part) for part in parts) + "\r>"
            req = op["requestHex"]
            raw += [{"dir": "tx", "req": req, "sf": "02 " + req},
                    {"dir": "rx", "hex": response.encode().hex()}]
            manifest["results"].append({"role": "identity", "field": fields[op["id"]], "operationId": op["id"],
                "ok": True, "payload_hex": pdu.hex(), "decoded": {"text": "wrong cached value"}})
        got = qualify_saved_identity(manifest, raw, catalog)
        self.assertTrue(got["observedProfileMatch"], got)
        self.assertEqual(got["identity"]["dsn"], "P200")
        modified = copy.deepcopy(manifest)
        modified["results"][0]["synthetic"] = True
        self.assertFalse(qualify_saved_identity(modified, raw, catalog)["observedProfileMatch"])
        modified = copy.deepcopy(manifest)
        modified["results"].append({**modified["results"][0], "synthetic": True})
        rejected = qualify_saved_identity(modified, raw, catalog)
        self.assertFalse(rejected["observedProfileMatch"])
        self.assertIsNone(rejected["catalogProfileId"])
        self.assertIsNone(rejected["matchedCatalogProfileId"])
        self.assertIsNone(rejected["variantLink"])

    def test_cached_success_without_raw_cannot_qualify(self):
        manifest = {"mode": "live", "simulation": False, "profileId": "porsche-981-2014-dme",
            "identityQualification": {"observedProfileMatch": True}, "results": [{"role": "identity",
                "field": "dsn", "ok": True, "operationId": "dme-dsn", "payload_hex": "5A9F50323030"}]}
        got = qualify_saved_identity(manifest, [], load_catalog())
        self.assertFalse(got["observedProfileMatch"])
        self.assertEqual(got["status"], "raw-identity-rejected")
        self.assertTrue(got["rawErrors"])
        for key, value in (("simulation", True), ("mode", "simulation")):
            modified = copy.deepcopy(manifest); modified[key] = value
            self.assertEqual(qualify_saved_identity(modified, [], load_catalog())["status"], "ignored-simulation-or-unknown-mode")


if __name__ == "__main__":
    unittest.main()
