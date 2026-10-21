@echo off
chcp 65001 >nul
cd /d "%~dp0"
title 豆瓣查询 - 公网隧道
echo.
echo  ============================================================
echo    正在把本机后端暴露到公网（免注册、免账号）
echo  ============================================================
echo.
echo   这个窗口必须一直开着，链接才有效。关掉窗口 = 链接失效。
echo.

set "PY=C:\Users\73117\.workbuddy\binaries\python\envs\default\Scripts\python.exe"
if not exist "%PY%" set "PY=C:\Users\73117\.workbuddy\binaries\python\versions\3.13.12\python.exe"

"%PY%" -u tools\tunnel.py

echo.
echo  隧道已断开。
pause
