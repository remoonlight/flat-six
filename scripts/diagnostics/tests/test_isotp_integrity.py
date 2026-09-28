"""Regression cases from independent review: damaged traffic must stay incomplete."""
import unittest

from scripts.diagnostics.elm import parse_ath1_response
from scripts.diagnostics.isotp import reconstruct_all


def frame(index, data_hex, dlc=None):
    return dict(frame_index=index, utc="t", timestamp_us=index, can_id=0x7E8,
                bus=0, extended=False, dlc=dlc if dlc is not None else len(bytes.fromhex(data_hex)),
                data_hex=data_hex)


class TestIsoTpIntegrity(unittest.TestCase):
    def test_short_first_frame_is_reported_without_crashing(self):
        pdus, issues, _ = reconstruct_all([frame(0, "10")])
        self.assertFalse(pdus)
        self.assertEqual(issues[0]["type"], "ff_short_header")

    def test_single_frame_interrupts_prior_multiframe_message(self):
        pdus, issues, _ = reconstruct_all([
            frame(0, "100A62F187010203"),
            frame(1, "035A9F00AAAAAAAA"),
            frame(2, "2104050607AAAAAA"),
        ])
        self.assertFalse(any(p["complete"] for p in pdus if p["kind"] == "FF+CF"))
        self.assertTrue(any(i["type"] == "interrupted_by_sf" for i in issues))
        self.assertTrue(any(i["type"] == "orphan_cf" for i in issues))

    def test_invalid_first_frame_length_cannot_become_complete(self):
        pdus, issues, _ = reconstruct_all([
            frame(0, "10015A9F00000000"), frame(1, "2100000000000000"),
        ])
        self.assertFalse(any(p["complete"] for p in pdus))
        self.assertTrue(any(i["type"] == "ff_declared_too_small" for i in issues))

    def test_dlc_mismatch_is_rejected(self):
        pdus, issues, _ = reconstruct_all([frame(0, "065A9F50323030AA", dlc=7)])
        self.assertFalse(pdus)
        self.assertEqual(issues[0]["type"], "invalid_dlc")

    def test_live_parser_rejects_malformed_pdu_even_with_valid_reply(self):
        result = parse_ath1_response(
            "7E8 10 01 5A 9F 00 00 00 00\r7E8 06 5A 9F 50 32 30 30 AA\r>",
            req_hex="1A9F", rx_id=0x7E8, tx_id=0x7E0, sent_sf="021A9F",
        )
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"], "isotp-incomplete-or-malformed")


if __name__ == "__main__":
    unittest.main()
