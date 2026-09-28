from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path

from .catalog import load_catalog
from .decode import decode_transaction, redact_vin
from .hashutil import sha256_file
from .isotp import reconstruct_all
from .pair import pair_transactions

def load_frames(path: Path) -> list[dict]:
    frames = []
    with path.open(encoding="utf-8") as f:
        for i, line in enumerate(f):
            line = line.strip()
            if not line:
                continue
            o = json.loads(line)
            if o.get("event") != "frame":
                continue
            hx = o["data_hex"]
            frames.append(
                {
                    "frame_index": i,
                    "utc": o["utc"],
                    "timestamp_us": o.get("timestamp_us"),
                    "can_id": o["can_id"],
                    "extended": bool(o["extended"]),
                    "bus": o["bus"],
                    "data_hex": hx,
                    "dlc": len(hx) // 2,
                }
            )
    return frames


def parse_atma_line(line: str) -> tuple[int, bytes] | None:
    tokens = line.strip().split()
    if not tokens or not re.fullmatch(r"[0-9A-Fa-f]{3}", tokens[0]):
        return None
    ident = int(tokens[0], 16)
    if ident > 0x7FF or len(tokens) > 9:
        return None
    if any(not re.fullmatch(r"[0-9A-Fa-f]{2}", t) for t in tokens[1:]):
        return None
    return ident, bytes(int(t, 16) for t in tokens[1:])


def reconstruct_raw_serial_frames(raw_path: Path) -> list[tuple[int, str]]:
    """Best-effort ATMA line rebuild from fragmented serial_rx hex. Not a repair of frames-000."""
    events = []
    with raw_path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                events.append(json.loads(line))
    started = False
    buf = bytearray()
    out: list[tuple[int, str]] = []
    for ev in events:
        if ev.get("event") == "serial_tx":
            hx = (ev.get("hex") or "").upper()
            if hx.startswith("41544D41"):  # ATMA
                started = True
            continue
        if not started or ev.get("event") != "serial_rx":
            continue
        buf.extend(bytes.fromhex(ev["hex"]))
    text = buf.decode("latin-1", errors="replace")
    for raw_line in text.split("\r"):
        parsed = parse_atma_line(raw_line)
        if parsed:
            ident, data = parsed
            out.append((ident, data.hex().upper()))
    return out


def _pick_profile(catalog: dict, tx_id: int, rx_id: int) -> dict | None:
    for p in catalog["profiles"]:
        if int(p["txId"], 16) == tx_id and int(p["rxId"], 16) == rx_id:
            return p
    return None


def _ids_from_frames(frames: list[dict]) -> tuple[int, int]:
    counts: dict[int, int] = {}
    for fr in frames:
        counts[fr["can_id"]] = counts.get(fr["can_id"], 0) + 1
    ids = sorted(counts, key=lambda i: (-counts[i], i))
    if len(ids) < 2:
        raise ValueError("need two CAN IDs")
    a, b = ids[0], ids[1]
    # tester usually lower physical request on 7E0 vs 7E8; 710 vs 77A
    req, resp = (a, b) if a < b else (b, a)
    return req, resp


def replay_capture(capture_dir: Path, catalog: dict | None = None) -> dict:
    capture_dir = capture_dir.resolve()
    frames_path = capture_dir / "frames-000.jsonl"
    raw_path = capture_dir / "raw-000.jsonl"
    status_path = capture_dir / "status.json"
    catalog = catalog or load_catalog()
    frames = load_frames(frames_path)
    status = json.loads(status_path.read_text(encoding="utf-8")) if status_path.exists() else {}
    status_frames = status.get("frames")
    hashes = {
        "frames-000.jsonl": sha256_file(frames_path),
        "raw-000.jsonl": sha256_file(raw_path) if raw_path.exists() else None,
        "status.json": sha256_file(status_path) if status_path.exists() else None,
    }
    serial_frames = reconstruct_raw_serial_frames(raw_path) if raw_path.exists() else []
    frames_pairs = [(fr["can_id"], fr["data_hex"].upper()) for fr in frames]
    serial_match = serial_frames == frames_pairs if serial_frames else False
    pdus, issues, partitions = reconstruct_all(frames)
    req_id, resp_id = _ids_from_frames(frames)
    profile = _pick_profile(catalog, req_id, resp_id)
    txs = pair_transactions(pdus, req_id, resp_id)
    decoded = []
    ops = (profile or {}).get("operations") or []
    for tx in txs:
        d = decode_transaction(tx, ops)
        if d:
            decoded.append(d)
    unpaired_pos = [
        t
        for t in txs
        if t.get("status") in ("unpaired_response", "unmatched_response")
        and (t.get("resp_payload_hex") or "").startswith("62")
    ]
    pending_assembled = sum(1 for t in txs if t.get("status") == "paired" and t.get("pending_nrc78"))
    summary = {
        "captureDir": str(capture_dir),
        "runId": capture_dir.name,
        "frameCount": len(frames),
        "statusFrames": status_frames,
        "statusCountMatch": status_frames == len(frames),
        "hashes": hashes,
        "serialReconstructCount": len(serial_frames),
        "serialMatchesFrames": serial_match,
        "partitions": partitions,
        "pduComplete": sum(1 for p in pdus if p.get("kind") != "FC" and p.get("complete")),
        "pduIncomplete": sum(1 for p in pdus if p.get("kind") != "FC" and not p.get("complete")),
        "fcCount": sum(1 for p in pdus if p.get("kind") == "FC"),
        "issues": issues,
        "profileId": (profile or {}).get("id"),
        "txId": f"{req_id:03X}",
        "rxId": f"{resp_id:03X}",
        "transactionCounts": _tx_counts(txs),
        "pendingChainsAssembled": pending_assembled,
        "orphan62": len(unpaired_pos),
        "decoded": decoded,
        "notIndependentlyVerifiedVlinker": True,
        "x431ReplacementComplete": False,
    }
    return {
        "summary": summary,
        "pdus": _public_pdus(pdus),
        "transactions": txs,
    }


def _tx_counts(txs: list[dict]) -> dict:
    counts: dict[str, int] = {}
    for t in txs:
        counts[t["status"]] = counts.get(t["status"], 0) + 1
    return counts


def _public_pdus(pdus: list[dict]) -> list[dict]:
    out = []
    for p in pdus:
        row = dict(p)
        hx = row.get("payload_hex") or ""
        # drop full VIN ascii from tracked-looking dumps; still keep hex for local replay
        if len(hx) >= 36:
            try:
                raw = bytes.fromhex(hx)
                for i in range(len(raw) - 16):
                    s = raw[i : i + 17].decode("ascii", errors="ignore")
                    if len(s) == 17 and s.isalnum():
                        row["payload_hex_redacted_vin"] = redact_vin(s)
            except ValueError:
                pass
        out.append(row)
    return out


def write_replay(result: dict, out_dir: Path | None = None) -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S") + f"-{os.getpid()}"
    dest = out_dir or (Path(__file__).resolve().parents[2] / ".local" / "diagnostics" / "replay" / stamp)
    dest.mkdir(parents=True, exist_ok=True)
    (dest / "summary.json").write_text(
        json.dumps(result["summary"], indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    _jsonl(dest / "pdus.jsonl", result["pdus"])
    _jsonl(dest / "transactions.jsonl", result["transactions"])
    (dest / "decoded.json").write_text(
        json.dumps(result["summary"]["decoded"], indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return dest


def _jsonl(path: Path, rows: list[dict]) -> None:
    with path.open("w", encoding="utf-8", newline="\n") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
