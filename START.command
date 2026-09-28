#!/bin/bash
# ASCII alias of 开始车库.command for tools that mangle Chinese filenames.
cd "$(dirname "$0")" || exit 1
exec bash "./scripts/run-desktop.sh"
