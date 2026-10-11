"""Installed drivers and present USB metadata only; never open an adapter or ADB."""
from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def collect_host_devices(*, platform=None, query=None, program_data=None, vendor_root=None, env=None):
    from .connection import _ps_json

    plat = platform or sys.platform
    if plat != "win32":
        return {"usb": [], "services": [], "drivers": {}, "errors": []}
    from .vnci import vendor_root as vnci_path, verify_vendor_files as verify_vnci, DRIVER_VERSION
    from .pt3g import vendor_root as pt3g_path, verify_vendor_files as verify_pt3g
    from .x431_reference import verify_windows_tools

    environment = dict(os.environ if env is None else env)
    if program_data is not None:
        environment['ProgramData'] = str(program_data)
    vnci_root = Path(vendor_root) if vendor_root is not None else vnci_path(environment)
    pt_root = pt3g_path(environment)

    def driver(root, required, verify, version):
        installed = all((root / name).is_file() for name in required)
        verified = False;error = None
        try:
            verify(root)
            verified = True
        except (OSError, ValueError, RuntimeError) as failure:
            error = str(failure) or type(failure).__name__
        return {'installed': installed, 'version': version if verified else None,
                'expectedVersion': version, 'supportFilesQualified': verified, 'supportError': error,
                'versionSource': 'pinned-support-file-hashes' if verified else None,
                'driverLoaded': False, 'firmwareQualified': False, 'vehicleQualified': False}

    adb = environment.get('PORSCHE981_ADB') or shutil.which('adb', path=environment.get('PATH', ''))
    adb_tools = None;adb_error = 'adb-runtime-missing'
    if adb:
        try:
            adb_tools = verify_windows_tools(adb)
            adb_error = None
        except (OSError, ValueError, RuntimeError) as failure:
            adb_error = str(failure) or type(failure).__name__
    drivers = {
        "VNCI": driver(vnci_root, ('PDUAPI_VW.dll',), verify_vnci, DRIVER_VERSION),
        "PT3G": driver(pt_root, ('../E70_PT3G_SERVICE.exe', 'PDU_VCI.dll', 'pdu2.dll'),
                       verify_pt3g, "E70/PORSCHE-VCI"),
        "X431-tablet": {"installed": bool(adb and Path(adb).is_file()),
                        "version": adb_tools['version'] if adb_tools else None,
                        "supportFilesQualified": bool(adb_tools), "supportError": adb_error,
                        "versionSource": 'pinned-support-file-hashes' if adb_tools else None,
                        "runtimeExecuted": False, "serverStarted": False,
                        "firmwareQualified": False, "vehicleQualified": False,
                        "directProjectHeadTransportImplemented": False},
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
