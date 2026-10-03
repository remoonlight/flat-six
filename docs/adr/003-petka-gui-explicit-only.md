# ADR 003: PETKA GUI automation only when explicitly allowed

## Decision

Background agents and scripts **must not** drive the PETKA / Etka7 GUI by default (no SendKeys, mouse inject, window activate, focus-stealing screenshots, or kill/restart of Etka7).

Price and locator capture uses **passive inbox / TSV** (user pastes or drops files under `.local/`).

**Exception**: GUI automation is allowed only when the user **explicitly** authorizes it in the current session (and preferably documents IL / UIPI constraints: Etka7 often High IL; Medium agents may be blocked). Do not invent authorization from silence.

## Consequences

- Default path: `watch:petka-inbox`, `watch:locator-inbox`, `apply:petka-handcopy`, `status:petka`
- Killing Etka7 to “recover” is forbidden unless the user orders it (session often falls back to Volkswagen)
- Requirements Q3 = B (2026-07-31)
