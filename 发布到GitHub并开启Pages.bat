@echo off
rem ============================================================
rem  一键发布到 GitHub：建仓库 + 推送 + 开启 Pages + 验证网址
rem  需要一个 GitHub Personal Access Token（只用于这一次，之后可撤销）
rem ============================================================
chcp 65001 > nul
setlocal
cd /d "%~dp0"

set PY=C:\Users\73117\.workbuddy\binaries\python\envs\default\Scripts\python.exe
if not exist "%PY%" set PY=C:\Users\73117\.workbuddy\binaries\python\versions\3.13.12\python.exe

if not exist "%PY%" (
    echo 找不到 Python，请先安装或重新运行「启动豆瓣查询.bat」初始化环境。
    pause
    exit /b 1
)

echo.
echo  需要 GitHub Personal Access Token。获取方法：
echo    github.com 右上角头像 → Settings → Developer settings
echo    → Personal access tokens → Tokens (classic) → Generate new token
echo    → 勾选 repo（完整仓库权限），有效期随意 → Generate
echo    → 复制那串 ghp_ 开头的字符
echo.
echo  粘贴后回车即可（输入不会显示）。
echo.

"%PY%" tools\publish_github.py %*
echo.
pause