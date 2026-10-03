# ADR 002: Local-only Windows desktop

## Decision

Single-user Windows Electron app. SQLite via Node built-in `node:sqlite` at `%APPDATA%\porsche981\garage.db` (dev: `.local/garage.db`). No cloud sync, no accounts. Avoids native `better-sqlite3` build (no VS C++ toolchain required).

## Development hosts

产品端仍是 Windows。macOS 仅支持开发和贡献者运行，可通过 `scripts/run-desktop.sh` 启动。开发数据库仍为 `.local/garage.db`。

打包后的 Electron userData 路径按平台不同：Windows 为 `%APPDATA%\porsche981\garage.db`；macOS 实际为 `~/Library/Application Support/@porsche981/desktop/`。

## Consequences

- No Supabase / auth
- Seed data ships in `data/seed/`
