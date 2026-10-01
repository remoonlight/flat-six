"""Generate a synthetic replay using an injected port; no hardware or local references."""
from pathlib import Path
import sys
from unittest.mock import patch

from scripts.diagnostics.can_monitor import capture, replay
from scripts.diagnostics.tests.test_can_monitor import Clock, Port
from scripts.diagnostics.tests.test_connection import MX, MX_BT, MX_SERIAL, _snap


def create(directory: Path):
    clock = Clock()
    stream = b"".join(f"{ident:03X} 00 01 02 03 04 05 06 07\r".encode()
                      for ident in (0x081, 0x086, 0x102, 0x103, 0x104, 0x105))
    port = Port(clock, stream=[stream[:11], stream[11:] + b"CAN ERROR\rCAN ERROR\r"])
    with patch("scripts.diagnostics.can_monitor.archive_references", return_value=[]), \
         patch("scripts.diagnostics.can_monitor.open_selected_port", side_effect=AssertionError("hardware forbidden")):
        state = capture(MX, directory, port=port, clock=clock, duration_s=.12,
                        snapshot=_snap(serial=[MX_SERIAL], bluetooth=[MX_BT]))
        result = replay(directory)
    assert state["simulation"] and port.closed and state["frame_count"] == 6
    assert result["integrityVerified"] and not result["captureQualityOk"]
    assert state["adapter_notices"]["CAN ERROR"] == 2


if __name__ == "__main__":
    create(Path(sys.argv[1]))
