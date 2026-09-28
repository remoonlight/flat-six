"""Catalog ISO-TP provenance. F197 decode is independent of private captures."""
from __future__ import annotations

import os
import unittest
from pathlib import Path

from scripts.diagnostics.catalog import load_catalog, profile_by_id
from scripts.diagnostics.decode import decode_payload
from scripts.diagnostics.isotp import pci_kind, reconstruct_all
from scripts.diagnostics.replay import load_frames, replay_capture

REPO = Path(__file__).resolve().parents[3]
ENV_RUNS = "PORSCHE981_SAVVYCAN_RUNS"
DME_RUN = "20260926-233624-327076"
GW_RUN = "20260926-234115-903227"


def capture_runs_dir() -> Path:
    override = os.environ.get(ENV_RUNS)
    if override:
        return Path(override)
    return REPO / ".local" / "savvycan-vlinker" / "2026-09-26" / "runs"


def captures_available() -> bool:
    runs = capture_runs_dir()
    return all((runs / rid / "frames-000.jsonl").is_file() for rid in (DME_RUN, GW_RUN))


def _skip_captures_reason() -> str:
    return f"private captures absent under {capture_runs_dir()} (set {ENV_RUNS} to override)"


def _pdu_indices(tx: dict) -> list[int]:
    used = list(tx.get("req_frames") or [])
    final = tx.get("final_payload_hex")
    for r in tx.get("responses") or []:
        if r.get("payload_hex") == final:
            used.extend(r.get("frames") or [])
    return used


class TestGwSystemF197Decode(unittest.TestCase):
    def test_f197_length_13_matches_payload_length_14_rejected(self):
        cat = load_catalog()
        op = next(
            o
            for o in profile_by_id(cat, "porsche-981-2014-gateway")["operations"]
            if o["id"] == "gw-system-f197"
        )
        self.assertEqual(op["decode"]["length"], 13)
        payload = "62F1977A656E74722047617465776179"
        self.assertTrue(decode_payload(payload, op["decode"])["ok"])
        bad = {**op["decode"], "length": 14}
        self.assertFalse(decode_payload(payload, bad)["ok"])


@unittest.skipUnless(captures_available(), _skip_captures_reason())
class TestCatalogIsotpProvenance(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cat = load_catalog()
        cls.runs = capture_runs_dir()
        cls.replays = {
            DME_RUN: replay_capture(cls.runs / DME_RUN, cls.cat),
            GW_RUN: replay_capture(cls.runs / GW_RUN, cls.cat),
        }

    def test_cited_frames_are_complete_pdu_without_flowcontrol(self):
        for profile in self.cat["profiles"]:
            for op in profile["operations"]:
                run = op["provenance"]["runId"]
                frames = {fr["frame_index"]: fr for fr in load_frames(self.runs / run / "frames-000.jsonl")}
                txs = [
                    t
                    for t in self.replays[run]["transactions"]
                    if (t.get("req_payload_hex") or "").upper() == op["requestHex"].upper()
                    and t.get("status") == "paired"
                ]
                self.assertTrue(txs, op["id"])
                tx = txs[0]
                cited = op["provenance"]["frameIndices"]
                self.assertEqual(cited, _pdu_indices(tx), op["id"])
                for idx in cited:
                    data = bytes.fromhex(frames[idx]["data_hex"])
                    self.assertNotEqual(pci_kind(data[0]), "FC", (op["id"], idx))

    def test_truncated_multiframe_stays_incomplete(self):
        frames = load_frames(self.runs / DME_RUN / "frames-000.jsonl")
        software = [fr for fr in frames if fr["frame_index"] in (56, 57)]
        pdus, issues, _ = reconstruct_all(software)
        self.assertTrue(any(not p.get("complete") for p in pdus if p.get("kind") == "FF+CF") or issues)
        self.assertFalse(
            any(p.get("complete") and p.get("payload_hex", "").startswith("5A95") and p["kind"] == "FF+CF" for p in pdus)
        )


if __name__ == "__main__":
    unittest.main()
