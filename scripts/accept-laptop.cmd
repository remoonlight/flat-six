@echo off
setlocal
if "%~1"=="" goto usage
if "%~2"=="" goto usage
if not "%~4"=="" goto usage
set "FLATSIX_ACCEPT_OPTION="
if not "%~3"=="" (
  if not "%~3"=="--check-only" goto usage
  set "FLATSIX_ACCEPT_OPTION=--check-only"
)
set "FLATSIX_ACCEPT_NODE=%~f1\resources\app\runtime\node.exe"
if not exist "%FLATSIX_ACCEPT_NODE%" (
  echo Bundled Node not found in the supplied Flat Six directory.
  exit /b 2
)
if not exist "%~dp0.local" mkdir "%~dp0.local"
if not exist "%~dp0.local" exit /b 3
set "FLATSIX_ACCEPT_LOG=%~dp0.local\laptop-accept-%RANDOM%-%RANDOM%.log"
if exist "%FLATSIX_ACCEPT_LOG%" exit /b 3
echo Running isolated software checks. Log: "%FLATSIX_ACCEPT_LOG%"
"%FLATSIX_ACCEPT_NODE%" "%~dp0scripts\windows-runtime-accept.mjs" "%~f1" "%~f2" "%~dp0.local" %FLATSIX_ACCEPT_OPTION% > "%FLATSIX_ACCEPT_LOG%" 2>&1
set "FLATSIX_ACCEPT_EXIT=%errorlevel%"
type "%FLATSIX_ACCEPT_LOG%"
exit /b %FLATSIX_ACCEPT_EXIT%
:usage
echo Usage: accept-laptop.cmd "Flat Six directory" "definitions-live-current.zip" [--check-only]
exit /b 2
