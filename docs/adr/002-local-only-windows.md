# ADR 002: Local-only Windows desktop

## Decision

Single-user Windows Electron app. SQLite via Node built-in `node:sqlite` at `%APPDATA%\porsche981\garage.db` (dev: `.local/garage.db`). No cloud sync, no accounts. Avoids native `better-sqlite3` build (no VS C++ toolchain required).

## Consequences

- No Supabase / auth
- Seed data ships in `data/seed/`
