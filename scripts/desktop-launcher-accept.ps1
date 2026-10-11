# Headless Windows lifecycle checks. Never open visible test CMD windows.
$ErrorActionPreference = 'Stop'
$taskRoot = Split-Path -Parent $PSScriptRoot
$taskOutput = Join-Path $taskRoot ('.local\desktop-launcher-' + (Get-Date -Format 'yyyyMMdd-HHmmss'))
New-Item -ItemType Directory -Path $taskOutput | Out-Null
function Wait-Until($condition, $message, $seconds = 20) {
    $taskDeadline = [DateTime]::UtcNow.AddSeconds($seconds)
    do {
        if (& $condition) { return }
        Start-Sleep -Milliseconds 300
    } while ([DateTime]::UtcNow -lt $taskDeadline)
    throw "Timed out: $message"
}
function Get-TaskTree($parentId) {
    $all = @(Get-CimInstance Win32_Process)
    $ids = [System.Collections.Generic.HashSet[int]]::new()
    [void]$ids.Add($parentId)
    do {
        $added = $false
        foreach ($proc in $all) {
            if ($ids.Contains([int]$proc.ParentProcessId) -and $ids.Add([int]$proc.ProcessId)) { $added = $true }
        }
    } while ($added)
    @($all | Where-Object { $ids.Contains([int]$_.ProcessId) })
}
$taskOwned = [System.Collections.Generic.List[System.Diagnostics.Process]]::new()
$taskResults = [System.Collections.Generic.List[string]]::new()
$taskNames = @('TEST_JOB','TEST_NODE','TEST_FIXTURE','TEST_OUTPUT','TEST_STATE','PORSCHE981_DB',
    'PORSCHE981_USER_DATA','PORSCHE981_CONNECTION_FIXTURE','PORSCHE981_CONNECTION_STATE',
    'PORSCHE981_SESSION_DENY_LIVE','PORSCHE981_DEV_PORT','ELECTRON_RUN_AS_NODE')
$taskSaved = @{}
foreach ($name in $taskNames) { $taskSaved[$name] = [Environment]::GetEnvironmentVariable($name) }
try {
    $env:TEST_JOB = Join-Path $PSScriptRoot 'desktop-job.cs'
    $env:TEST_NODE = (Get-Command node).Source
    $env:TEST_OUTPUT = $taskOutput
    $env:TEST_FIXTURE = Join-Path $taskOutput 'fixture.cjs'
    @'
const fs = require('node:fs');
const { spawn } = require('node:child_process');
if (process.argv[2] === 'leaf') setInterval(() => {}, 1000);
else {
  const leaf = spawn(process.execPath, [__filename, 'leaf'], { detached: true, stdio: 'ignore' });
  leaf.unref();
  fs.writeFileSync(process.env.TEST_STATE, JSON.stringify({ root: process.pid, leaf: leaf.pid }));
  setInterval(() => { if (fs.existsSync(process.env.TEST_STATE + '.exit')) process.exit(37); }, 100);
}
'@ | Set-Content -LiteralPath $env:TEST_FIXTURE -Encoding UTF8
    $taskRunner = Join-Path $taskOutput 'fixture.ps1'
    @'
Add-Type -Path $env:TEST_JOB
$PID | Set-Content -LiteralPath ($env:TEST_STATE + '.supervisor')
$owner = (Get-CimInstance Win32_Process -Filter "ProcessId = $PID").ParentProcessId
exit [DesktopJob]::Run($env:TEST_NODE, ('"{0}" "{1}"' -f $env:TEST_NODE, $env:TEST_FIXTURE), $env:TEST_OUTPUT, $owner)
'@ | Set-Content -LiteralPath $taskRunner -Encoding UTF8
    $taskSentinel = Start-Process $env:TEST_NODE -ArgumentList ('"{0}" leaf' -f $env:TEST_FIXTURE) -WindowStyle Hidden -PassThru
    $taskOwned.Add($taskSentinel)
    foreach ($mode in @('cmd-end', 'supervisor-end', 'normal-exit')) {
        $env:TEST_STATE = Join-Path $taskOutput ($mode + '.json')
        $hostProcess = Start-Process $env:ComSpec -ArgumentList ('/d /s /c "powershell -NoProfile -ExecutionPolicy Bypass -File "{0}""' -f $taskRunner) -WindowStyle Hidden -PassThru
        $taskOwned.Add($hostProcess)
        [void]$hostProcess.Handle
        Wait-Until { Test-Path -LiteralPath $env:TEST_STATE } "$mode fixture started"
        $state = Get-Content -Raw -LiteralPath $env:TEST_STATE | ConvertFrom-Json
        $supervisorId = [int](Get-Content -LiteralPath ($env:TEST_STATE + '.supervisor'))
        $processes = @($hostProcess) + @($state.root, $state.leaf, $supervisorId | ForEach-Object { Get-Process -Id $_ })
        foreach ($proc in $processes) { $taskOwned.Add($proc); [void]$proc.Handle }
        if ($mode -eq 'cmd-end') { $hostProcess.Kill() }
        elseif ($mode -eq 'supervisor-end') { Stop-Process -Id $supervisorId -Force }
        else { Set-Content -LiteralPath ($env:TEST_STATE + '.exit') -Value 'exit' }
        Wait-Until { @($processes | Where-Object { -not $_.HasExited }).Count -eq 0 } "$mode descendants exited"
        if ($mode -eq 'normal-exit' -and $hostProcess.ExitCode -ne 37) { throw 'Exit code lost' }
        if ($taskSentinel.HasExited) { throw 'Unrelated process stopped' }
        $taskResults.Add("PASS ${mode}: complete tree gone; unrelated process kept")
    }
    $listener = [Net.Sockets.TcpListener]::new([Net.IPAddress]::Loopback, 0)
    $listener.Start()
    $env:PORSCHE981_DEV_PORT = [string]$listener.LocalEndpoint.Port
    $listener.Stop()
    $env:PORSCHE981_DB = Join-Path $taskOutput 'garage.db'
    $env:PORSCHE981_USER_DATA = Join-Path $taskOutput 'user-data'
    $env:PORSCHE981_CONNECTION_FIXTURE = Join-Path $taskOutput 'devices.json'
    $env:PORSCHE981_CONNECTION_STATE = Join-Path $taskOutput 'connection.json'
    $env:PORSCHE981_SESSION_DENY_LIVE = '1'
    Remove-Item Env:ELECTRON_RUN_AS_NODE -ErrorAction SilentlyContinue
    '{"devices":[],"errors":[]}' | Set-Content -LiteralPath $env:PORSCHE981_CONNECTION_FIXTURE -Encoding ASCII
    $taskApp = Start-Process $env:ComSpec -ArgumentList ('/d /s /c ""{0}\START.cmd" > "{1}\app.log" 2>&1"' -f $taskRoot, $taskOutput) -WindowStyle Hidden -PassThru
    $taskOwned.Add($taskApp)
    $script:taskTree = @()
    Wait-Until {
        if ($taskApp.HasExited) { throw 'Source launcher exited early: see app.log' }
        $script:taskTree = @(Get-TaskTree $taskApp.Id)
        @($script:taskTree | Where-Object { $_.CommandLine -match 'db-bridge.mjs' }).Count -gt 0
    } 'real Electron/DB bridge started' 60
    $processes = @($script:taskTree | ForEach-Object { Get-Process -Id $_.ProcessId -ErrorAction SilentlyContinue })
    foreach ($proc in $processes) { $taskOwned.Add($proc); [void]$proc.Handle }
    if (-not @($script:taskTree | Where-Object { $_.CommandLine -match 'run-desktop-supervised.ps1' }).Count) { throw 'Supervision missing' }
    $script:taskTree | Select-Object ProcessId,ParentProcessId,Name,CommandLine | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $taskOutput 'app-processes.json')
    $taskApp.Kill()
    Wait-Until { @($processes | Where-Object { -not $_.HasExited }).Count -eq 0 } 'real app complete tree exited'
    $taskResults.Add('PASS START.cmd: Electron and DB bridge started; ending CMD removed all descendants')
    $listener.Start()
    $env:PORSCHE981_DEV_PORT = [string]$listener.LocalEndpoint.Port
    try {
        $conflict = Start-Process $env:TEST_NODE -ArgumentList ('"{0}\apps\desktop\scripts\dev.mjs"' -f $taskRoot) -WindowStyle Hidden -PassThru `
            -RedirectStandardOutput (Join-Path $taskOutput 'conflict.log') -RedirectStandardError (Join-Path $taskOutput 'conflict-error.log')
        $taskOwned.Add($conflict)
        if (-not $conflict.WaitForExit(15000)) { throw 'Occupied port did not fail promptly' }
        if ($conflict.ExitCode -eq 0 -or (Get-Content -Raw (Join-Path $taskOutput 'conflict-error.log')) -notmatch 'already in use') { throw 'Occupied port error missing' }
    } finally { $listener.Stop() }
    if ($taskSentinel.HasExited) { throw 'Unrelated process stopped' }
    $taskResults.Add('PASS occupied port: rejected before opening Electron; existing listener kept')
    $taskResults.Add('NOTE headless process checks; physical console close is not automated')
    $taskResults | Set-Content -LiteralPath (Join-Path $taskOutput 'results.txt')
    $taskResults
    Write-Output "Evidence: $taskOutput"
} finally {
    foreach ($proc in $taskOwned) {
        if (-not $proc.HasExited) { try { $proc.Kill() } catch { Write-Warning "Cleanup PID $($proc.Id): $_" } }
    }
    foreach ($name in $taskNames) { [Environment]::SetEnvironmentVariable($name, $taskSaved[$name]) }
}
