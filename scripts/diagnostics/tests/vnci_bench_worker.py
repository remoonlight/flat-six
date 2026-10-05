"""Real VNCI USB acceptance with adapter-only native API guard; no CAN link."""
import json
import os
from pathlib import Path
import sys
import time

sys.path.insert(0, os.environ["PORSCHE981_BENCH_CODE_ROOT"])
from scripts.diagnostics import vnci

ALLOWED = {"PDUConstruct", "PDUDestruct", "PDUGetModuleIds", "PDUDestroyItem",
           "PDUModuleConnect", "PDUModuleDisconnect", "PDUGetObjectId", "PDUIoCtl"}


def main():
    directory = Path(sys.argv[1]).resolve()
    if ".local" not in directory.parts or not directory.is_dir():
        raise SystemExit("private scratch required")
    audit = directory / f"native-{os.getpid()}.jsonl"
    original = vnci.NativeDpu.call

    def guarded(self, name, *args):
        if name not in ALLOWED:
            raise AssertionError("bench-forbidden-api:" + name)
        if name == "PDUGetObjectId" and args[1] != b"PDU_IOCTL_READ_VBATT":
            raise AssertionError("bench-forbidden-object")
        if name == "PDUIoCtl" and (args[1] != vnci.UNDEF or args[3] is not None):
            raise AssertionError("bench-forbidden-ioctl")
        started = time.monotonic_ns()
        item = {"api": name, "atNs": time.time_ns()}
        with audit.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps({**item, "phase": "begin"}) + "\n")
        try:
            result = original(self, name, *args)
            item["ok"] = True
            return result
        except Exception as exc:
            item.update(ok=False, error=str(exc))
            raise
        finally:
            with audit.open("a", encoding="utf-8") as stream:
                stream.write(json.dumps({**item, "phase": "end", "durationNs": time.monotonic_ns() - started}) + "\n")

    vnci.NativeDpu.call = guarded
    if "--discover" in sys.argv:
        print(json.dumps({"devices": vnci.discover(), "errors": []}))
        return 0
    request = json.loads(sys.stdin.readline())
    if (request.get("action") != "monitor" or request.get("purpose") != "diagnostic"
            or not str(request.get("deviceId", "")).startswith("vnci:")):
        raise SystemExit("bench-request-not-permitted")
    return vnci.run_monitor(request["deviceId"], stdout=sys.stdout, stdin=sys.stdin)


if __name__ == "__main__":
    raise SystemExit(main())
