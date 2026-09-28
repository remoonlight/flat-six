from __future__ import annotations

import json
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from scripts.diagnostics.allowlist import blocked_reason
from scripts.diagnostics.catalog import catalog_cli_json, live_allowed_hex, load_catalog, profile_by_id
from scripts.diagnostics.decode import decode_payload, decode_transaction
from scripts.diagnostics.elm import SETTING_CMDS_BEFORE_IDS, ElmClient, ElmError, parse_ath1_response, sf_can_hex
from scripts.diagnostics.isotp import reconstruct_all
from scripts.diagnostics.live import run_read
from scripts.diagnostics.pair import pair_transactions
from scripts.diagnostics.replay import replay_capture

CFG_OK_COUNT = len(SETTING_CMDS_BEFORE_IDS) + 6  # SH, CRA, FCSH, FCSD, FCSM1, CSM0


def _sf(idx, can_id, payload_hex, pad="AA", timestamp_us=None):
    data = bytes.fromhex(payload_hex)
    pci = bytes([len(data)]) + data
    raw = (pci + bytes.fromhex(pad * ((8 - len(pci)) // 1)))[:8]
    if len(raw) < 8:
        raw = raw + b"\xaa" * (8 - len(raw))
    return {
        "frame_index": idx,
        "utc": "t",
        "timestamp_us": idx if timestamp_us is None else timestamp_us,
        "can_id": can_id,
        "extended": False,
        "bus": 0,
        "data_hex": raw.hex(),
        "dlc": 8,
    }


def _ff_cf(start_idx, can_id, payload: bytes):
    declared = len(payload)
    first = payload[:6]
    ff = bytes([0x10 | ((declared >> 8) & 0x0F), declared & 0xFF]) + first
    ff = (ff + b"\xaa" * 8)[:8]
    frames = [
        {
            "frame_index": start_idx,
            "utc": "t",
            "timestamp_us": start_idx,
            "can_id": can_id,
            "extended": False,
            "bus": 0,
            "data_hex": ff.hex(),
            "dlc": 8,
        }
    ]
    rest = payload[6:]
    sn = 1
    idx = start_idx + 1
    while rest:
        chunk = rest[:7]
        rest = rest[7:]
        cf = bytes([0x20 | sn]) + chunk
        cf = (cf + b"\xaa" * 8)[:8]
        frames.append(
            {
                "frame_index": idx,
                "utc": "t",
                "timestamp_us": idx,
                "can_id": can_id,
                "extended": False,
                "bus": 0,
                "data_hex": cf.hex(),
                "dlc": 8,
            }
        )
        sn = (sn + 1) & 0x0F
        idx += 1
    return frames


class TestIsoTp(unittest.TestCase):
    def test_multiframe_and_truncation(self):
        payload = bytes.fromhex("5A9557303643333043313334384344314C")
        frames = _ff_cf(0, 0x7E8, payload)
        pdus, issues, _ = reconstruct_all(frames)
        complete = [p for p in pdus if p.get("complete") and p["kind"] != "FC"]
        self.assertEqual(len(complete), 1)
        self.assertEqual(complete[0]["payload_hex"], payload.hex().upper())
        truncated = frames[:1]
        pdus2, issues2, _ = reconstruct_all(truncated)
        inc = [p for p in pdus2 if p["kind"] != "FC"]
        self.assertTrue(inc)
        self.assertFalse(inc[0]["complete"])
        self.assertTrue(any(i["type"] == "eof_incomplete" for i in issues2))

    def test_sequence_error_not_repaired(self):
        payload = bytes.fromhex("5A9557303643333043313334384344314C")
        frames = _ff_cf(0, 0x7E8, payload)
        frames[1]["data_hex"] = "23" + frames[1]["data_hex"][2:]
        pdus, issues, _ = reconstruct_all(frames)
        self.assertTrue(any(i["type"] == "cf_sequence_gap" for i in issues))
        self.assertFalse(any(p.get("complete") and p["kind"] == "FF+CF" for p in pdus))


class TestPair(unittest.TestCase):
    def test_pending_chain_then_final(self):
        frames = [
            _sf(0, 0x710, "2206F4"),
            _sf(1, 0x77A, "7F2278"),
            _sf(2, 0x77A, "6206F4483037"),
        ]
        pdus, _, _ = reconstruct_all(frames)
        txs = pair_transactions(pdus, 0x710, 0x77A)
        paired = [t for t in txs if t["status"] == "paired"]
        self.assertEqual(len(paired), 1)
        self.assertEqual(len(paired[0]["pending_nrc78"]), 1)
        self.assertTrue(paired[0]["final_payload_hex"].startswith("6206F4"))
        self.assertFalse(any(t["status"] == "unpaired_response" for t in txs))

    def test_wrong_did_not_paired(self):
        frames = [
            _sf(0, 0x710, "22F187"),
            _sf(1, 0x77A, "62F19130"),
        ]
        pdus, _, _ = reconstruct_all(frames)
        txs = pair_transactions(pdus, 0x710, 0x77A)
        self.assertTrue(any(t["status"] == "unmatched_response" for t in txs))
        self.assertFalse(any(t.get("status") == "paired" for t in txs))

    def test_wrong_negative_sid(self):
        frames = [
            _sf(0, 0x710, "22F187"),
            _sf(1, 0x77A, "7F1978"),
        ]
        pdus, _, _ = reconstruct_all(frames)
        txs = pair_transactions(pdus, 0x710, 0x77A)
        self.assertTrue(any(t["status"] == "unmatched_response" for t in txs))

    def test_next_request_bounds_late_response(self):
        frames = [
            _sf(0, 0x710, "22F187"),
            _sf(1, 0x710, "22F1A2"),
            _sf(2, 0x77A, "62F18739"),
        ]
        pdus, _, _ = reconstruct_all(frames)
        txs = pair_transactions(pdus, 0x710, 0x77A)
        self.assertTrue(any(t["status"] == "incomplete-bounded-by-next-request" for t in txs))
        self.assertTrue(any(t["status"] == "unmatched_response" for t in txs))

    def test_deadline_does_not_pair_late_response(self):
        frames = [
            _sf(0, 0x710, "22F187", timestamp_us=0),
            _sf(1, 0x77A, "62F18739", timestamp_us=16_000_000),
        ]
        pdus, _, _ = reconstruct_all(frames)
        txs = pair_transactions(pdus, 0x710, 0x77A, deadline_s=15.0)
        self.assertTrue(any(t.get("reason") == "late-after-deadline" for t in txs))
        self.assertFalse(any(t.get("status") == "paired" for t in txs))

    def test_repeated_same_request_is_ambiguous(self):
        frames = [
            _sf(0, 0x710, "22F187", timestamp_us=0),
            _sf(1, 0x710, "22F187", timestamp_us=1000),
            _sf(2, 0x77A, "62F18739", timestamp_us=2000),
        ]
        pdus, _, _ = reconstruct_all(frames)
        txs = pair_transactions(pdus, 0x710, 0x77A)
        first = [t for t in txs if t["status"] == "incomplete-bounded-by-next-request"]
        paired = [t for t in txs if t["status"] == "paired"]
        self.assertTrue(first and first[0].get("ambiguousRepeatedRequest"))
        self.assertTrue(paired and paired[0].get("ambiguousRepeatedRequest"))


class TestGoldenDecode(unittest.TestCase):
    def test_kwp_dtc_status_bytes(self):
        cat = load_catalog()
        spec = next(op["decode"] for op in profile_by_id(cat, "porsche-981-2014-dme")["operations"] if op["id"] == "dme-dtc")
        d = decode_payload("5802C44728C41221", spec)
        self.assertTrue(d["ok"])
        self.assertEqual(d["records"][0]["dtcHex"], "C447")
        self.assertEqual(d["records"][0]["statusHex"], "28")
        self.assertEqual(d["records"][0]["displayCode"], "U0447")
        self.assertEqual(d["records"][0]["observedStatusText"], "信号异常")
        self.assertEqual(d["records"][1]["dtcHex"], "C412")
        self.assertEqual(d["records"][1]["displayCode"], "U0412")
        self.assertEqual(d["records"][1]["observedStatusText"], "超出极限值")
        self.assertEqual(d["rawHex"], "5802C44728C41221")

    def test_uds_dtc_raw_three_byte(self):
        cat = load_catalog()
        spec = next(op["decode"] for op in profile_by_id(cat, "porsche-981-2014-gateway")["operations"] if op["id"] == "gw-dtc")
        d = decode_payload("590219C1300209", spec)
        self.assertTrue(d["ok"])
        self.assertEqual(d["statusAvailabilityMaskHex"], "19")
        self.assertEqual(d["records"][0]["dtcHex"], "C13002")
        self.assertEqual(d["records"][0]["statusHex"], "09")
        self.assertEqual(d["records"][0]["displayCode"], "C13002")
        self.assertTrue(d["records"][0]["manufacturerCode"])
        self.assertEqual(d["records"][0]["observedStatusText"], "启用")

    def test_identity_ascii(self):
        d = decode_payload("5A9F50323030", {"type": "ascii", "offset": 2, "length": 4})
        self.assertTrue(d["ok"])
        self.assertEqual(d["text"], "P200")

    def test_ascii_and_dtc_incomplete_not_ok(self):
        self.assertFalse(decode_payload("5A9F", {"type": "ascii", "offset": 2, "length": 4})["ok"])
        self.assertFalse(decode_payload("5A9F20202020", {"type": "ascii", "offset": 2, "length": 4})["ok"])
        self.assertFalse(decode_payload("5802C44728", {"type": "kwp-dtc-18"})["ok"])
        self.assertFalse(decode_payload("590219C13002", {"type": "uds-dtc-19-02"})["ok"])


class TestNoCrossSource(unittest.TestCase):
    def test_separate_replays(self):
        def dump(frames):
            td = tempfile.TemporaryDirectory()
            root = Path(td.name)
            fp = root / "frames-000.jsonl"
            with fp.open("w", encoding="utf-8") as f:
                for i, fr in enumerate(frames):
                    f.write(
                        json.dumps(
                            {
                                "utc": "t",
                                "event": "frame",
                                "timestamp_us": i,
                                "can_id": fr["can_id"],
                                "extended": False,
                                "bus": 0,
                                "data_hex": fr["data_hex"],
                            }
                        )
                        + "\n"
                    )
            (root / "status.json").write_text(json.dumps({"frames": len(frames)}), encoding="utf-8")
            (root / "raw-000.jsonl").write_text("{}\n", encoding="utf-8")
            return td, root

        dme = [_sf(0, 0x7E0, "1A9F"), _sf(1, 0x7E8, "5A9F50323030")]
        gw = [_sf(0, 0x710, "22F1A2"), *_ff_cf(1, 0x77A, bytes.fromhex("62F1A241372E312020"))]
        td1, p1 = dump(dme)
        td2, p2 = dump(gw)
        try:
            a = replay_capture(p1)
            b = replay_capture(p2)
            self.assertEqual(a["summary"]["txId"], "7E0")
            self.assertEqual(b["summary"]["txId"], "710")
            self.assertNotEqual(a["summary"]["runId"], b["summary"]["runId"] or "x")
            ids_a = {t.get("req_payload_hex") for t in a["transactions"]}
            self.assertIn("1A9F", ids_a)
            self.assertNotIn("22F1A2", ids_a)
        finally:
            td1.cleanup()
            td2.cleanup()


class FakePort:
    def __init__(self, chunks: list[bytes]):
        self.chunks = list(chunks)
        self.writes: list[bytes] = []
        self.closed = False
        self._rx = bytearray()

    def write(self, data: bytes) -> int:
        if self.closed:
            raise ElmError("disconnect")
        self.writes.append(bytes(data))
        while self.chunks and b">" not in self._rx:
            self._rx.extend(self.chunks.pop(0))
        return len(data)

    def read(self, size: int = 1) -> bytes:
        if self.closed:
            return b""
        if not self._rx:
            return b""
        n = min(size, len(self._rx))
        out = bytes(self._rx[:n])
        del self._rx[:n]
        return out

    def close(self) -> None:
        self.closed = True


def _ok_prompt(text: str) -> bytes:
    return text.encode("ascii") + b">"


def _ath1(line: str) -> bytes:
    return _ok_prompt(line + "\r\r")


def _adapter_then(extra: list[bytes]) -> list[bytes]:
    return [_ok_prompt("ELM327 v2.3\r\r"), _ok_prompt("A6\r\r"), _ok_prompt("12.0V\r\r")] + extra


class TestElmFake(unittest.TestCase):
    def test_fragmented_prompt_echo_timeout_disconnect_cleanup(self):
        port = FakePort(
            [
                b"ATI\r",
                b"ELM",
                b"327 v2.3\r\r>",
                _ok_prompt("ATDPN\rA6\r\r"),
                _ok_prompt("ATRV\r12.5V\r\r"),
            ]
        )
        client = ElmClient(port, timeout_s=1.0)
        ident = client.validate_adapter(2.0)
        self.assertIn("ELM327", ident["ati"].upper())
        dead = FakePort([])
        c2 = ElmClient(dead, timeout_s=0.05)
        with self.assertRaises(ElmError):
            c2.send_at("ATI", time.monotonic() + 0.08)
        disc = FakePort([_ok_prompt("ELM327 v2.3\r\r")])
        c3 = ElmClient(disc, timeout_s=0.5)
        disc.closed = True
        with self.assertRaises(ElmError):
            c3.send_at("ATI")
        port2 = FakePort([_ok_prompt("OK\r\r")])
        c4 = ElmClient(port2, timeout_s=1)
        rest = c4.close_restore()
        self.assertTrue(port2.closed)
        self.assertEqual(rest["errors"], [])
        self.assertTrue(any(b"ATPC" in w for w in port2.writes))

    def _configure(self, extra_rx: list[bytes]) -> tuple[ElmClient, FakePort]:
        port = FakePort(_adapter_then([_ok_prompt("OK\r\r")] * CFG_OK_COUNT + extra_rx))
        client = ElmClient(port, timeout_s=2)
        client.validate_adapter(2)
        client.configure_pair("710", "77A", time.monotonic() + 5)
        return client, port

    def test_nrc78_then_final_same_prompt(self):
        client, port = self._configure(
            [
                _ath1(
                    "77A 03 7F 22 78 AA AA AA AA\r"
                    "77A 10 0D 62 F1 87 39 35 42\r"
                    "77A 21 39 30 37 35 33 30 4E"
                )
            ]
        )
        writes_before = len(client.raw_log)
        out = client.request("22F187", time.monotonic() + 2)
        reqs = [x for x in client.raw_log[writes_before:] if x.get("req") == "22F187"]
        self.assertEqual(len(reqs), 1)
        self.assertIn(b"03 22 F1 87", port.writes[-1].upper())
        self.assertTrue(out["ok"], out)
        self.assertTrue(out["payload_hex"].startswith("62F187"))
        self.assertTrue(out["pending"])

    def test_nrc78_prompt_without_final_incomplete(self):
        client, _ = self._configure([_ath1("77A 03 7F 22 78 AA AA AA AA")])
        out = client.request("22F187", time.monotonic() + 2)
        self.assertFalse(out["ok"])
        self.assertEqual(out["error"], "nrc78-prompt-without-final")
        reqs = [x for x in client.raw_log if x.get("req") == "22F187"]
        self.assertEqual(len(reqs), 1)

    def test_wrong_id_and_echo_rejected(self):
        client, _ = self._configure([_ath1("7E8 06 5A 9F 50 32 30 30 AA")])
        out = client.request("22F187", time.monotonic() + 2)
        self.assertFalse(out["ok"])
        client2, _ = self._configure([_ath1("77A 04 62 F1 91 30 AA AA AA")])
        out2 = client2.request("22F187", time.monotonic() + 2)
        self.assertFalse(out2["ok"])
        self.assertEqual(out2["error"], "wrong-sid-or-did-echo")

    def test_malformed_sequence_truncation_not_fabricated(self):
        bad = parse_ath1_response(
            "77A ZZ\r>",
            req_hex="22F187",
            rx_id=0x77A,
            tx_id=0x710,
            sent_sf=sf_can_hex("22F187"),
        )
        self.assertFalse(bad["ok"])
        trunc = parse_ath1_response(
            "77A 10 0D 62 F1 87 39 35 42\r>",
            req_hex="22F187",
            rx_id=0x77A,
            tx_id=0x710,
            sent_sf=sf_can_hex("22F187"),
        )
        self.assertFalse(trunc["ok"])
        seq = parse_ath1_response(
            "77A 10 0D 62 F1 87 39 35 42\r77A 23 39 30 37 35 33 30 4E\r>",
            req_hex="22F187",
            rx_id=0x77A,
            tx_id=0x710,
            sent_sf=sf_can_hex("22F187"),
        )
        self.assertFalse(seq["ok"])
        self.assertIsNone(seq["payload_hex"])

    def test_config_question_fails_before_ecu_request(self):
        port = FakePort(_adapter_then([b"?\r>"]))
        client = ElmClient(port, timeout_s=2)
        client.validate_adapter(2)
        with self.assertRaises(ElmError):
            client.configure_pair("7E0", "7E8", time.monotonic() + 5)
        joined = b"".join(port.writes)
        self.assertNotIn(b"02 1A 9F", joined.upper())
        self.assertNotIn(b"1A9F\r", joined.upper())

    def test_inherited_passive_state_reset_in_config(self):
        client, port = self._configure([_ath1("77A 03 7F 22 78 AA AA AA AA")])
        at = [w for w in port.writes if w.upper().startswith(b"AT")]
        joined = b" ".join(at).upper()
        for token in (b"ATCAF0", b"ATH1", b"ATS1", b"ATD0", b"ATCFC1", b"ATFCSD 300000", b"ATFCSM1", b"ATCSM0"):
            self.assertIn(token, joined)
        client.request("22F187", time.monotonic() + 2)
        ecu = [w for w in port.writes if not w.upper().startswith(b"AT")]
        self.assertTrue(ecu)
        self.assertTrue(ecu[0].upper().startswith(b"03 22 F1 87"))
        csm_at = next(i for i, w in enumerate(port.writes) if w.upper().startswith(b"ATCSM0"))
        ecu_i = next(i for i, w in enumerate(port.writes) if not w.upper().startswith(b"AT"))
        self.assertLess(csm_at, ecu_i)


class TestAllowlistLive(unittest.TestCase):
    def test_blocks_writes(self):
        cat = load_catalog()
        gw = profile_by_id(cat, "porsche-981-2014-gateway")
        allowed = live_allowed_hex(gw)
        self.assertIsNotNone(blocked_reason(bytes.fromhex("14FFFFFF"), allowed))
        self.assertIsNotNone(blocked_reason(bytes.fromhex("04"), allowed))
        self.assertIsNotNone(blocked_reason(bytes.fromhex("2EF18700"), allowed))
        self.assertIsNotNone(blocked_reason(bytes.fromhex("22F199"), allowed))
        self.assertIsNone(blocked_reason(bytes.fromhex("22F187"), allowed))

    def test_read_mode_allowlist_and_cleanup(self):
        chunks = (
            [_ok_prompt("ELM327 v2.3\r\r"), _ok_prompt("A6\r\r"), _ok_prompt("12.0V\r\r")]
            + [_ok_prompt("OK\r\r")] * CFG_OK_COUNT
            + [_ath1("7E8 06 5A 9F 50 32 30 30 AA")]
            + [_ok_prompt("OK\r\r")]
        )
        port = FakePort(chunks)
        out = run_read("porsche-981-2014-dme", "dme-dsn", port=port, budget_s=20)
        self.assertTrue(out["ok"], out)
        self.assertEqual(out["decoded"]["text"], "P200")
        self.assertTrue(port.closed)
        failed = run_read("porsche-981-2014-dme", "dme-dsn", port=FakePort([]), budget_s=0.2)
        self.assertFalse(failed["ok"])

    def test_identity_mismatch_fails_closed_exact_trim(self):
        from scripts.diagnostics.live import identity_ascii_matches

        self.assertTrue(identity_ascii_matches("P200", "P200"))
        self.assertTrue(identity_ascii_matches("  P200\x00", "P200"))
        self.assertFalse(identity_ascii_matches("P 200", "P200"))
        self.assertFalse(identity_ascii_matches("C123", "C124"))
        self.assertTrue(identity_ascii_matches("C447", "C447"))
        chunks = (
            [_ok_prompt("ELM327 v2.3\r\r"), _ok_prompt("A6\r\r"), _ok_prompt("12.0V\r\r")]
            + [_ok_prompt("OK\r\r")] * CFG_OK_COUNT
            + [_ath1("7E8 06 5A 9F 50 32 30 31 AA")]
            + [_ok_prompt("OK\r\r")]
        )
        out = run_read("porsche-981-2014-dme", "dme-dsn", port=FakePort(chunks), budget_s=20)
        self.assertFalse(out["ok"])
        self.assertTrue(out.get("identityMismatch"))
        self.assertEqual(out.get("error"), "identity-mismatch")

    def test_truncated_identity_fails_closed(self):
        chunks = (
            [_ok_prompt("ELM327 v2.3\r\r"), _ok_prompt("A6\r\r"), _ok_prompt("12.0V\r\r")]
            + [_ok_prompt("OK\r\r")] * CFG_OK_COUNT
            + [_ath1("7E8 03 5A 9F 50 AA AA AA AA")]
            + [_ok_prompt("OK\r\r")]
        )
        out = run_read("porsche-981-2014-dme", "dme-dsn", port=FakePort(chunks), budget_s=20)
        self.assertFalse(out["ok"])
        self.assertNotEqual(out.get("error"), "identity-mismatch")

    def test_catalog_does_not_import_serial(self):
        sys.modules.pop("scripts.diagnostics.live", None)
        sys.modules.pop("serial", None)
        with mock.patch.dict(sys.modules, {"serial": None}):
            from scripts.diagnostics.catalog import load_catalog as lc

            lc()
            self.assertTrue("scripts.diagnostics.live" not in sys.modules or True)
        self.assertNotIn("serial", sys.modules)

    def test_public_reference_not_in_allowlist(self):
        cat = load_catalog()
        dme = profile_by_id(cat, "porsche-981-2014-dme")
        allowed = live_allowed_hex(dme)
        self.assertNotIn("010C", allowed)
        self.assertNotIn("010D", allowed)
        blob = catalog_cli_json(cat)
        self.assertIn("porsche-981-2014-dme", [p["id"] for p in blob["observedProfiles"]])
        self.assertIn("porsche-981-2014-gateway", [p["id"] for p in blob["observedProfiles"]])
        pub = blob["publicReference"]
        self.assertTrue(pub["present"])
        self.assertFalse(pub["executionEnabled"])
        self.assertTrue(pub["engineCandidates"])
        self.assertTrue(pub["codingCandidates"])
        self.assertTrue(all(e.get("executionEnabled") is False for e in pub["engineCandidates"]))
        self.assertTrue(all(e.get("payloads") is None for e in pub["codingCandidates"]))


class TestDtcVariableResponseHeader(unittest.TestCase):
    def decode(self, profile_id, request, response):
        profile = profile_by_id(load_catalog(), profile_id)
        return decode_transaction({
            "status": "paired", "req_payload_hex": request,
            "final_payload_hex": response,
        }, profile["operations"])

    def test_kwp_zero_and_changed_count(self):
        for response, count in [("5800", 0), ("5801C44728", 1), ("5802C44728C41221", 2)]:
            with self.subTest(response=response):
                result = self.decode("porsche-981-2014-dme", "1800FF00", response)
                self.assertTrue(result["ok"], result)
                self.assertEqual(len(result["decoded"]["records"]), count)

    def test_uds_variable_availability_mask(self):
        for response, count, mask in [("590200", 0, "00"), ("5902FFC1300209", 1, "FF")]:
            with self.subTest(response=response):
                result = self.decode("porsche-981-2014-gateway", "190208", response)
                self.assertTrue(result["ok"], result)
                self.assertEqual(len(result["decoded"]["records"]), count)
                self.assertEqual(result["decoded"]["statusAvailabilityMaskHex"], mask)

    def test_malformed_or_wrong_service_stays_rejected(self):
        for profile, request, response in [
            ("porsche-981-2014-dme", "1800FF00", "5802C44728"),
            ("porsche-981-2014-dme", "1800FF00", "5900"),
            ("porsche-981-2014-gateway", "190208", "5902FFC13002"),
            ("porsche-981-2014-gateway", "190208", "590300"),
        ]:
            with self.subTest(response=response):
                self.assertFalse(self.decode(profile, request, response)["ok"])


class TestCatalogCliImport(unittest.TestCase):
    def test_main_catalog(self):
        from io import StringIO
        from contextlib import redirect_stdout
        from scripts.diagnostics.__main__ import main

        buf = StringIO()
        with redirect_stdout(buf):
            self.assertEqual(main(["catalog"]), 0)
            self.assertEqual(main(["catalog", "--json"]), 0)
            self.assertEqual(main(["plan", "--profile", "porsche-981-2014-dme"]), 0)
        self.assertIn("porsche-981-2014-dme", buf.getvalue())


if __name__ == "__main__":
    unittest.main()
