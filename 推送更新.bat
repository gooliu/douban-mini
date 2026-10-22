@echo off
chcp 65001 >nul
cd /d "%~dp0"
title 推送更新到 GitHub（gooliu/douban-mini）

set "GITEXE=C:\Users\73117\.workbuddy\binaries\PortableGit\versions\1.2.0\cmd\git.exe"
if not exist "%GITEXE%" set "GITEXE=C:\Users\73117\.workbuddy\binaries\PortableGit\versions\1.2.0\mingw64\bin\git.exe"
if not exist "%GITEXE%" set "GITEXE=git"

echo ============================================
echo   推送本地更新到 GitHub
echo   仓库：https://github.com/gooliu/douban-mini
echo ============================================
echo.
echo  方式 1：直接回车 —— 会弹出 GitHub 登录窗口/浏览器，登录并授权即可
echo  方式 2：已生成 Personal Access Token —— 粘贴过来回车（不会外传）
echo.
set TOKEN=
set /p TOKEN=Token（没有就直接回车）: 

echo.
if "%TOKEN%"=="" (
    "%GITEXE%" push origin main
) else (
    "%GITEXE%" push "https://%TOKEN%@github.com/gooliu/douban-mini.git" main
)

if errorlevel 1 (
    echo.
    echo --------------------------------------------
    echo  推送失败，按下面顺序排查：
    echo   1. 弹出登录窗但登录失败 —— 改用 Token 方式：
    echo      github.com ^> 右上角头像 ^> Settings ^> Developer settings
    echo      ^> Personal access tokens ^> Tokens (classic)
    echo      ^> Generate new token ^> 只勾 repo ^> 生成后复制
    echo      然后重新双击本文件，粘贴 Token 回车
    echo   2. 网络连不上 GitHub —— 需要代理，或换网络重试
    echo   3. 提示 non-fast-forward —— 远程有更新，先执行：git pull --rebase
    echo --------------------------------------------
) else (
    echo.
    echo ============================================
    echo  推送成功！现在可以去服务器部署了：
    echo    http://43.108.119.94/
    echo ============================================
)
echo.
pause
