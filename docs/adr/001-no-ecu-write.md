# ADR 001: No ECU write from this app

## Decision

This app never sends write commands to the vehicle.

- **X431 companion**: users perform coding on Launch X431 Pro3S; this app only stores playbooks and before/after snapshots.
- **OBD (product direction)**: **read-only** — DTC + live PIDs may be logged locally (`docs/obd-plan.md`). **No clear-DTC, no coding, no flash, no hidden-feature writes.**

## Consequences

- No VCI write / UDS write / YZJM decrypt in this repository
- Coding UI is read + manual note entry only
- OBD UI: **read-only** (Phase 1 delivered: code lookup + manual sessions, no adapter); hardware read in Phase 2+; clear-code is out of scope
