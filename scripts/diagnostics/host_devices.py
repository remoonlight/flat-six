"""Installed drivers and present USB metadata only; never open an adapter or ADB."""
from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def collect_host_devices(*, platform=None, query=None, program_data=None, vendor_root=None):
    from .connection import _ps_json

    plat = platform or sys.platform
    if plat != "win32":
        return {"usb": [], "services": [], "drivers": {}, "errors": []}
    data_root = Path(program_data or os.environ.get("ProgramData", r"C:\ProgramData"))
    vnci_root = Path(vendor_root or ROOT / ".local/vnci-support/vendor/VW_PDUAPI_OS")
    e70 = data_root / "PORSCHE-VCI"
    drivers = {
        "VNCI": {"installed": (vnci_root / "PDUAPI_VW.dll").is_file(), "version": "29.0.0"},
        "PT3G": {"installed": all((e70 / f).is_file() for f in
                                  ("E70_PT3G_SERVICE.exe", "X64/PDU_VCI.dll", "X64/pdu2.dll")),
                  "version": "E70/PORSCHE-VCI"},
        "X431-tablet": {"installed": bool(shutil.which("adb")), "version": "ADB"},
    }
    script = (
        "[Console]::OutputEncoding=[System.Text.Encoding]::UTF8; "
        "$usb=@(Get-PnpDevice -PresentOnly -ErrorAction Stop | "
        "Where-Object {$_.InstanceId -like 'USB\\*'} | "
        "Select-Object Status,FriendlyName,InstanceId); "
        "$services=@(Get-Service -Name VciToolServerPORSCHE -ErrorAction SilentlyContinue | "
        "Select-Object Name,@{n='State';e={[string]$_.Status}}); "
        "[pscustomobject]@{usb=$usb;services=$services}|ConvertTo-Json -Depth 4 -Compress"
    )
    rows, error = (query or _ps_json)(script)
    doc = rows[0] if isinstance(rows, list) and rows and isinstance(rows[0], dict) else {}
    return {"usb": doc.get("usb") or [], "services": doc.get("services") or [],
            "drivers": drivers, "errors": [error] if error else []}
