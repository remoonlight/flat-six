"""Read the original closed UINT32 constraint records used by the PSM job.

This is an offline source reader. It supplies no ECU command, recipe, transport
or execution qualification. Unsupported value/range formats fail explicitly.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import re
import struct


@dataclass(frozen=True)
class ClosedUint32Interval:
    lower: int
    upper: int

    def contains(self, value: int) -> bool:
        return type(value) is int and self.lower <= value <= self.upper


@dataclass(frozen=True)
class ExcludedUint32Range:
    interval: ClosedUint32Interval
    # IDs remain in their original string-pool namespaces, without guessed text.
    description_id: int
    description_key_id: int
    short_label_id: int
    short_label_key_id: int


@dataclass(frozen=True)
class Uint32Constraint:
    domain: ClosedUint32Interval
    excluded: tuple[ExcludedUint32Range, ...]
    source_sha256: str

    def accepts(self, value: int) -> bool:
        return self.domain.contains(value) and not any(
            item.interval.contains(value) for item in self.excluded
        )


class _Record:
    def __init__(self, data: bytes):
        self.data = data
        self.offset = 0

    def take(self, size: int) -> bytes:
        end = self.offset + size
        if end > len(self.data):
            raise ValueError("piwis-constraint-truncated")
        result = self.data[self.offset:end]
        self.offset = end
        return result

    def expect(self, value: bytes) -> None:
        if self.take(len(value)) != value:
            raise ValueError("piwis-constraint-format-unsupported")

    def uint32(self) -> int:
        return struct.unpack("<I", self.take(4))[0]

    def interval(self) -> ClosedUint32Interval:
        self.expect(b"\x01\xfb\x00")  # present MCDIntervalImpl, class 251
        self.expect(b"\x0b")  # serialized UINT32 MCDValue
        lower = self.uint32()
        self.expect(b"\x0b")
        upper = self.uint32()
        # Both limits are closed in the source records. Open/infinite or
        # different numeric types require their own original-code checks.
        self.expect(b"\x11")
        if lower > upper:
            raise ValueError("piwis-constraint-interval-invalid")
        return ClosedUint32Interval(lower, upper)


def read_uint32_constraint(data: bytes, *, expected_sha256: str) -> Uint32Constraint:
    """Read one complete, fingerprinted class-515 record, including its end.

    The original loader maps range byte 3 to MCDRangeInfo 0x603. The native
    valid-interval computation subtracts these ranges from the main interval.
    Other range meanings and computed constraints remain unsupported here.
    """
    if type(data) is not bytes or not 1 <= len(data) <= 16384:
        raise ValueError("piwis-constraint-data-invalid")
    if (not isinstance(expected_sha256, str)
            or re.fullmatch(r"[0-9a-f]{64}", expected_sha256) is None
            or hashlib.sha256(data).hexdigest() != expected_sha256):
        raise ValueError("piwis-constraint-source-mismatch")
    record = _Record(data)
    record.expect(b"\x03\x02")  # MCDConstraintImpl, class 515
    domain = record.interval()
    record.expect(b"\x01\x01\x02")  # present MCDScaleConstraintsImpl, class 513
    count = record.uint32()
    if count > 256:
        raise ValueError("piwis-constraint-range-count-invalid")
    excluded = []
    for _ in range(count):
        record.expect(b"\x01\x02\x02")  # MCDScaleConstraintImpl, class 514
        interval = record.interval()
        record.expect(b"\x03")  # not-valid range, not a legal-value list
        ids = tuple(record.uint32() for _ in range(4))
        record.expect(b"\x00")  # static scale constraint
        excluded.append(ExcludedUint32Range(interval, *ids))
    record.expect(b"\x00")  # static main constraint
    record.expect(b"#>\x00")  # complete source object stream
    if record.offset != len(data):
        raise ValueError("piwis-constraint-trailing-data")
    return Uint32Constraint(domain, tuple(excluded), expected_sha256)
