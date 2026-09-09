@echo off
REM 下载仓库后双击本文件即可。真正逻辑在 scripts\run-desktop.cmd。
call "%~dp0scripts\run-desktop.cmd"
exit /b %ERRORLEVEL%
