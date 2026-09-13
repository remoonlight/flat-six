# Stop only this checkout's desktop processes, never arbitrary port owners or tools.
[CmdletBinding(SupportsShouldProcess)]
param()

$ErrorActionPreference = "SilentlyContinue"
$repoRoot = Split-Path -Parent $PSScriptRoot
$processes = @(Get-CimInstance Win32_Process)
$pids = [System.Collections.Generic.HashSet[int]]::new()
$protected = [System.Collections.Generic.HashSet[int]]::new()
# Protect the launcher and every ancestor (including a calling Node test runner).
$ancestorId = $PID
while ($ancestorId -gt 0 -and $protected.Add($ancestorId)) {
  $ancestor = $processes | Where-Object ProcessId -eq $ancestorId | Select-Object -First 1
  if (-not $ancestor) { break }
  $ancestorId = [int]$ancestor.ParentProcessId
}

$electronExe = Join-Path $repoRoot "node_modules\electron\dist\electron.exe"
$entryPoints = @(
  "apps\desktop\scripts\dev.mjs",
  "apps\desktop\electron\db-bridge.mjs",
  "apps\desktop\electron\obd-bridge.mjs",
  "node_modules\vite\bin\vite.js"
) | ForEach-Object { [IO.Path]::GetFullPath((Join-Path $repoRoot $_)) }
# Match the actual Node entry point, not a project path appearing in arguments.
$nodeEntryPattern = '^\s*(?:"[^"]+"|\S+)\s+(?:"([^"]+)"|(\S+))'
foreach ($proc in $processes) {
  if ($protected.Contains([int]$proc.ProcessId)) { continue }
  $isElectron = $proc.Name -eq "electron.exe" -and $proc.ExecutablePath -eq $electronExe
  $isNodeEntry = $false
  if ($proc.Name -eq "node.exe" -and $proc.CommandLine -match $nodeEntryPattern) {
    $entry = if ($Matches[1]) { $Matches[1] } else { $Matches[2] }
    if ([IO.Path]::IsPathRooted($entry)) {
      # npm's .bin shim uses paths like .bin\..\vite\bin\vite.js.
      try { $isNodeEntry = $entryPoints -contains [IO.Path]::GetFullPath($entry) } catch {}
    }
  }
  if ($isElectron -or $isNodeEntry) { [void]$pids.Add([int]$proc.ProcessId) }
}

# Include the children of matched dev processes (npx / cmd wrappers, renderer, etc.).
do {
  $added = $false
  foreach ($proc in $processes) {
    if ($pids.Contains([int]$proc.ParentProcessId) -and -not $protected.Contains([int]$proc.ProcessId)) {
      if ($pids.Add([int]$proc.ProcessId)) { $added = $true }
    }
  }
} while ($added)

if ($pids.Count -eq 0) {
  Write-Host "[porsche981] no related processes to kill"
  exit 0
}

foreach ($procId in @($pids)) {
  try {
    if ($PSCmdlet.ShouldProcess("PID $procId", "Stop desktop process")) {
      Stop-Process -Id $procId -Force -ErrorAction Stop
      Write-Host "[porsche981] killed PID $procId"
    }
  } catch {
    Write-Host "[porsche981] skip PID $procId : $($_.Exception.Message)"
  }
}
exit 0
