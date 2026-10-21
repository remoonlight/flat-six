"""Synthetic source records test the codec; exact original/native checks are private."""
import hashlib
import struct
import unittest
from unittest.mock import patch

from scripts.diagnostics import piwis_psm_roller as source


def constraint_fixture(excluded):
    def interval(low, high):
        return b"\x01\xfb\x00\x0b" + struct.pack("<I", low) + b"\x0b" + struct.pack("<I", high) + b"\x11"
    data = b"\x03\x02" + interval(0, 255) + b"\x01\x01\x02" + struct.pack("<I", len(excluded))
    for low, high in excluded:
        data += b"\x01\x02\x02" + interval(low, high) + b"\x03" + struct.pack("<4I", 1, 2, 3, 4) + b"\x00"
    return data + b"\x00#>\x00"


class PiwisPsmRollerTest(unittest.TestCase):
    def setUp(self):
        # Non-original string IDs make these explicit synthetic records.
        self.records = {role: constraint_fixture(ranges) for role, ranges in {
            "mode": [(2, 253)], "start": [(0, 0), (5, 6), (8, 255)],
            "stop": [(0, 0), (5, 255)], "result": [(5, 6), (8, 255)],
        }.items()}
        self.pins = {role: hashlib.sha256(data).hexdigest() for role, data in self.records.items()}
        self.patch = patch.dict(source._CONSTRAINT_SOURCES, self.pins)
        self.patch.start()
        self.addCleanup(self.patch.stop)
        self.codec = source.PsmRollerSourceCodec(self.records)

    def test_requests_have_exact_source_extent_and_legal_modes(self):
        for mode in (0, 1, 254, 255):
            self.assertEqual(self.codec.encode_request("start", mode=mode), bytes.fromhex("3101000a") + bytes((mode,)))
        self.assertEqual(self.codec.encode_request("stop"), bytes.fromhex("3102000a"))
        self.assertEqual(self.codec.encode_request("result"), bytes.fromhex("3103000a"))
        for mode in (*range(2, 254), -1, 256, True, False, 0.0, "0", None):
            with self.subTest(mode=mode), self.assertRaises(ValueError):
                self.codec.encode_request("start", mode=mode)
        for action in ("stop", "result"):
            with self.subTest(action=action), self.assertRaisesRegex(ValueError, "not-applicable"):
                self.codec.encode_request(action, mode=0)

    def test_each_reply_status_is_checked_against_its_own_constraint(self):
        legal = {"start": {1, 2, 3, 4, 7}, "stop": {1, 2, 3, 4}, "result": {0, 1, 2, 3, 4, 7}}
        for action, control in (("start", 1), ("stop", 2), ("result", 3)):
            for status in range(256):
                payload = bytes((0x71, control, 0, 10, status))
                if status in legal[action]:
                    reply = self.codec.decode_positive_reply(action, payload)
                    self.assertEqual((reply.action, reply.status_code), (action, status))
                    self.assertGreater(reply.physical_label_id, 0)
                    self.assertFalse(hasattr(reply, "success"))
                else:
                    with self.subTest(action=action, status=status), self.assertRaisesRegex(ValueError, "unqualified"):
                        self.codec.decode_positive_reply(action, payload)
        self.assertNotEqual(self.codec.decode_positive_reply("start", bytes.fromhex("7101000a01")).physical_label_id,
                            self.codec.decode_positive_reply("stop", bytes.fromhex("7102000a01")).physical_label_id)

    def test_only_exact_mode_labels_can_be_converted_back(self):
        for code in (0, 1, 254, 255):
            label_id = self.codec.mode_label_id(code)
            self.assertEqual(self.codec.mode_code_for_label_id(label_id), code)
        status_label = self.codec.decode_positive_reply("start", bytes.fromhex("7101000a01")).physical_label_id
        for label in (0, -1, status_label, 0xffffffff, True, False, None, "1797771462", 1797771462.0):
            with self.subTest(label=label), self.assertRaisesRegex(ValueError, "label-invalid"):
                self.codec.mode_code_for_label_id(label)

    def test_mismatch_cannot_be_reinterpreted_as_a_positive_result(self):
        valid = bytes.fromhex("7101000a01")
        for offset in range(4):
            for replacement in range(256):
                if replacement == valid[offset]:
                    continue
                payload = valid[:offset] + bytes((replacement,)) + valid[offset+1:]
                with self.subTest(offset=offset, replacement=replacement), self.assertRaisesRegex(ValueError, "mismatch"):
                    self.codec.decode_positive_reply("start", payload)
        for payload in (bytes.fromhex("7f3178"), bytes.fromhex("7f3122"), b"", valid[:4], valid + b"\x00", valid + b"\x71" * 64, bytearray(valid), None):
            with self.subTest(payload_type=type(payload)), self.assertRaises(ValueError):
                self.codec.decode_positive_reply("start", payload)

    def test_unknown_operations_do_not_default_to_start_or_result(self):
        for action in (None, "", "START", "clear", "1", 1, [], {}):
            with self.subTest(action=action), self.assertRaises(ValueError):
                self.codec.encode_request(action, mode=0)
            with self.subTest(reply_action=action), self.assertRaises(ValueError):
                self.codec.decode_positive_reply(action, bytes.fromhex("7101000a01"))

    def test_missing_swapped_or_changed_source_records_rejected(self):
        for records in (None, {}, {**self.records, "extra": b"x"}, {k: v for k, v in self.records.items() if k != "stop"},
                        {**self.records, "mode": self.records["start"]}, {**self.records, "start": self.records["start"] + b"\x00"}):
            with self.subTest(record_type=type(records)), self.assertRaises(ValueError):
                source.PsmRollerSourceCodec(records)

    def test_even_fingerprinted_incompatible_constraints_rejected(self):
        other = constraint_fixture([(2, 254)])
        with patch.dict(source._CONSTRAINT_SOURCES, {"mode": hashlib.sha256(other).hexdigest()}):
            with self.assertRaisesRegex(ValueError, "mapping-mismatch"):
                source.PsmRollerSourceCodec({**self.records, "mode": other})


if __name__ == "__main__":
    unittest.main()
