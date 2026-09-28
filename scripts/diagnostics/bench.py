"""Offline ELM/ISO-TP rehearsal. Virtual BytePort only; never opens serial."""
from __future__ import annotations

import argparse
import json
import os
import tempfile
from pathlib import Path

from .allowlist import BLOCKED_SIDS, blocked_reason
from .catalog import live_allowed_hex, load_catalog, operation, profile_by_id
from .decode import decode_payload, redact_vin
from .elm import ElmClient, ElmError
from .hashutil import sha256_file
from .isotp import pci_kind
from .live import run_read
from .replay import load_frames, replay_capture

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CAPTURE_ROOT = REPO_ROOT / ".local" / "savvycan-vlinker" / "2026-09-26" / "runs"
DME_RUN = "20260926-233624-327076"
GW_RUN = "20260926-234115-903227"
SCHEMA_VERSION = 1
AUTHENTIC_NRC78_REQ = "2206F4"


class FakeClock:
    def __init__(self) -> None:
        self.t = 0.0

    def time(self) -> float:
        return self.t

    def sleep(self, dt: float) -> None:
        self.t += float(dt)


class VirtualElmPort:
    """ELM AT + one SF request -> one ATH1 prompt. No serial."""

    def __init__(
        self,
        *,
        ecu_map: dict[str, bytes] | None = None,
        atpc_body: bytes = b"OK\r\r>",
        close_error: Exception | None = None,
        disconnect: bool = False,
        hang_unknown: bool = False,
        identity: str = "ELM327 v2.3",
    ):
        self.ecu_map = {k.replace(" ", "").upper(): v for k, v in (ecu_map or {}).items()}
        self.atpc_body = atpc_body
        self.close_error = close_error
        self.disconnect = disconnect
        self.hang_unknown = hang_unknown
        self.identity = identity
        self.writes: list[bytes] = []
        self.ecu_payloads: list[str] = []
        self.closed = False
        self._rx = bytearray()

    def write(self, data: bytes) -> int:
        if self.closed or self.disconnect:
            raise ConnectionError("disconnect")
        self.writes.append(bytes(data))
        text = data.decode("ascii", errors="replace").strip()
        up = text.upper()
        if up.startswith("AT"):
            if up == "ATI":
                self._rx.extend(f"{self.identity}\r\r>".encode("ascii"))
            elif up == "ATDPN":
                self._rx.extend(b"A6\r\r>")
            elif up == "ATRV":
                self._rx.extend(b"12.0V\r\r>")
            elif up == "ATPC":
                self._rx.extend(self.atpc_body)
            else:
                self._rx.extend(b"OK\r\r>")
            return len(data)
        req = _sf_payload(up)
        self.ecu_payloads.append(req)
        if req not in self.ecu_map:
            if self.hang_unknown:
                return len(data)
            self._rx.extend(b"NO DATA\r\r>")
            return len(data)
        self._rx.extend(self.ecu_map[req])
        return len(data)

    def read(self, size: int = 1) -> bytes:
        if self.closed or self.disconnect:
            return b""
        if not self._rx:
            return b""
        n = min(size, len(self._rx))
        out = bytes(self._rx[:n])
        del self._rx[:n]
        return out

    def close(self) -> None:
        if self.close_error is not None:
            err = self.close_error
            self.closed = True
            raise err
        self.closed = True


def _sf_payload(spaced: str) -> str:
    toks = spaced.replace("\r", " ").split()
    raw = bytes(int(t, 16) for t in toks)
    n = raw[0] & 0x0F
    return raw[1 : 1 + n].hex().upper()


def ath1_prompt(lines: list[str]) -> bytes:
    body = "\r".join(lines)
    return (body + "\r\r>").encode("ascii")


def frame_ath1_line(fr: dict) -> str:
    data = bytes.fromhex(fr["data_hex"])
    return f"{fr['can_id']:03X} " + " ".join(f"{b:02X}" for b in data)


def captures_present(root: Path) -> bool:
    return all((root / rid / "frames-000.jsonl").is_file() for rid in (DME_RUN, GW_RUN))


def _cited_rx_lines(frames_by_idx: dict[int, dict], cited: list[int], rx_id: int) -> list[str]:
    lines = []
    for idx in cited:
        fr = frames_by_idx[idx]
        if fr["can_id"] != rx_id:
            continue
        lines.append(frame_ath1_line(fr))
    return lines


def load_authentic_bundle(root: Path, catalog: dict) -> dict:
    replays = {rid: replay_capture(root / rid, catalog) for rid in (DME_RUN, GW_RUN)}
    frames = {rid: {fr["frame_index"]: fr for fr in load_frames(root / rid / "frames-000.jsonl")} for rid in (DME_RUN, GW_RUN)}
    hashes = {rid: sha256_file(root / rid / "frames-000.jsonl") for rid in (DME_RUN, GW_RUN)}
    ops = {}
    for profile in catalog["profiles"]:
        rx_id = int(profile["rxId"], 16)
        for op in profile["operations"]:
            run = op["provenance"]["runId"]
            cited = op["provenance"]["frameIndices"]
            lines = _cited_rx_lines(frames[run], cited, rx_id)
            kinds = [pci_kind(bytes.fromhex(frames[run][i]["data_hex"])[0]) for i in cited if frames[run][i]["can_id"] == rx_id]
            ops[op["id"]] = {
                "profileId": profile["id"],
                "op": op,
                "runId": run,
                "framesSha256": hashes[run],
                "provenanceHash": op["provenance"]["framesSha256"],
                "lines": lines,
                "pciKinds": kinds,
                "cited": cited,
            }
    nrc = None
    gw = replays[GW_RUN]
    rx_id = int(profile_by_id(catalog, "porsche-981-2014-gateway")["rxId"], 16)
    for tx in gw["transactions"]:
        if (tx.get("req_payload_hex") or "").upper() != AUTHENTIC_NRC78_REQ:
            continue
        if not tx.get("pending_nrc78") or tx.get("status") != "paired":
            continue
        idxs: list[int] = []
        for rec in tx.get("responses") or []:
            idxs.extend(rec.get("frames") or [])
        nrc = {
            "req": AUTHENTIC_NRC78_REQ,
            "runId": GW_RUN,
            "framesSha256": hashes[GW_RUN],
            "lines": [frame_ath1_line(frames[GW_RUN][i]) for i in idxs if frames[GW_RUN][i]["can_id"] == rx_id],
            "ecuWriteExpected": 1,
        }
        break
    return {"ops": ops, "hashes": hashes, "nrc78": nrc, "replays": replays}


def _expect_identity(op: dict, decoded: dict) -> str | None:
    if op["decode"]["type"] == "vin-ascii":
        text = decoded.get("text") or ""
        if text != redact_vin(text) or "*" not in text or len(text) != 17:
            return f"vin-not-redacted:{text}"
        return None
    exp = op.get("expectedValue")
    if op["decode"]["type"] == "ascii":
        got = (decoded.get("text") or "").rstrip()
        if got.replace(" ", "") != str(exp).replace(" ", ""):
            return f"expected-{exp}-got-{got}"
        return None
    if op["decode"]["type"] == "kwp-dtc-18":
        recs = decoded.get("records") or []
        got = " ".join(f"{r['dtcHex']}{r['statusHex']}" for r in recs)
        if got != exp:
            return f"expected-{exp}-got-{got}"
        return None
    if op["decode"]["type"] == "uds-dtc-19-02":
        recs = decoded.get("records") or []
        if not recs:
            return "no-dtc-records"
        r0 = recs[0]
        if r0.get("dtcHex") != "C13002" or r0.get("statusHex") != "09":
            return f"expected-C13002-09-got-{r0.get('dtcHex')}-{r0.get('statusHex')}"
        return None
    return "unknown-decode"


def _case(cid: str, kind: str, ok: bool, detail: dict) -> dict:
    return {"id": cid, "evidenceKind": kind, "ok": ok, "detail": _redact_obj(detail)}


def _redact_obj(obj):
    if isinstance(obj, str):
        return redact_vin(obj)
    if isinstance(obj, list):
        return [_redact_obj(x) for x in obj]
    if isinstance(obj, dict):
        return {k: _redact_obj(v) for k, v in obj.items()}
    return obj


def _scratch() -> Path:
    p = Path(tempfile.mkdtemp(prefix="diag-bench-"))
    return p


def _run_op(profile_id: str, op_id: str, port: VirtualElmPort, budget_s: float = 20.0) -> dict:
    return run_read(profile_id, op_id, port=port, budget_s=budget_s, out_dir=_scratch())


def _ecu_only(port: VirtualElmPort) -> list[str]:
    return list(port.ecu_payloads)


def synthetic_map_catalog_sf() -> dict[str, bytes]:
    """Public catalog DSN P200 as SF ATH1 — not a capture."""
    return {"1A9F": ath1_prompt(["7E8 06 5A 9F 50 32 30 30 AA"])}


def run_bench(capture_root: Path | None = None) -> dict:
    catalog = load_catalog()
    root = Path(capture_root) if capture_root is not None else DEFAULT_CAPTURE_ROOT
    present = captures_present(root)
    cases: list[dict] = []
    allow_before = {p["id"]: sorted(live_allowed_hex(p)) for p in catalog["profiles"]}

    if present:
        bundle = load_authentic_bundle(root, catalog)
        for op_id, meta in bundle["ops"].items():
            op = meta["op"]
            hash_ok = meta["framesSha256"] == meta["provenanceHash"]
            port = VirtualElmPort(ecu_map={op["requestHex"].upper(): ath1_prompt(meta["lines"])})
            if op_id in ("dme-dtc", "gw-dtc"):
                # Capture replay stays one request; CLI DTC goes through session runner separately.
                clock = FakeClock()
                client = ElmClient(port, timeout_s=2.0, clock=clock.time, sleeper=clock.sleep)
                decoded = None
                err = None
                try:
                    client.validate_adapter(2.0)
                    client.configure_pair(
                        profile_by_id(catalog, meta["profileId"])["txId"],
                        profile_by_id(catalog, meta["profileId"])["rxId"],
                        clock.time() + 5,
                    )
                    resp = client.request(op["requestHex"].upper(), clock.time() + 5)
                    if not resp.get("ok"):
                        err = resp.get("error") or "not-ok"
                    elif not (resp.get("payload_hex") or "").upper().startswith(op["positivePrefixHex"].upper()):
                        err = "positive-prefix-mismatch"
                    else:
                        decoded = decode_payload(resp["payload_hex"], op["decode"])
                        if not decoded.get("ok"):
                            err = decoded.get("reason") or "decode-incomplete"
                except Exception as e:  # noqa: BLE001
                    err = str(e)
                finally:
                    client.close_restore()
                out = {"ok": err is None, "error": err, "decoded": decoded}
            else:
                out = _run_op(meta["profileId"], op_id, port)
            mismatch = None
            if not hash_ok:
                mismatch = "hash-mismatch"
            elif not port.closed:
                mismatch = "port-not-closed"
            elif len(_ecu_only(port)) != 1:
                mismatch = f"ecu-writes-{_ecu_only(port)}"
            elif _ecu_only(port)[0] != op["requestHex"].upper():
                mismatch = "wrong-request-on-wire"
            elif not out.get("ok"):
                mismatch = out.get("error") or "not-ok"
            else:
                mismatch = _expect_identity(op, out.get("decoded") or {})
            cases.append(
                _case(
                    f"capture:{op_id}",
                    "capture_replay",
                    mismatch is None,
                    {
                        "runId": meta["runId"],
                        "framesSha256": meta["framesSha256"],
                        "hashMatchesProvenance": hash_ok,
                        "pciKinds": meta["pciKinds"],
                        "ecuWriteCount": len(_ecu_only(port)),
                        "decoded": _public_decoded(out.get("decoded")),
                        "error": mismatch,
                        "closed": port.closed,
                    },
                )
            )
        nrc = bundle["nrc78"]
        if nrc:
            gw = profile_by_id(catalog, "porsche-981-2014-gateway")
            clock = FakeClock()
            port = VirtualElmPort(ecu_map={nrc["req"]: ath1_prompt(nrc["lines"])})
            client = ElmClient(port, timeout_s=2.0, clock=clock.time, sleeper=clock.sleep)
            err = None
            try:
                client.validate_adapter(2.0)
                client.configure_pair(gw["txId"], gw["rxId"], clock.time() + 5)
                writes_before = len(port.ecu_payloads)
                resp = client.request(nrc["req"], clock.time() + 5)
                if len(port.ecu_payloads) - writes_before != 1:
                    err = f"retransmit:{port.ecu_payloads}"
                elif not resp.get("ok"):
                    err = resp.get("error")
                elif not (resp.get("pending") or []):
                    err = "missing-nrc78-pending"
                elif not (resp.get("payload_hex") or "").upper().startswith("6206F4"):
                    err = "unexpected-final-prefix"
            except Exception as e:  # noqa: BLE001
                err = str(e)
            finally:
                client.close_restore()
            cases.append(
                _case(
                    "capture:nrc78-2206F4",
                    "capture_replay",
                    err is None and port.closed,
                    {
                        "runId": nrc["runId"],
                        "framesSha256": nrc["framesSha256"],
                        "notLiveAllowed": True,
                        "error": err,
                        "closed": port.closed,
                    },
                )
            )
        else:
            cases.append(
                _case(
                    "capture:nrc78-2206F4",
                    "capture_replay",
                    False,
                    {"skipped": False, "error": "nrc78-transcript-missing"},
                )
            )
    else:
        for profile in catalog["profiles"]:
            for op in profile["operations"]:
                cases.append(
                    _case(
                        f"capture:{op['id']}",
                        "capture_replay",
                        True,
                        {
                            "skipped": True,
                            "reason": f"private captures absent under {root}",
                        },
                    )
                )
        cases.append(
            _case(
                "capture:nrc78-2206F4",
                "capture_replay",
                True,
                {"skipped": True, "reason": f"private captures absent under {root}"},
            )
        )

    cases.extend(_synthetic_cases(catalog))

    allow_after = {p["id"]: sorted(live_allowed_hex(p)) for p in catalog["profiles"]}
    cases.append(
        _case(
            "synthetic:allowlist-unchanged",
            "synthetic",
            allow_before == allow_after and all(len(v) for v in allow_after.values()),
            {"liveAllowedHex": allow_after, "count": {k: len(v) for k, v in allow_after.items()}},
        )
    )

    executed = [c for c in cases if not (c.get("detail") or {}).get("skipped")]
    summary = {
        "schemaVersion": SCHEMA_VERSION,
        "executionEnabled": False,
        "independentLiveVerified": False,
        "x431ReplacementComplete": False,
        "notAFirmwareOrVehicleProof": True,
        "captureRoot": str(root),
        "capturesPresent": present,
        "authenticSkipped": (not present),
        "caseCount": len(cases),
        "executedCount": len(executed),
        "okCount": sum(1 for c in executed if c["ok"]),
        "failCount": sum(1 for c in executed if not c["ok"]),
        "skippedAuthenticCount": sum(1 for c in cases if (c.get("detail") or {}).get("skipped")),
        "evidenceKinds": sorted({c["evidenceKind"] for c in cases}),
        "vinRedacted": True,
        "pid": os.getpid(),
    }
    return {
        "schemaVersion": SCHEMA_VERSION,
        "executionEnabled": False,
        "independentLiveVerified": False,
        "cases": cases,
        "summary": summary,
    }


def _public_decoded(decoded: dict | None) -> dict | None:
    if not decoded:
        return None
    out = dict(decoded)
    if out.get("type") == "vin-ascii":
        out["text"] = redact_vin(str(out.get("text") or ""))
        out["hex"] = None
    return out


def _synthetic_cases(catalog: dict) -> list[dict]:
    cases: list[dict] = []
    dme = profile_by_id(catalog, "porsche-981-2014-dme")
    gw = profile_by_id(catalog, "porsche-981-2014-gateway")
    dsn = operation(dme, "dme-dsn")
    part = operation(gw, "gw-part-f187")

    port = VirtualElmPort(ecu_map=synthetic_map_catalog_sf())
    out = _run_op(dme["id"], "dme-dsn", port)
    ok = bool(out.get("ok") and (out.get("decoded") or {}).get("text") == dsn["expectedValue"] and port.closed)
    cases.append(
        _case(
            "synthetic:sf-dme-dsn",
            "synthetic",
            ok,
            {"expected": dsn["expectedValue"], "got": (out.get("decoded") or {}).get("text"), "closed": port.closed, "error": out.get("error")},
        )
    )

    mf_lines = [
        "77A 10 0E 62 F1 87 39 35 42",
        "77A 21 39 30 37 35 33 30 4E",
        "77A 22 20 AA AA AA AA AA AA",
    ]
    port = VirtualElmPort(ecu_map={"22F187": ath1_prompt(mf_lines)})
    out = _run_op(gw["id"], "gw-part-f187", port)
    got = (out.get("decoded") or {}).get("text")
    ok = bool(out.get("ok") and got == part["expectedValue"] and port.closed)
    cases.append(
        _case(
            "synthetic:mf-gw-f187",
            "synthetic",
            ok,
            {"expected": part["expectedValue"], "got": got, "closed": port.closed, "error": out.get("error")},
        )
    )

    nrc_lines = ["77A 03 7F 22 78 AA AA AA AA"] + mf_lines
    clock = FakeClock()
    port = VirtualElmPort(ecu_map={"22F187": ath1_prompt(nrc_lines)})
    client = ElmClient(port, timeout_s=2.0, clock=clock.time, sleeper=clock.sleep)
    err = None
    try:
        client.validate_adapter(2.0)
        client.configure_pair(gw["txId"], gw["rxId"], clock.time() + 5)
        resp = client.request("22F187", clock.time() + 5)
        if len(port.ecu_payloads) != 1:
            err = f"retransmit:{port.ecu_payloads}"
        elif not resp.get("ok"):
            err = resp.get("error")
        elif not resp.get("pending"):
            err = "missing-pending"
        elif not (resp.get("payload_hex") or "").upper().startswith(part["positivePrefixHex"].upper()):
            err = "prefix"
    except Exception as e:  # noqa: BLE001
        err = str(e)
    finally:
        rest = client.close_restore()
    cases.append(
        _case(
            "synthetic:nrc78-then-final",
            "synthetic",
            err is None and port.closed and not rest.get("errors"),
            {"error": err, "ecuWriteCount": len(port.ecu_payloads), "closed": port.closed},
        )
    )

    port = VirtualElmPort(ecu_map={"22F187": ath1_prompt(["7E8 06 5A 9F 50 32 30 30 AA"])})
    out = _run_op(gw["id"], "gw-part-f187", port)
    cases.append(
        _case(
            "synthetic:wrong-ecu-header",
            "synthetic",
            (not out.get("ok")) and out.get("error") == "malformed-or-wrong-id" and port.closed,
            {"error": out.get("error"), "closed": port.closed},
        )
    )

    port = VirtualElmPort(ecu_map={"22F187": ath1_prompt(["77A 04 62 F1 91 30 AA AA AA"])})
    out = _run_op(gw["id"], "gw-part-f187", port)
    cases.append(
        _case(
            "synthetic:mismatched-did",
            "synthetic",
            (not out.get("ok")) and out.get("error") == "wrong-sid-or-did-echo" and port.closed,
            {"error": out.get("error"), "closed": port.closed},
        )
    )

    port = VirtualElmPort(ecu_map={"22F187": ath1_prompt(["77A 10 0D 62 F1 87 39 35 42"])})
    out = _run_op(gw["id"], "gw-part-f187", port)
    cases.append(
        _case(
            "synthetic:truncated-mf",
            "synthetic",
            (not out.get("ok")) and out.get("error") == "isotp-incomplete-or-malformed" and port.closed,
            {"error": out.get("error"), "closed": port.closed},
        )
    )

    port = VirtualElmPort(
        ecu_map={"22F187": ath1_prompt(["77A 10 0D 62 F1 87 39 35 42", "77A 23 39 30 37 35 33 30 4E"])}
    )
    out = _run_op(gw["id"], "gw-part-f187", port)
    cases.append(
        _case(
            "synthetic:out-of-order-cf",
            "synthetic",
            (not out.get("ok")) and out.get("error") == "isotp-incomplete-or-malformed" and port.closed,
            {"error": out.get("error"), "closed": port.closed},
        )
    )

    port = VirtualElmPort(ecu_map={"22F187": ath1_prompt(["77A ZZ"])})
    out = _run_op(gw["id"], "gw-part-f187", port)
    cases.append(
        _case(
            "synthetic:malformed-line",
            "synthetic",
            (not out.get("ok")) and out.get("error") == "malformed-or-wrong-id" and port.closed,
            {"error": out.get("error"), "closed": port.closed},
        )
    )

    port = VirtualElmPort(ecu_map={"22F187": ath1_prompt(["77A 03 7F 22 31 AA AA AA AA"])})
    out = _run_op(gw["id"], "gw-part-f187", port)
    cases.append(
        _case(
            "synthetic:negative-response",
            "synthetic",
            (not out.get("ok")) and out.get("error") == "negative-response" and port.closed,
            {"error": out.get("error"), "closed": port.closed},
        )
    )

    clock = FakeClock()
    port = VirtualElmPort(ecu_map={}, hang_unknown=True)
    client = ElmClient(port, timeout_s=1.0, clock=clock.time, sleeper=clock.sleep)
    timed = False
    try:
        client.validate_adapter(2.0)
        client.configure_pair(gw["txId"], gw["rxId"], clock.time() + 5)
        client.request("22F187", clock.time() + 1.0)
    except ElmError as e:
        timed = str(e).startswith("prompt-timeout")
    finally:
        client.close_restore()
    cases.append(
        _case(
            "synthetic:timeout",
            "synthetic",
            timed and port.closed,
            {"timedOut": timed, "closed": port.closed, "clock": clock.t},
        )
    )

    clock = FakeClock()
    port = VirtualElmPort(ecu_map={"22F187": ath1_prompt(mf_lines)})
    client = ElmClient(port, timeout_s=1.0, clock=clock.time, sleeper=clock.sleep)
    disc_err = None
    try:
        client.validate_adapter(2.0)
        client.configure_pair(gw["txId"], gw["rxId"], clock.time() + 5)
        port.disconnect = True
        try:
            client.request("22F187", clock.time() + 1.0)
            disc_err = "no-error"
        except (ElmError, ConnectionError) as e:
            disc_err = type(e).__name__
    finally:
        port.disconnect = False
        client.close_restore()
    cases.append(
        _case(
            "synthetic:disconnect",
            "synthetic",
            disc_err in ("ElmError", "ConnectionError") and port.closed,
            {"errorType": disc_err, "closed": port.closed},
        )
    )

    port = VirtualElmPort(ecu_map=synthetic_map_catalog_sf(), atpc_body=b"?\r>", close_error=OSError("close-fail"))
    out = _run_op(dme["id"], "dme-dsn", port)
    rest = out.get("restoration") or {}
    cases.append(
        _case(
            "synthetic:teardown-failure",
            "synthetic",
            bool(out.get("ok")) and bool(rest.get("errors")) and port.closed,
            {"restoration": rest, "closed": port.closed, "decoded": (out.get("decoded") or {}).get("text")},
        )
    )

    clock = FakeClock()
    late = VirtualElmPort(ecu_map={}, hang_unknown=True)
    client = ElmClient(late, timeout_s=1.0, clock=clock.time, sleeper=clock.sleep)
    late_ok = False
    resp2 = {}
    try:
        client.validate_adapter(2.0)
        client.configure_pair(gw["txId"], gw["rxId"], clock.time() + 5)
        try:
            client.request("22F187", clock.time() + 0.5)
        except ElmError:
            pass
        n_writes = len(late.writes)
        try:
            client.request("22F191", clock.time() + 2)
            resp2 = {"error": "reused-after-timeout"}
        except ElmError as e:
            resp2 = {"error": str(e)}
            late_ok = str(e).startswith("client-poisoned") and len(late.writes) == n_writes and len(late.ecu_payloads) == 1
    except Exception as e:  # noqa: BLE001
        late_ok = False
        resp2 = {"error": str(e)}
    finally:
        client.close_restore()
    cases.append(
        _case(
            "synthetic:late-response-other-request",
            "synthetic",
            late_ok and late.closed,
            {"error": (resp2 or {}).get("error"), "ecuPayloads": late.ecu_payloads, "closed": late.closed},
        )
    )

    allowed_dme = live_allowed_hex(dme)
    allowed_gw = live_allowed_hex(gw)
    clear_r = blocked_reason(bytes.fromhex("14FFFFFF"), allowed_gw)
    write_r = blocked_reason(bytes.fromhex("2EF18700"), allowed_gw)
    coding_on = bool((gw.get("coding") or {}).get("enabled"))
    sids = {bytes.fromhex(op["requestHex"])[0] for p in catalog["profiles"] for op in p["operations"]}
    cases.append(
        _case(
            "synthetic:clear-and-coding-rejected",
            "synthetic",
            clear_r == "blocked-sid-14"
            and write_r == "blocked-sid-2E"
            and not coding_on
            and sids.isdisjoint(BLOCKED_SIDS)
            and "14FFFFFF" not in allowed_gw
            and "1A9F" in allowed_dme,
            {"clear": clear_r, "write": write_r, "codingEnabled": coding_on, "blockedSidsInCatalog": sorted(sids & BLOCKED_SIDS)},
        )
    )

    port = VirtualElmPort(ecu_map={"22F187": ath1_prompt([])})
    out_missing = _run_op(gw["id"], "gw-part-f187", port)
    cases.append(
        _case(
            "synthetic:missing-frames",
            "synthetic",
            (not out_missing.get("ok")) and out_missing.get("error") == "empty-payload" and port.closed,
            {"error": out_missing.get("error"), "closed": port.closed},
        )
    )

    ident = VirtualElmPort(identity="NOT-AN-ADAPTER")
    ident_err = None
    try:
        out_id = _run_op(dme["id"], "dme-dsn", ident)
        ident_err = out_id.get("error")
        closed = ident.closed
    except Exception as e:  # noqa: BLE001
        ident_err = str(e)
        closed = ident.closed
    cases.append(
        _case(
            "synthetic:adapter-identity-mismatch",
            "synthetic",
            ident_err == "adapter-identity-mismatch" and closed,
            {"error": ident_err, "closed": closed},
        )
    )

    return cases


def write_result(result: dict, out_path: Path) -> Path:
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(_redact_obj(result), indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return out_path


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m scripts.diagnostics.bench")
    p.add_argument("--out", required=True)
    p.add_argument("--capture-root", default=None)
    args = p.parse_args(argv)
    root = Path(args.capture_root) if args.capture_root else DEFAULT_CAPTURE_ROOT
    result = run_bench(root)
    write_result(result, Path(args.out))
    s = result["summary"]
    print("out", args.out)
    print("capturesPresent", s["capturesPresent"], "ok", s["okCount"], "/", s["executedCount"], "fail", s["failCount"], "skipped", s["skippedAuthenticCount"])
    print("executionEnabled", False, "independentLiveVerified", False)
    return 0 if s["failCount"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
