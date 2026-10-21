@echo off
rem ============================================================
 停止豆瓣小组查询后台服务（端口 8000）
 ============================================================
chcp 65001 > nul
setlocal

set PORT=8000

echo.
echo === 停止豆瓣小组查询 ===
echo.

netstat -ano | findstr ":%PORT% " | findstr "LISTENING" > nul
if %errorlevel%==0 (
    for /f "tokens=5" %%P in ('netstat -ano ^| findstr ":%PORT% " ^| findstr "LISTENING"') do (
        echo [结束] PID %%P
        taskkill /F /PID %%P > nul 2>&1
    )
    echo [完成] 已停止
) else (
    echo [信息] 端口 %PORT% 没有监听中的进程
)
echo.
pause