#!/bin/bash
# 克隆或解压仓库后双击本文件（或在终端运行）。真正逻辑在 scripts/run-desktop.sh。
cd "$(dirname "$0")" || exit 1
exec bash "./scripts/run-desktop.sh"
