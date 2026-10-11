@echo off
REM ASCII launcher name for desktop shortcuts. Real start path is START.cmd.
call "%~dp0START.cmd"
exit /b %ERRORLEVEL%
