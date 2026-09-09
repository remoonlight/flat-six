@echo off
REM One-click start for non-developers. Plain CMD (no hidden PowerShell shortcut).
chcp 65001 >nul
setlocal EnableExtensions
title 981 车库
cd /d "%~dp0.."
if not exist "package.json" (
  echo [981车库] 找不到 package.json。请把整个仓库文件夹完整解压后再双击「开始车库.cmd」。
  pause
  exit /b 1
)

echo.
echo ========================================
echo   2014 Boxster S（981）本机车库
echo ========================================
echo.

if not defined ELECTRON_MIRROR set "ELECTRON_MIRROR=https://npmmirror.com/mirrors/electron/"

where node >nul 2>&1
if errorlevel 1 (
  if exist "%ProgramFiles%\nodejs\node.exe" set "PATH=%ProgramFiles%\nodejs;%PATH%"
)
where node >nul 2>&1
if errorlevel 1 (
  echo [981车库] 还没有安装 Node.js（需要 20 或更高）。
  echo           这是运行本软件唯一要装的东西，装好一次即可。
  echo.
  echo   官网：https://nodejs.org/
  echo   请选 LTS，安装时勾选 “Add to PATH”。
  echo.
  where winget >nul 2>&1
  if errorlevel 1 (
    echo [981车库] 本机没有 winget，请自行打开上面的官网安装，装完后重新双击「开始车库.cmd」。
    pause
    exit /b 1
  )
  echo 按任意键用 Windows 自动安装 Node.js LTS（需要网络，可能弹出确认框）...
  pause >nul
  winget install -e --id OpenJS.NodeJS.LTS --accept-package-agreements --accept-source-agreements
  if errorlevel 1 (
    echo [981车库] 自动安装失败。请打开 https://nodejs.org/ 手动安装 LTS 后重试。
    pause
    exit /b 1
  )
  if exist "%ProgramFiles%\nodejs\node.exe" set "PATH=%ProgramFiles%\nodejs;%PATH%"
  where node >nul 2>&1
  if errorlevel 1 (
    echo [981车库] Node.js 已安装，但当前窗口还读不到。请关掉本窗口，再双击一次「开始车库.cmd」。
    pause
    exit /b 0
  )
)

for /f "tokens=1 delims=v" %%a in ('node -v 2^>nul') do set "NODEVER=%%a"
for /f "tokens=1 delims=." %%a in ("%NODEVER%") do set "NODEMAJOR=%%a"
if not defined NODEMAJOR set "NODEMAJOR=0"
if %NODEMAJOR% LSS 20 (
  echo [981车库] 当前 Node.js 是 v%NODEVER%，需要 20 或更高。请升级：https://nodejs.org/
  pause
  exit /b 1
)
echo [981车库] Node.js v%NODEVER%

echo [981车库] 清理上次没关干净的开发进程 ...
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0kill-dev-related.ps1" >nul 2>&1

if not exist ".local\" mkdir ".local" >nul 2>&1
if not exist ".local\xray-transforms.json" if exist "data\seed\xray\transforms.template.json" (
  copy /Y "data\seed\xray\transforms.template.json" ".local\xray-transforms.json" >nul
)
if not exist ".local\xray-mesh-state.json" if exist "data\seed\xray\mesh-state.template.json" (
  copy /Y "data\seed\xray\mesh-state.template.json" ".local\xray-mesh-state.json" >nul
)
if not exist ".local\model-oem-links.json" if exist "data\seed\xray\model-oem-links.seed.json" (
  copy /Y "data\seed\xray\model-oem-links.seed.json" ".local\model-oem-links.json" >nul
)

if not exist "node_modules\" (
  echo [981车库] 第一次运行：正在安装依赖（可能要几分钟，请保持网络畅通）...
  call npm install
  if errorlevel 1 (
    echo [981车库] npm install 失败。请检查网络后重试。
    pause
    exit /b 1
  )
)

echo [981车库] 正在启动。请等 Electron 窗口出现；本黑框请留着，关掉它软件也会退出。
echo.
set "ELECTRON_MIRROR=%ELECTRON_MIRROR%"
call npm run dev
set "ERR=%ERRORLEVEL%"
echo.
if not "%ERR%"=="0" (
  echo [981车库] 启动失败，退出码 %ERR%。
  pause
)
exit /b %ERR%
