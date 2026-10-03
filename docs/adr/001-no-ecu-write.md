# ADR 001: Staged diagnostics and independent coding

## Current decision — 2026-09-27

On 2026-10-01 the user requested a combined 981/982 PIWIS maintenance/programming workspace, with 991.1/991.2 reserved in Markdown only. This authorizes project-owned catalog summaries, local plaintext rule inspection, offline workflow preparation and export. Roller routines and programming are feature-specific targets; their exact ECU applicability, access, requests, verification, exit and recovery remain unqualified. The workspace therefore has no new vehicle execution channel. See workspace scope（本机资料：`docs/piwis-workshop.md`） and 991 interface reservation（本机资料：`docs/piwis-991-interface.md`）.

The user explicitly requested reverse engineering X431 fault-code definitions, measurement definitions, and hidden-feature coding so the Windows project can ultimately communicate directly with the car through vLinker. Priorities are live engine parameters and X431 hidden features. This supersedes the previous permanent exclusion of protocol-data decoding and independent coding research.

This decision now also authorizes implementation of one constrained active operation: clear DTCs on the named 981 DME/Gateway profiles only. A product confirmation must name the target ECU and acknowledge that X431 is inactive; a read-only confirmation must never clear. The chain is identity qualification, DTC read, durable pre-clear identity/DTC/raw snapshot (`pre-clear.json`, flushed and fsynced), one exact clear request, then a DTC reread. DME uses `14FF00` with KWP reference `54FF00`; Gateway uses `14FFFFFF` with UDS reference `54`. Those are protocol references from local inventory, [Scapy KWP](https://github.com/secdev/scapy/blob/master/scapy/contrib/automotive/kwp.py), and [udsoncan ClearDiagnosticInformation](https://udsoncan.readthedocs.io/en/latest/_modules/udsoncan/services/ClearDiagnosticInformation.html), not observed Porsche clear responses (`sourceObservedResponse=false`, `liveVerified=false`). Unknown payloads, uncertain sends, retries, coding, and all other ECU writes remain blocked. A clear snapshot is evidence, not a way to restore erased DTCs.

The standard engine set is a separate read-only path: qualify DME identity on the observed `7E0/7E8` pair, then configure the fixed standard Mode 01 `7DF/7E8` route, query `0100`, and only query the supported subset of `04`, `05`, `0C`, `0D`, `0F`, `11`. The old `1089` route has an observed `7F0111` failure; there is no automatic fallback or retry. CLI field evidence exists for the fixed standard route; the newly integrated desktop path still needs vehicle acceptance. Published formulas alone do not establish vehicle support. A malformed/failed response stops the run; it does not authorize a guessed address or session. The field procedure is vehicle-connection-runbook.md（本机资料：`docs/vehicle-connection-runbook.md`）.

- Offline inspection and extraction of the user's local diagnostic files, protocol definitions, replay, and a project-owned read-only transport are in scope. Raw vehicle logs and proprietary package files stay local under `.local/`.
- Current live execution is limited to explicitly defined read-only diagnostics plus the constrained clear-DTC operation above. An observed X431 request is evidence, not automatically an approved project operation. Unknown addresses, payloads, formulas, ECU versions, and coding values remain unknown.
- Independent coding is a product target, not a delivered capability. Each concrete feature needs its ECU/version applicability, read-before/write/read-back sequence, timing, integrity/security dependencies, failure behavior, and recovery procedure established before a controlled write validation. This request does not authorize speculative writes, firmware flashing, or actuator tests.
- X431 is a research reference. The final application must own its definitions and transport and must not depend on a running X431 session. On 2026-10-01 the user explicitly added OBDLink MX+ over Bluetooth as a project adapter; disconnect RaceChrono/OBDwiz before using the same adapter here. This supersedes the earlier MX+ reservation, without expanding authorized vehicle operations.
- Existing manual companion snapshots stay compatible. Their optional-before rule does not apply to future automated writes, which require an original-value backup.

On 2026-10-01 the user also requested VNCI support for OBD reading and coding. The locally identified interface is VAS6154A over USB/D-PDU; private serials and driver paths remain in local evidence. The project now supports adapter enumeration/voltage and the existing named read-only diagnostic transport; its initial USB-only voltage was 0 V, and subsequent external-12-V samples were 11.635/11.642 V. No VNCI ECU reads have been validated. VNCI clear-DTC and coding remain disabled until separately qualified on this transport. The coding request retains the feature-specific identity, original backup, exact write/read-back and recovery requirements above. See VNCI USB evidence（本机资料：`docs/vnci-usb.md`）.

See implementation scope（本机资料：`docs/independent-diagnostics.md`）. No live coding was performed when adopting this decision.

## Topology action UI — 2026-10-03

The user explicitly requested removing the topology read/clear confirmation panels and cancel controls. Clicking the selected module's action button, or GW's batch action button, now starts the operation directly. This supersedes the modal confirmation requirement for these topology actions; the current implementation remains limited to the named DME/Gateway profiles. X431 must be inactive during vehicle communication. Identity qualification, durable pre-clear backup, one exact clear request, reread, transport qualification and failure/timeout gates remain in place. No other ECU write or coding capability is added.

## Previous decision (historical)

This app never sends write commands to the vehicle.

- **X431 companion**: users perform coding on Launch X431 Pro3S; this app only stores playbooks and before/after snapshots.
- **OBD (product direction)**: **read-only** — DTC + live PIDs may be logged locally (`docs/obd-plan.md`). **No clear-DTC, no coding, no flash, no hidden-feature writes.**

## Previous consequences (historical)

- No VCI write / UDS write / YZJM decrypt in this repository
- Coding UI is read + manual note entry only
- OBD UI: **read-only** (Phase 1 delivered: code lookup + manual sessions, no adapter); hardware read in Phase 2+; clear-code is out of scope
