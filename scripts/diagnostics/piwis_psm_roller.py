"""Offline application-message codec for the retained PSM roller definitions.

Original field positions, types, table key and text mappings are source-bound.
There is no transport or recipe registration here. This does not establish
vehicle/software applicability, safe-stop rules or a complete guided job.
"""
from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType

from .piwis_constraints import read_uint32_constraint


SOURCE_LIBRARY_SHA256 = "917bf402b50652476d5e30318ae918bc2bde0e35d0b03eeb52c3d1ac207cde0b"
SOURCE_DATABASE_SHA256 = "f7ad150df00ca4223f489725377665b254b6543527b6dc8d1199632a58f22d11"

# Exact original constraint records, joined to the four fully loaded PSM DOPs.
_CONSTRAINT_SOURCES = {
    "mode": "daf1be7770107752eab23c124667ee7d55dbbeb56708308713dff6a527f6aa42",
    "start": "78f3a94845416b0b752e42a037836594cc7b046ab737d77eb68127f2d09cce68",
    "stop": "3d89f6cc872aac0f4fb88ca56ac5a46abf42a97d13b4b2e1f6ab3bf2c5ef76e6",
    "result": "5ea526ff9edc0d8dcba5b6f9b732e582cb5dafb2d0107677020e9395997b4ed1",
}
_CONTROL = {"start": 1, "stop": 2, "result": 3}
_LABEL_IDS = {
    "mode": {0: 1797771462, 1: 1422472009, 254: 745630811, 255: 1061433240},
    "start": {1: 1131688387, 2: 1213685914, 3: 14363739, 4: 282816688, 7: 1183684872},
    "stop": {1: 1213037931, 2: 892426959, 3: 834127547, 4: 1062744333},
    "result": {0: 498310132, 1: 1507306295, 2: 1066780979, 3: 1012096539, 4: 282816688, 7: 1183684872},
}


@dataclass(frozen=True)
class RollerSourceReply:
    action: str
    status_code: int
    physical_label_id: int


class PsmRollerSourceCodec:
    def __init__(self, constraint_records: dict[str, bytes]):
        if type(constraint_records) is not dict or set(constraint_records) != set(_CONSTRAINT_SOURCES):
            raise ValueError("psm-roller-constraint-records-required")
        constraints = {}
        for role, digest in _CONSTRAINT_SOURCES.items():
            constraint = read_uint32_constraint(constraint_records[role], expected_sha256=digest)
            if (constraint.domain.lower, constraint.domain.upper) != (0, 255) or {
                n for n in range(256) if constraint.accepts(n)
            } != set(_LABEL_IDS[role]):
                raise ValueError("psm-roller-constraint-mapping-mismatch")
            constraints[role] = constraint
        self._constraints = MappingProxyType(constraints)

    def mode_label_id(self, value: int) -> int:
        """Return the original text-pool ID; no inferred vehicle applicability."""
        if not self._constraints["mode"].accepts(value):
            raise ValueError("psm-roller-mode-invalid")
        return _LABEL_IDS["mode"][value]

    def mode_code_for_label_id(self, label_id: int) -> int:
        """Invert an exact original mode option, without prefix/text guessing."""
        if type(label_id) is int:
            for code, source_label in _LABEL_IDS["mode"].items():
                if source_label == label_id and self._constraints["mode"].accepts(code):
                    return code
        raise ValueError("psm-roller-mode-label-invalid")

    def encode_request(self, action: str, *, mode: int | None = None) -> bytes:
        """Encode the source application PDU, without opening a vehicle session."""
        if not isinstance(action, str) or action not in _CONTROL:
            raise ValueError("psm-roller-action-invalid")
        if action == "start":
            self.mode_label_id(mode)
        elif mode is not None:
            raise ValueError("psm-roller-mode-not-applicable")
        # The table's decimal key "10" is UINT32 with a 16-bit high/low field.
        # Start adds its one-byte mode; stop/result have no request structure.
        prefix = bytes((0x31, _CONTROL[action])) + (10).to_bytes(2, "big")
        return prefix + bytes((mode,)) if action == "start" else prefix

    def decode_positive_reply(self, action: str, payload: bytes) -> RollerSourceReply:
        """Require the outstanding action and a complete source positive PDU.

        A positive PDU alone does not mean the maintenance job succeeded. Return
        its original status and text-pool ID for the still-unqualified workflow.
        Negative/pending, truncated, extended and unknown replies are rejected.
        """
        if not isinstance(action, str) or action not in _CONTROL:
            raise ValueError("psm-roller-action-invalid")
        if type(payload) is not bytes or len(payload) != 5:
            raise ValueError("psm-roller-positive-response-length")
        if payload[:4] != bytes((0x71, _CONTROL[action], 0, 10)):
            raise ValueError("psm-roller-positive-response-mismatch")
        status = payload[4]
        if not self._constraints[action].accepts(status):
            raise ValueError("psm-roller-status-unqualified")
        return RollerSourceReply(action, status, _LABEL_IDS[action][status])
