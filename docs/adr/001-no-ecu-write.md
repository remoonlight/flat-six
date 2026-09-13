# ADR 001: No ECU coding writes; explicit DTC-clear exception

## Decision

Updated 2026-09-13 following the user's explicit request for a one-click clear-DTC operation and confirmation that read/clear should cover all supported control units. This supersedes the previous absolute read-only decision only for clearing diagnostic trouble codes.

- **X431 companion**: users perform coding on Launch X431 Pro3S; this app only stores playbooks and before/after snapshots.
- **OBD**: diagnostic reads and locally logged observations. Clear-DTC is permitted through a dedicated operation after an actual vehicle response and a fresh scan. Show the actual protocol/module scope; persist pre-clear evidence before transmission; reread faults and report remaining/failed/unknown results. An ambiguous write must not be replayed automatically.
- **No coding, flash, hidden-feature writes, security unlock, or actuator commands** are introduced by this exception. A renderer must not receive arbitrary raw-command access.
- An adapter having power is not proof of a vehicle connection. Simulation data cannot satisfy live-operation gates. A software implementation is not evidence of on-vehicle validation.

## Consequences

- No general-purpose VCI/UDS write or YZJM decrypt interface in this repository.
- Coding UI is read + manual note entry only
- OBD UI provides explicit read and clear buttons. Clearing diagnostic memory may reset readiness and erase diagnostic evidence; the UI explains this beside the button. Permanent DTCs are not claimed cleared by Mode 04. A positive service response and a clean reread are distinct outcomes.
- The authorized implementation is tested offline without connecting to or clearing a real vehicle. Hardware acceptance is recorded separately.
