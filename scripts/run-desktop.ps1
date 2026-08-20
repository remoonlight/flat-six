# One-click launcher: kill leftovers, start npm run dev detached/hidden, exit immediately.
$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $repoRoot

if (-not (Test-Path (Join-Path $repoRoot "package.json"))) {
  throw "package.json not found at $repoRoot"
}

$node = Get-Command node -ErrorAction SilentlyContinue
if (-not $node) {
  throw "Node.js not found in PATH (need >= 20)"
}

& (Join-Path $PSScriptRoot "kill-dev-related.ps1")

if (-not $env:ELECTRON_MIRROR) {
  $env:ELECTRON_MIRROR = "https://npmmirror.com/mirrors/electron/"
}

if (-not (Test-Path (Join-Path $repoRoot "node_modules"))) {
  $install = Start-Process -FilePath "npm.cmd" -ArgumentList "install" `
    -WorkingDirectory $repoRoot -Wait -PassThru -WindowStyle Hidden
  if ($install.ExitCode -ne 0) {
    throw "npm install failed with code $($install.ExitCode)"
  }
}

# Detached: no console left on desktop; Electron UI is the only window.
Start-Process -FilePath "npm.cmd" -ArgumentList "run", "dev" `
  -WorkingDirectory $repoRoot -WindowStyle Hidden

exit 0
