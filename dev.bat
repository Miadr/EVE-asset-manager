@echo off
chcp 65001 >nul
cd /d "%~dp0"
title EVE Asset Manager - 开发服务器

echo ======================================
echo   EVE Asset Manager 开发环境启动
echo ======================================
echo.

python --version >nul 2>&1
if errorlevel 1 (
    echo [错误] 未检测到 Python，请先安装 Python 3.10+
    pause & exit /b 1
)

node --version >nul 2>&1
if errorlevel 1 (
    echo [错误] 未检测到 Node.js，请先安装 Node.js
    pause & exit /b 1
)

if not exist "frontend\node_modules" (
    echo [前端] 首次运行，正在安装 npm 依赖...
    cd frontend
    npm install
    cd ..
)

echo [后端] 启动 FastAPI  ^(http://127.0.0.1:8000^) ...
start "EVE 后端" cmd /k "cd /d %~dp0 & python -m uvicorn backend.main:app --host 127.0.0.1 --port 8000 --reload & pause"

ping -n 3 127.0.0.1 >nul

echo [前端] 启动 Vite 开发服务器 ^(http://localhost:5173^) ...
start "EVE 前端" cmd /k "cd /d %~dp0frontend & npm run dev & pause"

echo.
echo ======================================
echo  后端 API : http://127.0.0.1:8000/docs
echo  前端页面 : http://localhost:5173
echo  关闭两个命令行窗口即可停止服务
echo ======================================
echo.

ping -n 4 127.0.0.1 >nul
start http://localhost:5173
