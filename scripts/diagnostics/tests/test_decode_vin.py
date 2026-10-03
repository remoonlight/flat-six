"""VIN wire validation before storing or displaying a vehicle identity."""
import unittest
from scripts.diagnostics.decode import decode_payload


class VinDecodeTests(unittest.TestCase):
    def test_valid_vin_is_redacted_on_both_named_ecu_routes(self):
        for prefix, offset in (("62F190", 3), ("5A90", 2)):
            result = decode_payload(prefix + b"WP0ZZZ98ZES123456".hex(),
                                    {"type": "vin-ascii", "offset": offset, "length": 17})
            self.assertTrue(result["ok"])
            self.assertEqual(result["text"], "WP0***********456")
            self.assertIsNone(result["hex"])

    def test_invalid_wire_vin_does_not_become_an_identity(self):
        for wire in (b"WP0ZZZ98ZES12345I", b"WP0ZZZ98ZES12345O", b"WP0ZZZ98ZES12345Q",
                     b"WP0ZZZ98ZES12345 ", b"WP0ZZZ98ZES12345\x00", b"WP0ZZZ98ZES12345\xff",
                     b"wp0zzz98zes123456"):
            result = decode_payload("62F190" + wire.hex(),
                                    {"type": "vin-ascii", "offset": 3, "length": 17})
            self.assertFalse(result["ok"], wire)
            self.assertEqual(result["reason"], "invalid-vin")
            self.assertNotIn("text", result)

    def test_truncated_vin_still_reports_length_failure(self):
        result = decode_payload("62F190" + b"WP0ZZZ98ZES12345".hex(),
                                {"type": "vin-ascii", "offset": 3, "length": 17})
        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "truncated-ascii")


if __name__ == "__main__":
    unittest.main()
