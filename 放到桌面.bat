@echo off
rem ============================================================
 把"启动豆瓣查询"快捷方式放到桌面，方便随时双击
 ============================================================
chcp 65001 > nul
setlocal

set SRC=%~dp0启动豆瓣查询.bat
set DESK=%USERPROFILE%\Desktop
set LNK=%DESK%\豆瓣小组查询.lnk

if not exist "%SRC%" (
    echo [错误] 找不到 %SRC%
    pause
    exit /b 1
)

if not exist "%DESK%" (
    echo [错误] 找不到桌面 %DESK%
    pause
    exit /b 1
)

echo === 创建桌面快捷方式 ===
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$ws = New-Object -ComObject WScript.Shell;" ^
  "$s = $ws.CreateShortcut('%LNK%');" ^
  "$s.TargetPath = '%SRC%';" ^
  "$s.WorkingDirectory = '%~dp0';" ^
  "$s.WindowStyle = 7;" ^
  "$s.Description = '启动豆瓣小组查询（端口 8000）';" ^
  "$s.Save()"

if exist "%LNK%" (
    echo [完成] 桌面已出现：豆瓣小组查询
    echo        双击即可启动并打开浏览器。
) else (
    echo [错误] 创建失败
)
echo.
pause