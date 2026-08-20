# Kill leftover porsche981 / Vite / Electron processes for a clean one-click start.
$ErrorActionPreference = "SilentlyContinue"
$repoHint = "porsche981"
$pids = [System.Collections.Generic.HashSet[int]]::new()

function Add-Pid([int]$id) {
  if ($id -gt 0) { [void]$pids.Add($id) }
}

try {
  Get-NetTCPConnection -LocalPort 5173 -State Listen -ErrorAction Stop |
    ForEach-Object { Add-Pid $_.OwningProcess }
} catch {}

$procNames = @("node.exe", "electron.exe", "npm.cmd", "cmd.exe")
Get-CimInstance Win32_Process |
  Where-Object {
    $n = $_.Name
    $c = $_.CommandLine
    if (-not $c) { return $false }
    if ($c -notmatch [regex]::Escape($repoHint) -and $c -notmatch [regex]::Escape("D:\code\porsche981") -and $c -notmatch [regex]::Escape("D:/code/porsche981")) {
      return $false
    }
    if ($n -match '^(node|electron)\.exe$') { return $true }
    if ($n -eq "cmd.exe" -and ($c -match "npm run dev" -or $c -match "run-desktop" -or $c -match "vite")) { return $true }
    return $false
  } |
  ForEach-Object { Add-Pid $_.ProcessId }

# Also catch vite/npx without full path but cwd-linked via 5173 already handled;
# widen: any node vite on 5173 parent tree already in $pids.

if ($pids.Count -eq 0) {
  Write-Host "[porsche981] no related processes to kill"
  exit 0
}

$self = $PID
foreach ($procId in @($pids)) {
  if ($procId -eq $self) { continue }
  try {
    Stop-Process -Id $procId -Force -ErrorAction Stop
    Write-Host "[porsche981] killed PID $procId"
  } catch {
    Write-Host "[porsche981] skip PID $procId : $($_.Exception.Message)"
  }
}
exit 0
