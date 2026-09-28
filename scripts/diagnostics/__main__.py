from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# catalog/plan/replay must not import serial. live is imported only for `read`.


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m scripts.diagnostics",
        description=(
            "Read-only 981 diagnostics CLI. Runtime: CPython 3.14 stdlib; "
            "pyserial is optional and loaded only for `read`. "
            "Does not write ECU, does not replace X431, does not send arbitrary CAN."
        ),
    )
    sub = parser.add_subparsers(dest="mode", required=True)

    p_cat = sub.add_parser("catalog", help="Print definition catalog (no serial)")
    p_cat.add_argument("--json", action="store_true")

    p_plan = sub.add_parser("plan", help="Show named read operations (no serial)")
    p_plan.add_argument("--profile", required=True)

    p_rep = sub.add_parser("replay", help="Offline ISO-TP replay of a capture directory")
    p_rep.add_argument("capture_dir")
    p_rep.add_argument("--out", default=None)

    p_read = sub.add_parser(
        "read",
        help="EXPLICIT live vLinker read-only. Not run by catalog/plan/replay.",
    )
    p_read.add_argument("--profile", required=True)
    p_read.add_argument("--operation", required=True)
    p_read.add_argument(
        "--yes-read-only-live",
        action="store_true",
        help="Required. X431 must be inactive. No hardware test in this work unit.",
    )

    args = parser.parse_args(argv)
    if args.mode == "catalog":
        from .catalog import catalog_cli_json, load_catalog

        cat = load_catalog()
        if args.json:
            print(json.dumps(catalog_cli_json(cat), ensure_ascii=False))
        else:
            pub = catalog_cli_json(cat)["publicReference"]
            print("schemaVersion", cat["schemaVersion"])
            print("evidence: observed-x431-capture (NOT independently-verified-vlinker)")
            print("coding: disabled, payloads null")
            print("X431 replacement: not complete")
            print("live allowlist: catalog.v1.json liveAllowed only; public entries excluded")
            for p in cat["profiles"]:
                print(f"- {p['id']} {p['txId']}/{p['rxId']} {p['ecu']} ops={len(p['operations'])}")
            if pub.get("present"):
                print(
                    "publicReference",
                    "engineCandidates",
                    len(pub.get("engineCandidates") or []),
                    "codingCandidates",
                    len(pub.get("codingCandidates") or []),
                    "executionEnabled=false",
                )
                for g in pub.get("definitionGaps") or []:
                    print("gap:", g)
            else:
                print("publicReference: absent")
        return 0
    if args.mode == "plan":
        from .catalog import catalog_cli_json, load_catalog, profile_by_id

        profile = profile_by_id(load_catalog(), args.profile)
        print("profile", profile["id"])
        print("transport", profile["transport"])
        print("coding enabled", profile["coding"]["enabled"], "payloads", profile["coding"]["payloads"])
        for op in profile["operations"]:
            print(
                f"{op['id']}\t{op['requestHex']}\t{op['serviceFamily']}\tliveAllowed={op['liveAllowed']}\t{op['label']}"
            )
        pub = catalog_cli_json()["publicReference"]
        print("unmet: no executable engine live-data operations in observed catalog")
        print("unmet: coding disabled; public coding is menu text only")
        if pub.get("present"):
            print("public engine candidates", len(pub.get("engineCandidates") or []), "(not allowlisted)")
            print("public coding candidates", len(pub.get("codingCandidates") or []), "(not executable)")
            for g in pub.get("definitionGaps") or []:
                print("gap:", g)
        return 0
    if args.mode == "replay":
        from .replay import replay_capture, write_replay

        result = replay_capture(Path(args.capture_dir))
        dest = write_replay(result, Path(args.out) if args.out else None)
        s = result["summary"]
        print("out", dest)
        print("frames", s["frameCount"], "statusMatch", s["statusCountMatch"], "serialMatch", s["serialMatchesFrames"])
        print("tx", s["transactionCounts"], "pendingChains", s["pendingChainsAssembled"], "orphan62", s["orphan62"])
        print("decoded", len(s["decoded"]))
        return 0
    if args.mode == "read":
        if not args.yes_read_only_live:
            print("refusing: pass --yes-read-only-live; catalog/plan/replay never open serial", file=sys.stderr)
            return 2
        from .live import run_read

        out = run_read(args.profile, args.operation)
        print(json.dumps({k: out[k] for k in out if k != "raw"}, ensure_ascii=False, indent=2))
        return 0 if out.get("ok") else 1
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
