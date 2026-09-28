from __future__ import annotations

from collections import defaultdict


def pci_kind(b0: int) -> str:
    n = b0 >> 4
    return {0: "SF", 1: "FF", 2: "CF", 3: "FC"}.get(n, f"UNK{n}")


def partition_key(frame: dict) -> tuple:
    return (frame["bus"], bool(frame["extended"]), frame["dlc"], frame["can_id"])


def reconstruct_partition(frames: list[dict]) -> tuple[list[dict], list[dict]]:
    """Normal-address ISO-TP. Gaps stay partial; no splice repair."""
    pdus: list[dict] = []
    issues: list[dict] = []
    pending = None

    def flush_incomplete(reason: str, at_idx: int) -> None:
        nonlocal pending
        if pending is None:
            return
        pending["complete"] = False
        pending["issues"] = pending.get("issues", []) + [reason]
        pending["end_frame_index"] = at_idx
        pending["payload_hex"] = bytes(pending["payload"]).hex().upper()
        pending["payload_len"] = len(pending["payload"])
        pdus.append(pending)
        issues.append(
            {
                "type": reason,
                "can_id": pending["can_id"],
                "start_frame_index": pending["start_frame_index"],
                "declared_len": pending["declared_len"],
                "got": len(pending["payload"]),
            }
        )
        pending = None

    for fr in frames:
        idx = fr["frame_index"]
        try:
            data = bytes.fromhex(fr["data_hex"])
        except ValueError:
            issues.append({"type": "invalid_hex", "frame_index": idx})
            flush_incomplete("invalid_hex_interrupt", idx)
            continue
        if not 1 <= len(data) <= 8 or fr["dlc"] != len(data):
            issues.append({"type": "invalid_dlc", "frame_index": idx})
            flush_incomplete("invalid_dlc_interrupt", idx)
            continue
        kind = pci_kind(data[0])
        if kind == "FC":
            if len(data) < 3:
                issues.append({"type": "fc_short_header", "frame_index": idx})
                continue
            pdus.append(
                {
                    "kind": "FC",
                    "complete": True,
                    "can_id": fr["can_id"],
                    "can_id_hex": f"{fr['can_id']:03X}",
                    "bus": fr["bus"],
                    "extended": fr["extended"],
                    "dlc": fr["dlc"],
                    "utc": fr["utc"],
                    "timestamp_us": fr.get("timestamp_us"),
                    "start_frame_index": idx,
                    "end_frame_index": idx,
                    "frame_indices": [idx],
                    "pci": {
                        "fs": data[0] & 0x0F,
                        "bs": data[1] if len(data) > 1 else None,
                        "stmin": data[2] if len(data) > 2 else None,
                    },
                    "payload_hex": "",
                    "payload_len": 0,
                    "declared_len": 0,
                }
            )
            continue
        if kind == "SF":
            flush_incomplete("interrupted_by_sf", idx)
            ln = data[0] & 0x0F
            if ln == 0 or ln > len(data) - 1:
                issues.append(
                    {
                        "type": "sf_bad_length",
                        "frame_index": idx,
                        "declared": ln,
                        "avail": len(data) - 1,
                    }
                )
                flush_incomplete("interrupted_by_bad_sf", idx)
                continue
            payload = data[1 : 1 + ln]
            pdus.append(
                {
                    "kind": "SF",
                    "complete": True,
                    "can_id": fr["can_id"],
                    "can_id_hex": f"{fr['can_id']:03X}",
                    "bus": fr["bus"],
                    "extended": fr["extended"],
                    "dlc": fr["dlc"],
                    "utc": fr["utc"],
                    "timestamp_us": fr.get("timestamp_us"),
                    "start_frame_index": idx,
                    "end_frame_index": idx,
                    "frame_indices": [idx],
                    "payload_hex": payload.hex().upper(),
                    "payload_len": len(payload),
                    "declared_len": ln,
                    "padding_hex": data[1 + ln :].hex().upper(),
                }
            )
            continue
        if kind == "FF":
            flush_incomplete("new_ff_while_incomplete", idx)
            if len(data) < 2:
                issues.append({"type": "ff_short_header", "frame_index": idx})
                continue
            declared = ((data[0] & 0x0F) << 8) | data[1]
            if declared < 8:
                issues.append({"type": "ff_declared_too_small", "frame_index": idx, "declared": declared})
                continue
            pending = {
                "kind": "FF+CF",
                "complete": False,
                "can_id": fr["can_id"],
                "can_id_hex": f"{fr['can_id']:03X}",
                "bus": fr["bus"],
                "extended": fr["extended"],
                "dlc": fr["dlc"],
                "utc": fr["utc"],
                "timestamp_us": fr.get("timestamp_us"),
                "start_frame_index": idx,
                "end_frame_index": idx,
                "frame_indices": [idx],
                "payload": bytearray(data[2:]),
                "declared_len": declared,
                "next_sn": 1,
                "issues": [],
            }
            continue
        if kind == "CF":
            if len(data) < 2:
                issues.append({"type": "cf_empty_data", "frame_index": idx})
                flush_incomplete("cf_empty_data", idx)
                continue
            sn = data[0] & 0x0F
            chunk = data[1:]
            if pending is None:
                issues.append({"type": "orphan_cf", "frame_index": idx, "sn": sn, "can_id": fr["can_id"]})
                continue
            if sn != pending["next_sn"]:
                issues.append(
                    {
                        "type": "cf_sequence_gap",
                        "frame_index": idx,
                        "expected_sn": pending["next_sn"],
                        "got_sn": sn,
                        "can_id": fr["can_id"],
                    }
                )
                pending["issues"].append(f"seq_gap expected {pending['next_sn']} got {sn}")
                flush_incomplete("cf_sequence_gap", idx)
                continue
            pending["payload"].extend(chunk)
            pending["frame_indices"].append(idx)
            pending["end_frame_index"] = idx
            pending["next_sn"] = (sn + 1) & 0x0F
            if len(pending["payload"]) >= pending["declared_len"]:
                raw = bytes(pending["payload"])
                payload = raw[: pending["declared_len"]]
                extra = raw[pending["declared_len"] :]
                pending["payload_hex"] = payload.hex().upper()
                pending["payload_len"] = len(payload)
                pending["padding_hex"] = extra.hex().upper()
                pending["complete"] = True
                pending.pop("payload", None)
                pending.pop("next_sn", None)
                pdus.append(pending)
                pending = None
            continue
        issues.append({"type": "unknown_pci", "frame_index": idx, "b0": data[0]})
        flush_incomplete("unknown_pci_interrupt", idx)

    if pending is not None:
        flush_incomplete("eof_incomplete", frames[-1]["frame_index"] if frames else -1)
    for p in pdus:
        p.pop("payload", None)
        p.pop("next_sn", None)
    return pdus, issues


def reconstruct_all(frames: list[dict]) -> tuple[list[dict], list[dict], list[dict]]:
    groups: dict[tuple, list] = defaultdict(list)
    for fr in frames:
        groups[partition_key(fr)].append(fr)
    pdus: list[dict] = []
    issues: list[dict] = []
    partitions = []
    for key, group in sorted(groups.items(), key=lambda kv: (kv[0][3], kv[1][0]["frame_index"])):
        bus, ext, dlc, can_id = key
        partitions.append(
            {
                "bus": bus,
                "extended": ext,
                "dlc": dlc,
                "can_id_hex": f"{can_id:03X}",
                "n": len(group),
            }
        )
        p, iss = reconstruct_partition(group)
        pdus.extend(p)
        issues.extend(iss)
    pdus.sort(key=lambda p: (p["start_frame_index"], p["can_id"]))
    return pdus, issues, partitions
