"""Audit existing 981 field files only. Never enumerates or opens hardware."""
from collections import Counter
import hashlib
import json
from pathlib import Path
import argparse

from .can_monitor import replay
from .elm import parse_ath1_response
from .x431_capture import envelope

ROOT = Path(__file__).resolve().parents[2]
RUNS = ("981-20261001T083428Z", "981-standard-20261001T084558Z",
        "981-standard-retry-20261001T090641Z", "981-idle-20261001T091559Z")


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def audit(output):
    output.mkdir(parents=True, exist_ok=True)
    vlinker = []
    for name in RUNS:
        for manifest in (ROOT / ".local/vehicle-runs" / name).glob("*/manifest.json"):
            result = json.loads(manifest.read_text(encoding="utf-8"))
            if result.get("mode") != "live":
                continue
            raw_path = manifest.parent / "raw.json"
            raw = json.loads(raw_path.read_text(encoding="utf-8")) if raw_path.exists() else []
            transactions = []
            current, tx, rx = None, None, None
            def finish():
                if current and current.get("req"):
                    text = bytes.fromhex(current["rxHex"]).decode("ascii", errors="replace")
                    decoded = parse_ath1_response(text, req_hex=current["req"], rx_id=current["rxId"],
                        tx_id=current["txId"], sent_sf=current["sf"].replace(" ", ""))
                    transactions.append({"requestHex": current["req"], "promptComplete": ">" in text,
                        "positive": decoded["ok"], "error": decoded.get("error"),
                        "negativeComplete": decoded.get("error") == "negative-response",
                        "rxBytes": len(bytes.fromhex(current["rxHex"]))})
            for row in raw:
                if row["dir"] == "tx":
                    finish()
                    cmd = row.get("cmd", "")
                    if cmd.startswith("ATSH "):
                        tx = int(cmd.split()[1], 16)
                    if cmd.startswith("ATCRA "):
                        rx = int(cmd.split()[1], 16)
                    current = {**row, "rxHex": "", "txId": tx, "rxId": rx}
                elif row["dir"] == "rx" and current is not None:
                    current["rxHex"] += row["hex"]
            finish()
            vlinker.append({"manifest": str(manifest.relative_to(ROOT)), "manifestSha256": sha(manifest),
                "rawSha256": sha(raw_path) if raw_path.exists() else None, "ok": result["ok"], "error": result.get("error"),
                "requestCount": len(transactions), "positiveResponses": sum(t["positive"] for t in transactions),
                "completeNegatives": sum(t["negativeComplete"] for t in transactions),
                "missingPrompts": sum(not t["promptComplete"] for t in transactions),
                "unresolvedResponses": [t for t in transactions if not t["positive"] and not t["negativeComplete"]],
                "restoration": result.get("restoration"), "rfLostPacketRate": None})
    mxplus = []
    for manifest in (ROOT / ".local/mxplus-drive-can").glob("*/status.json"):
        state = json.loads(manifest.read_text(encoding="utf-8"))
        if state.get("simulation"):
            continue
        try:
            verified = replay(manifest.parent)
            integrity, integrity_error = verified["integrityVerified"], None
        except (ValueError, OSError, KeyError) as exc:
            integrity, integrity_error = False, str(exc)
        mxplus.append({"session": manifest.parent.name, "manifestSha256": sha(manifest),
            "opened": state["opened_port"], "error": state["error"], "frames": state["frame_count"],
            "duration": state.get("monitor_elapsed_s"), "notices": state["adapter_notices"],
            "partialBytes": state["trailing_partial_bytes"], "captureQualityOk": state["ok"],
            "integrityVerified": integrity, "integrityError": integrity_error, "rfLostPacketRate": None})
    analysis = ROOT / ".local/vehicle-analysis/981-x431-20261001"
    transport = json.loads((analysis / "transport-audit.json").read_text(encoding="utf-8"))
    frames = [json.loads(l) for l in (analysis / "app-frames.jsonl").read_text(encoding="utf-8").splitlines()]
    outgoing = paired = invalid = 0
    for i, f in enumerate(frames):
        direction = f["stream"][1]
        invalid += not envelope(bytes.fromhex(f["frameHex"]), bytes.fromhex("f0f8" if direction == "host_to_controller" else "f8f0"))
        if direction != "host_to_controller":
            continue
        outgoing += 1
        r = frames[i+1] if i+1 < len(frames) else None
        paired += bool(r and r["stream"][1] == "controller_to_host" and r["stream"][0] == f["stream"][0]
                       and r["stream"][2] == f["stream"][2] and r["sequenceByte"] == f["sequenceByte"]
                       and r["commandByte"] == (f["commandByte"] | 0x40))
    x431 = {"records": transport["records"], "sourceSha256": transport["sha256"],
        "reportedHostCaptureDropsMax": transport["reportedDropsMax"], "applicationFrames": len(frames),
        "applicationChecksumFailures": invalid, "outgoingApplicationFrames": outgoing, "adjacentResponsePairs": paired,
        "unpairedOutgoingApplicationFrames": outgoing - paired, "containerIssues": transport["container_issues"],
        "l2capRejects": transport["l2cap_rejects"], "rfcommParserExclusions": transport["rfcommRejectKinds"],
        "rfLostPacketRate": None, "limits": "HCI capture drops and envelope checks do not measure baseband retries or RF loss"}
    summary = {"vlinker": vlinker, "mxplus": mxplus, "x431": x431,
        "vlinkerTotalRequests": sum(r["requestCount"] for r in vlinker),
        "vlinkerOpenFailures121": sum("121" in (r["error"] or "") and not r["requestCount"] for r in vlinker),
        "mxplusOpenFailures121": sum("121" in (r["error"] or "") and not r["opened"] for r in mxplus),
        "noNewHardwareAccess": True, "usbOnCarReliability": {"VNCI": "not-verified", "PT3G": "not-verified"},
        "officialErrorReference": "https://www.scantool.net/scantool/downloads/682/obdlink_frpm_f.pdf"}
    (output / "transport-stability.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"vlinkerRequests": summary["vlinkerTotalRequests"], "vlinkerOpenFailures121": summary["vlinkerOpenFailures121"],
        "mxplusOpenFailures121": summary["mxplusOpenFailures121"], "mxplusIntegrity": Counter(str(r["integrityVerified"]) for r in mxplus),
        "x431": x431}, ensure_ascii=True))
    return summary


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    audit(parser.parse_args().output_dir)
