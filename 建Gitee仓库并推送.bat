@echo off
rem ============================================================
rem  把代码推到 Gitee（码云）仓库
rem  方式 1：给我一个私人令牌，全自动建仓库 + 推送
rem  方式 2：你自己在 gitee.com 建好空仓库，把地址粘进来，只推送
rem ============================================================
chcp 65001 > nul
setlocal enabledelayedexpansion
cd /d "%~dp0"

set REPO_NAME=douban-mini
set GIT=C:\Program Files\Git\cmd\git.exe
if not exist "%GIT%" set GIT=git

echo.
echo ================== 豆瓣小组查询：推送到 Gitee ==================
echo.
echo   请先确认本地没有遗留的私密文件（douban_config.json 已设置忽略）。
echo.
echo   请选择：
echo     [1] 我有 Gitee 私人令牌（自动建仓库并推送）
echo     [2] 我已经在 gitee.com 建好了空仓库（只推送）
echo.
set /p MODE=输入 1 或 2 后回车：

set TOKEN=
set REMOTE_URL=

if "%MODE%"=="2" goto HAVE_REPO

:NEED_TOKEN
echo.
echo   获取私人令牌：gitee.com 右上角头像 → 设置 → 安全设置 → 私人令牌
echo                → 生成新令牌 → 勾选 projects（仓库）权限 → 提交后复制
echo.
set /p TOKEN=请粘贴私人令牌：
if not defined TOKEN goto NEED_TOKEN

echo.
echo   正在创建仓库 %REPO_NAME% ...
set API_OUT=%TEMP%\gitee_create.json
curl -s -m 20 --noproxy "*" -X POST "https://gitee.com/api/v5/user/repos?access_token=%TOKEN%" -d "name=%REPO_NAME%" -d "description=豆瓣小组查询工具：查用户加入的小组、发帖与回帖历史" -d "private=false" -d "auto_init=false" -o "%API_OUT%"

findstr /i "html_url" "%API_OUT%" >nul
if %errorlevel% neq 0 (
    echo.
    echo   [失败] 创建仓库失败，返回内容：
    type "%API_OUT%"
    echo.
    echo   常见原因：令牌权限不够（要勾 projects）、同名仓库已存在、令牌复制不完整。
    goto END
)

for /f "tokens=*" %%u in ('powershell -NoProfile -Command "$j=Get-Content '%API_OUT%' -Raw | ConvertFrom-Json; $j.html_url"') do set HTML_URL=%%u
for /f "tokens=*" %%u in ('powershell -NoProfile -Command "$j=Get-Content '%API_OUT%' -Raw | ConvertFrom-Json; $j.owner.login"') do set OWNER=%%u
echo   [成功] 仓库已创建：%HTML_URL%

set REMOTE_URL=https://%OWNER%:%TOKEN%@gitee.com/%OWNER%/%REPO_NAME%.git
goto PUSH

:HAVE_REPO
echo.
set /p REMOTE_URL=粘贴仓库 HTTPS 地址（形如 https://gitee.com/你的用户名/douban-mini.git）：
if not defined REMOTE_URL goto HAVE_REPO

:PUSH
echo.
echo ================== 提交本地改动 ==================
"%GIT%" add -A
"%GIT%" commit -m "豆瓣小组查询：CloudStudio 部署版 + 公网访问与 Gitee 脚本" >nul 2>&1

"%GIT%" branch -M master >nul 2>&1
"%GIT%" remote remove origin >nul 2>&1
"%GIT%" remote add origin "%REMOTE_URL%"

echo.
echo   正在推送...
"%GIT%" push -u origin master
if %errorlevel%==0 (
    echo.
    echo   [成功] 已推送到 Gitee！
    echo   仓库地址：%HTML_URL%
) else (
    echo.
    echo   [失败] 推送失败。常见原因：
    echo     1. 仓库不是空的（建仓库时勾了初始化 README）→ 重新建一个完全空的仓库
    echo     2. 令牌过期或权限不足
    echo     3. 网络问题（可重试一次）
)

:END
echo.
pause
