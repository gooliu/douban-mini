@echo off
rem ============================================================
rem  分享给不在同一 WiFi 的人（生成一个公网网址）
rem  原理：用内网穿透把本机 8000 端口映射到一个公网地址。
rem  注意：网址是临时的，用完关掉这个窗口即可停止分享。
rem ============================================================
chcp 65001 > nul
setlocal

set ROOT=%~dp0
set PY=C:\Users\73117\.workbuddy\binaries\python\envs\default\Scripts\python.exe
set PORT=8000

echo.
echo === 内网穿透分享（外网也能访问）===
echo.

rem 1. 确认服务在跑
netstat -ano | findstr ":%PORT% " | findstr "LISTENING" > nul
if %errorlevel% neq 0 (
    echo [提示] 后端还没启动，正在帮你拉起...
    cd /d "%ROOT%backend"
    start "豆瓣查询后台 (不要关)" /min "%PY%" -m uvicorn main:app --host 0.0.0.0 --port %PORT%
    ping 127.0.0.1 -n 6 > nul
)

rem 2. 找已安装的穿透工具
set TOOL=
where cloudflared >nul 2>&1
if %errorlevel%==0 set TOOL=cloudflared
if not defined TOOL (
    where cpolar >nul 2>&1
    if %errorlevel%==0 set TOOL=cpolar
)
if not defined TOOL (
    if exist "%LOCALAPPDATA%\cloudflared\cloudflared.exe" set TOOL=cloudflared
)
if not defined TOOL (
    if exist "C:\Program Files\cpolar\cpolar.exe" set TOOL=cpolar
)

if defined TOOL goto RUN

rem 3. 都没装 → 引导安装
echo 还没检测到穿透工具，先装一个（只需一次）：
echo.
echo   [1] Cloudflare Tunnel（推荐，免费、不限速、不用注册）
echo       winget install --id Cloudflare.cloudflared -e
echo.
echo   [2] cpolar（国内速度快，免费版网址会变）
echo       去 https://www.cpolar.com/download 下载安装
echo.
echo   装好后重新双击本脚本即可。
echo.
set /p CH=现在尝试自动安装 cloudflared？输入 y 回车，其他键退出：
if /i not "%CH%"=="y" exit /b 0
echo.
echo [安装] winget install Cloudflare.cloudflared ...
winget install --id Cloudflare.cloudflared -e --accept-source-agreements --accept-package-agreements
if %errorlevel% neq 0 (
    echo [失败] 自动安装没成功。请手动装好后重新运行本脚本。
    pause
    exit /b 1
)
set TOOL=cloudflared

:RUN
echo.
echo 使用工具：%TOOL%
echo 正在生成公网地址，请稍候...（窗口里出现 https://xxx.trycloudflare.com 就是地址）
echo.
echo ------------------------------------------------------------
if /i "%TOOL%"=="cloudflared" (
    cloudflared tunnel --url http://127.0.0.1:%PORT%
) else (
    cpolar http %PORT%
)
echo ------------------------------------------------------------
echo.
echo 已停止分享。
echo 提醒：公网网址不要长期开着，用完关掉即可。
pause
exit /b 0
