@echo off
rem ============================================================
rem  把本机后端开放到公网（别人访问 http://公网IP:8000/web/）
rem  1) 自动放行 Windows 防火墙 8000 端口（需要管理员，会弹 UAC）
rem  2) 显示你的公网 IP / 内网 IP / 路由器地址
rem  3) 引导在路由器上做端口映射（这一步必须手动，需要路由器密码）
rem  4) 自动检测是否已通
rem ============================================================
chcp 65001 > nul
setlocal enabledelayedexpansion

rem ---------- 自动提权 ----------
net session >nul 2>&1
if %errorlevel% neq 0 (
    echo 正在请求管理员权限（弹窗请点“是”）...
    mshta vbscript:CreateObject^("Shell.Application"^).ShellExecute^("cmd.exe","/c ""%~s0""","","runas",1^)^&(window.close)
    exit /b
)

set PORT=8000
set PY=C:\Users\73117\.workbuddy\binaries\python\envs\default\Scripts\python.exe
if not exist "%PY%" set PY=C:\Users\73117\.workbuddy\binaries\python\versions\3.13.12\python.exe

echo.
echo ================== 第 1 步：放行防火墙 ==================
netsh advfirewall firewall delete rule name="豆瓣查询-8000" >nul 2>&1
netsh advfirewall firewall add rule name="豆瓣查询-8000" dir=in action=allow protocol=TCP localport=%PORT% profile=any >nul 2>&1
if %errorlevel%==0 (echo   [OK] 已放行 TCP %PORT% 入站) else (echo   [警告] 放行失败，请手动放行)

echo.
echo ================== 第 2 步：网络信息 ==================
for /f "delims=" %%i in ('powershell -NoProfile -Command "(Get-NetIPAddress -AddressFamily IPv4 | Where-Object { $_.IPAddress -notlike '127.*' -and $_.IPAddress -notlike '169.*' } | Select-Object -First 1).IPAddress"') do set LANIP=%%i
for /f "delims=" %%i in ('powershell -NoProfile -Command "(Get-NetRoute -DestinationPrefix '0.0.0.0/0' | Sort-Object RouteMetric | Select-Object -First 1).NextHop"') do set GW=%%i

set PUBIP=
for %%s in (https://myip.ipip.net http://myip.ipip.net) do (
    if not defined PUBIP (
        for /f "tokens=2 delims=：" %%p in ('curl -s -m 8 --noproxy "*" %%s 2^>nul ^| findstr /i "IP"') do (
            for /f "tokens=1 delims= " %%q in ("%%p") do set PUBIP=%%q
        )
    )
)
echo   内网 IP   : %LANIP%
echo   路由器地址 : %GW%
echo   公网 IP   : %PUBIP%

echo.
echo ================== 第 3 步：在路由器上做端口映射 ==================
echo   请用浏览器打开  http://%GW%   登录你的路由器
echo   找到下面任意一个菜单（不同品牌名字不一样）：
echo      · 端口映射 / 虚拟服务器 / Port Mapping / NAT
echo      · 高级设置 - NAT 设置
echo      · 应用 - DMZ / 端口转发
echo   新增一条规则：
echo      外部端口 : %PORT%
echo      内部 IP  : %LANIP%
echo      内部端口 : %PORT%
echo      协议     : TCP
echo   保存后回到这里。
echo.
echo   提示：如果路由器有 DMZ 主机选项，把 DMZ 指向 %LANIP% 也可以（更简单，但整机会暴露）。
echo.
pause

echo.
echo ================== 第 4 步：检测是否通 ==================
echo   正在从本机测试 http://%PUBIP%:%PORT%/api/health ...
set CODE=
for /f %%c in ('curl -s -m 8 --noproxy "*" -o nul -w "%%{http_code}" http://%PUBIP%:%PORT%/api/health 2^>nul') do set CODE=%%c
echo   返回码: %CODE%
echo.
if "%CODE%"=="200" (
    echo   [成功] 公网已经能访问你的后端了！
) else (
    echo   [未通] 可能原因：
    echo      1. 路由器映射没生效（最常见）—— 确认规则已保存且指向 %LANIP%
    echo      2. 本机不支持 NAT 回环—— 用手机关掉 WiFi、用流量访问试试
    echo      3. 运营商封了入站端口 —— 试试把外部端口改成 8080 或 8888（连映射一起改）
    echo      4. 后端没启动 —— 双击「启动豆瓣查询.bat」
)
echo.
echo   别人访问的地址（把下面的链接发给对方）：
echo      http://%PUBIP%:%PORT%/web/
echo.
echo   注意：家宽公网 IP 重启光猫/路由器后会变，变了要重发新地址。
echo.
pause
