@echo off
rem ============================================================
 启动豆瓣小组查询（端口 8000）
 双击即可。会自动检查端口、后台拉起 uvicorn 并打开浏览器。
 提示：本脚本运行结束后服务仍在后台运行；
       想彻底停止请用 "停止豆瓣查询.bat"。
 ============================================================
chcp 65001 > nul
setlocal

set ROOT=%~dp0
set BACKEND=%ROOT%backend
set PY=C:\Users\73117\.workbuddy\binaries\python\envs\default\Scripts\python.exe
set PORT=8000
set URL=http://127.0.0.1:%PORT%/

echo.
echo === 豆瓣小组查询 一键启动 ===
echo.

:: 1. 检查后端目录
if not exist "%BACKEND%\main.py" (
    echo [错误] 找不到 %BACKEND%\main.py
    echo        请确认脚本位置正确。
    pause
    exit /b 1
)

:: 2. 检查端口是否已被占用
netstat -ano | findstr ":%PORT% " | findstr "LISTENING" > nul
if %errorlevel%==0 (
    echo [信息] 端口 %PORT% 已被占用，可能服务已经在跑。
    echo        直接打开浏览器即可：
    echo        %URL%
    start "" "%URL%"
    exit /b 0
)

:: 3. 检查 Python
if not exist "%PY%" (
    echo [错误] 找不到 Python: %PY%
    echo        请确认 WorkBuddy Python 环境已就绪。
    pause
    exit /b 1
)

:: 4. 启动后端（新窗口，关闭最小化窗口不会停服务）
echo [启动] 拉起后端...
cd /d "%BACKEND%"
start "豆瓣查询后台 (不要点)" /min "%PY%" -m uvicorn main:app --host 0.0.0.0 --port %PORT%

:: 5. 等服务起来
set /a TRIED=0
:WAIT
set /a TRIED+=1
ping 127.0.0.1 -n 2 > nul
netstat -ano | findstr ":%PORT% " | findstr "LISTENING" > nul
if %errorlevel%==0 goto READY
if %TRIED% GEQ 10 (
    echo [错误] 服务 10 秒内没起来，请查看后端日志 %BACKEND%\server.log
    pause
    exit /b 1
)
goto WAIT

:READY
echo [就绪] 服务已启动
echo.
echo        浏览器已自动打开：%URL%
echo        如未打开，请手动复制到浏览器访问。
echo.
start "" "%URL%"
exit /b 0