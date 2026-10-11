# Keep all startup/build/runtime descendants in this console's Windows job.
$ErrorActionPreference = 'Stop'
try {
    Add-Type -Path (Join-Path $PSScriptRoot 'desktop-job.cs')
    $taskEntry = Join-Path $PSScriptRoot 'run-desktop.cmd'
    $taskCommand = '"{0}" /d /s /c ""{1}" --supervised"' -f $env:ComSpec, $taskEntry
    $taskParentId = (Get-CimInstance Win32_Process -Filter "ProcessId = $PID").ParentProcessId
    exit [DesktopJob]::Run($env:ComSpec, $taskCommand, (Split-Path -Parent $PSScriptRoot), $taskParentId)
} catch {
    Write-Host "[981 garage] Startup supervision failed: $_"
    exit 1
}
