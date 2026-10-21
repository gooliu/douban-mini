@echo off
rem ============================================================
 把豆瓣小组查询加到开机自启（创建启动文件夹的快捷方式）
 卸载：删除  %APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\豆瓣小组查询.lnk
 ============================================================
chcp 65001 > nul
setlocal

set SRC=%~dp0启动豆瓣查询.bat
set LNK=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\豆瓣小组查询.lnk

if not exist "%SRC%" (
    echo [错误] 找不到 %SRC%
    pause
    exit /b 1
)

echo === 安装开机自启 ===
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$ws = New-Object -ComObject WScript.Shell;" ^
  "$s = $ws.CreateShortcut('%LNK%');" ^
  "$s.TargetPath = '%SRC%';" ^
  "$s.WorkingDirectory = '%~dp0';" ^
  "$s.WindowStyle = 7;" ^
  "$s.Description = '豆瓣小组查询';" ^
  "$s.Save()"

if exist "%LNK%" (
    echo [完成] 已加入开机自启
    echo        快捷方式：%LNK%
    echo        下次开机后会自动启动豆瓣查询。
) else (
    echo [错误] 创建快捷方式失败
)
echo.
pause