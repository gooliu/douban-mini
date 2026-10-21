@echo off
chcp 65001 >nul
cd /d "%~dp0"
title 推送 douban-mini 到 GitHub
echo ============================================
echo   推送 douban-mini 到 GitHub
echo ============================================
echo.
echo  前置：到 https://github.com/new 新建一个空仓库
echo        （名字随便，不要勾 README / .gitignore / license）
echo  建好后复制它的 HTTPS 地址，形如：
echo        https://github.com/yourname/douban-mini.git
echo.
echo  注意：GitHub 需要能访问的网络（当前这台机器直连 GitHub 会 TLS 失败，
echo        如果推送卡住或报网络错误，请先开好代理再重试）。
echo.
set REPO=
set /p REPO=粘贴仓库地址后回车: 
if "%REPO%"=="" (
    echo 没填地址，已取消。
    pause
    exit /b 1
)

echo.
git remote remove origin >nul 2>&1
git remote add origin %REPO%
git branch -M main
echo 正在推送...（若弹出登录窗口，用 GitHub 账号登录即可）
echo.
git push -u origin main

if %errorlevel% neq 0 (
    echo.
    echo --------------------------------------------
    echo  推送失败，常见原因：
    echo   1. 网络连不上 GitHub —— 需要代理 / VPN，或换一个能访问的网络
    echo   2. 认证失败 —— 密码要填 Personal Access Token，不是登录密码
    echo      生成地址：github.com ^> Settings ^> Developer settings
    echo                ^> Personal access tokens ^> Tokens(classic) ^> 勾 repo
    echo   3. 仓库地址写错，或仓库不是空的
    echo --------------------------------------------
) else (
    echo.
    echo ============================================
    echo  推送成功！接下来部署后端（约 3 分钟）：
    echo.
    echo   1. 打开 https://render.com 用 GitHub 登录
    echo   2. New +  ^>  Blueprint  ^>  选这个仓库  ^>  Apply
    echo      （它会自动读仓库里的 render.yaml，不用填任何参数）
    echo   3. 等状态变 Live，拿到地址：
    echo      https://douban-mini-xxxx.onrender.com
    echo   4. 把这个地址直接发给别人即可（手机也能打开，可加到桌面）
    echo.
    echo  想要 github.io 地址的话：仓库 Settings ^> Pages ^> Source
    echo  选 GitHub Actions，再到 Settings ^> Variables 加一个
    echo  CLOUD_API = 上面那个 onrender.com 地址，重跑一次工作流即可。
    echo ============================================
)
echo.
pause
