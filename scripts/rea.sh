#!/usr/bin/env bash
# Development-only, project-local REA. Never installs or updates on launch.
set -euo pipefail
task_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
rea_root="$task_root/.local/tools/rea"
entry="$rea_root/node_modules/rea-agents/scripts/rea.mjs"
if [[ ! -f "$entry" ]]; then
  printf '%s\n' 'REA 未安装；项目使用 rea-agents@6.5.0，安装方式见 .codex/README.md。' >&2
  exit 1
fi
node_bin="$(command -v node || true)"
if [[ -z "$node_bin" && -x /opt/homebrew/bin/node ]]; then
  node_bin=/opt/homebrew/bin/node
fi
if [[ -z "$node_bin" ]]; then
  printf '%s\n' 'REA 需要 Node.js 22.x（22.19+）、24.x（24.11+）或 26+。' >&2
  exit 1
fi
umask 077
mkdir -p "$rea_root/cache" "$rea_root/config" "$rea_root/tmp"
export XDG_CACHE_HOME="$rea_root/cache"
export XDG_CONFIG_HOME="$rea_root/config"
export TMPDIR="$rea_root/tmp"
if [[ -z "${GHIDRA_INSTALL_DIR:-}" && -d "$rea_root/engines/ghidra_12.1.4_PUBLIC" ]]; then
  export GHIDRA_INSTALL_DIR="$rea_root/engines/ghidra_12.1.4_PUBLIC"
fi
if [[ -z "${JAVA_HOME:-}" && -x "$rea_root/engines/jdk-21.0.12.1+1/Contents/Home/bin/java" ]]; then
  export JAVA_HOME="$rea_root/engines/jdk-21.0.12.1+1/Contents/Home"
fi
if [[ -z "${REA_ADB_PATH:-}" && -x "$task_root/.local/obd-x431-live-reference-20261010/tools/platform-tools/adb" ]]; then
  export REA_ADB_PATH="$task_root/.local/obd-x431-live-reference-20261010/tools/platform-tools/adb"
fi
exec "$node_bin" "$entry" "$@"
