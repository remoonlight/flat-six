@echo off
REM AV-friendly launcher: plain cmd, no hidden PowerShell shortcut.
REM Starts npm run dev in a minimized window, then this window exits.
setlocal
cd /d "%~dp0.."
if not exist "package.json" (
  echo [porsche981] package.json not found
  pause
  exit /b 1
)

where node >nul 2>&1
if errorlevel 1 (
  echo [porsche981] Node.js not in PATH ^(need ^>=20^)
  pause
  exit /b 1
)

if not defined ELECTRON_MIRROR set "ELECTRON_MIRROR=https://npmmirror.com/mirrors/electron/"

echo [porsche981] killing leftover Vite / Electron / node for this repo ...

REM Listeners on 5173
for /f "tokens=5" %%p in ('netstat -ano ^| findstr /R /C:":5173 .*LISTENING"') do (
  if not "%%p"=="0" taskkill /F /PID %%p >nul 2>&1
)

REM Processes whose command line mentions this repo
wmic process where "CommandLine like '%%porsche981%%' and (Name='node.exe' or Name='electron.exe')" call terminate >nul 2>&1

if not exist "node_modules\" (
  echo [porsche981] npm install ...
  call npm install
  if errorlevel 1 (
    echo [porsche981] npm install failed
    pause
    exit /b 1
  )
)

echo [porsche981] starting ^(minimized console; Electron UI is the app^) ...
start "porsche981-dev" /MIN /D "%CD%" cmd /c "set ELECTRON_MIRROR=%ELECTRON_MIRROR%&& npm run dev"
exit /b 0
