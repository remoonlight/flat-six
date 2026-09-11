#!/usr/bin/env bash
# Stop leftover porsche981 Vite / Electron processes for a clean start.
set -u
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SELF=$$
killed=0

kill_pid() {
  local pid="$1"
  [[ -z "$pid" || "$pid" == "$SELF" ]] && return
  if kill -0 "$pid" 2>/dev/null; then
    kill "$pid" 2>/dev/null || true
    killed=1
  fi
}

# Vite default port from apps/desktop/scripts/dev.mjs
if command -v lsof >/dev/null 2>&1; then
  while read -r pid; do
    kill_pid "$pid"
  done < <(lsof -nP -iTCP:5173 -sTCP:LISTEN -t 2>/dev/null || true)
fi

while read -r pid; do
  [[ "$pid" == "$SELF" ]] && continue
  cmd="$(ps -p "$pid" -o args= 2>/dev/null || true)"
  case "$cmd" in
    *"$ROOT"*) kill_pid "$pid" ;;
  esac
done < <(pgrep -f "vite|electron|npm run dev|run-desktop" 2>/dev/null || true)

if [[ "$killed" -eq 0 ]]; then
  echo "[981车库] 没有需要清理的开发进程"
fi
exit 0
