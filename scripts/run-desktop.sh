#!/usr/bin/env bash
# macOS / Unix one-click start for developers. Mirrors scripts/run-desktop.cmd.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

if [[ ! -f package.json ]]; then
  echo "[981车库] 找不到 package.json。请把整个仓库文件夹完整克隆或解压后再运行。"
  exit 1
fi

echo
echo "========================================"
echo "  2014 Boxster S（981）本机车库"
echo "========================================"
echo

if [[ -z "${ELECTRON_MIRROR:-}" ]]; then
  export ELECTRON_MIRROR="https://npmmirror.com/mirrors/electron/"
fi

ensure_node() {
  if command -v node >/dev/null 2>&1; then
    return 0
  fi
  if [[ -x /usr/local/bin/node ]]; then
    PATH="/usr/local/bin:$PATH"
    return 0
  fi
  if [[ -x /opt/homebrew/bin/node ]]; then
    PATH="/opt/homebrew/bin:$PATH"
    return 0
  fi
  return 1
}

if ! ensure_node; then
  echo "[981车库] 还没有安装 Node.js（需要 20 或更高）。"
  echo "          这是运行本软件唯一要装的东西，装好一次即可。"
  echo
  echo "  官网：https://nodejs.org/  （选 LTS）"
  echo
  if command -v brew >/dev/null 2>&1; then
    echo "按 Enter 用 Homebrew 安装 Node.js LTS，或 Ctrl+C 取消后自行安装。"
    read -r _
    brew install node@22 || brew install node
    PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"
    hash -r
  else
    echo "[981车库] 本机没有 Homebrew。请先安装 Node.js LTS，然后重新运行本脚本。"
    echo "  Homebrew：https://brew.sh/"
    exit 1
  fi
  if ! ensure_node; then
    echo "[981车库] Node.js 已安装，但当前窗口还读不到。请关掉终端，再运行一次。"
    exit 0
  fi
fi

NODEVER="$(node -v 2>/dev/null | sed 's/^v//')"
NODEMAJOR="${NODEVER%%.*}"
if [[ -z "$NODEMAJOR" || "$NODEMAJOR" -lt 20 ]]; then
  echo "[981车库] 当前 Node.js 是 v${NODEVER}，需要 20 或更高。请升级：https://nodejs.org/"
  exit 1
fi
echo "[981车库] Node.js v${NODEVER}"

echo "[981车库] 清理上次没关干净的开发进程 ..."
bash "$ROOT/scripts/kill-dev-related.sh" || true

mkdir -p .local
if [[ ! -f .local/xray-transforms.json && -f data/seed/xray/transforms.template.json ]]; then
  cp data/seed/xray/transforms.template.json .local/xray-transforms.json
fi
if [[ ! -f .local/xray-mesh-state.json && -f data/seed/xray/mesh-state.template.json ]]; then
  cp data/seed/xray/mesh-state.template.json .local/xray-mesh-state.json
fi
if [[ ! -f .local/model-oem-links.json && -f data/seed/xray/model-oem-links.seed.json ]]; then
  cp data/seed/xray/model-oem-links.seed.json .local/model-oem-links.json
fi

if [[ ! -d node_modules ]]; then
  echo "[981车库] 第一次运行：正在安装依赖（可能要几分钟，请保持网络畅通）..."
  npm install
fi

echo "[981车库] 正在启动。请等 Electron 窗口出现；本终端请留着，关掉它软件也会退出。"
echo
exec npm run dev
