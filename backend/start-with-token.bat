@echo off
rem Start douban-mini backend with your douban login token (real data mode)
cd /d %~dp0
set DOUBAN_TOKEN=9ee3804329d5c8a4dcaa80c97d8159ec
set DOUBAN_CACHE_TTL=600
echo Starting backend with token configured...
echo Web UI: http://127.0.0.1:8000/
C:\Users\73117\.workbuddy\binaries\python\envs\default\Scripts\python.exe -m uvicorn main:app --host 0.0.0.0 --port 8000
pause
