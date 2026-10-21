@echo off
rem ============================================================
rem  分享给他人使用（同一 WiFi / 局域网）
rem  右键本文件 → 以管理员身份运行
rem  作用：放行 Windows 防火墙 8000 端口，并显示别人的访问地址。
rem ============================================================
chcp 65001 > nul
setlocal

set ROOT=%~dp0
set PY=C:\Users\73117\.workbuddy\binaries\python\envs\default\Scripts\python.exe
set PORT=8000

echo.
echo === 分享豆瓣小组查询 给同一 WiFi 的人 ===
echo.

rem 1. 检查管理员权限（放行防火墙必须）；没有就自动弹 UAC 重新打开自己
net session >nul 2>&1
if %errorlevel% neq 0 (
    echo [需要管理员] 正在请求权限，请在弹出的窗口点「是」...
    echo            （若没弹窗，请右键本文件 → 以管理员身份运行）
    echo.
    powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
    exit /b 0
)

rem 2. 检查服务是否在跑
netstat -ano | findstr ":%PORT% " | findstr "LISTENING" > nul
if %errorlevel% neq 0 (
    echo [提示] 后端还没启动，正在帮你拉起...
    if exist "%ROOT%backend\main.py" (
        cd /d "%ROOT%backend"
        start "豆瓣查询后台 (不要关)" /min "%PY%" -m uvicorn main:app --host 0.0.0.0 --port %PORT%
        ping 127.0.0.1 -n 6 > nul
    ) else (
        echo [错误] 找不到 %ROOT%backend\main.py
        pause
        exit /b 1
    )
)

rem 3. 放行防火墙（先删后加，避免重复规则堆积）
echo [1/3] 放行 Windows 防火墙 8000 端口...
netsh advfirewall firewall delete rule name="豆瓣小组查询-8000" > nul 2>&1
netsh advfirewall firewall add rule name="豆瓣小组查询-8000" dir=in action=allow protocol=TCP localport=%PORT% > nul
if %errorlevel% neq 0 (
    echo        [警告] 防火墙放行失败，可能系统策略限制。
    echo        若别人打不开，请手动在「Windows Defender 防火墙 → 允许应用」里放行 Python。
) else (
    echo        已放行。
)

rem 4. 取本机局域网 IP（优先 192.168.*）
echo [2/3] 获取本机局域网地址...
set LANIP=
for /f "delims=" %%i in ('powershell -NoProfile -Command "$ips = Get-NetIPAddress -AddressFamily IPv4 ^| Where-Object { $_.IPAddress -ne '127.0.0.1' -and $_.PrefixOrigin -ne 'WellKnown' }; $p = $ips ^| Where-Object { $_.IPAddress -like '192.168.*' }; if (-not $p) { $p = $ips ^| Where-Object { $_.IPAddress -like '10.*' } }; if (-not $p) { $p = $ips }; ($p ^| Select-Object -First 1).IPAddress"') do set LANIP=%%i

if not defined LANIP (
    echo [错误] 没取到局域网 IP，你可能没连 WiFi / 网线。
    pause
    exit /b 1
)

echo [3/3] 打开分享面板...
echo.
echo ============================================================
echo.
echo   把下面这个地址发给对方（同一 WiFi 才能访问）：
echo.
echo        http://%LANIP%:%PORT%/
echo.
echo   或让对方扫二维码：浏览器打开上面的地址后，
echo   点首页最下方的「📤 分享给他人使用」即可看到二维码。
echo.
echo   注意：你现在若已登录豆瓣，对方会看到你的账号数据。
echo   不希望暴露，就先在分享面板里点「退出登录」。
echo.
echo   用完想收回：运行「停止豆瓣查询.bat」，
echo   或管理员运行 netsh advfirewall firewall delete rule name="豆瓣小组查询-8000"
echo.
echo ============================================================
echo.

start "" "http://%LANIP%:%PORT%/"
pause
exit /b 0
