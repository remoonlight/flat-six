# Compatibility entrypoint: always show CMD, and wait for the complete session.
$ErrorActionPreference = 'Stop'
Add-Type -Path (Join-Path $PSScriptRoot 'desktop-job.cs')
$taskEntry = Join-Path $PSScriptRoot 'run-desktop.cmd'
$taskCommand = '"{0}" /d /s /c ""{1}" --supervised"' -f $env:ComSpec, $taskEntry
exit [DesktopJob]::Run($env:ComSpec, $taskCommand, (Split-Path -Parent $PSScriptRoot), $PID, $true)
