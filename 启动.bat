@echo off
setlocal
chcp 936 >nul 2>&1
title Scope_ShuaShuaLe - RIGOL DS2000E Web Console
cd /d "%~dp0"

echo.
echo   Scope_ShuaShuaLe  .  RIGOL DS2000E 网页控制台
echo   ----------------------------------------------------------
echo.

REM ---- 找 Python：SCOPE_PY > 常规安装位置 > PATH > py 启动器 ----
set "PY="
call :try_py "%SCOPE_PY%"
call :try_py "%LOCALAPPDATA%\Programs\Python\Python314\python.exe"
call :try_py "%LOCALAPPDATA%\Programs\Python\Python313\python.exe"
call :try_py "%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
call :try_py "%LOCALAPPDATA%\Programs\Python\Python311\python.exe"
call :try_py "%LOCALAPPDATA%\Programs\Python\Python310\python.exe"
call :try_py "C:\Python314\python.exe"
call :try_py "C:\Python313\python.exe"
call :try_py "C:\Python312\python.exe"
call :try_py "C:\Python311\python.exe"
if not defined PY for /f "delims=" %%P in ('where python 2^>nul') do if not defined PY set "PY=%%P"
if not defined PY py -3 -c "" >nul 2>&1 && set "PY=py"

if not defined PY (
    echo   [x] 没找到 Python。
    echo.
    echo       请先安装 Python 3.10 或以上版本：
    echo         https://www.python.org/downloads/
    echo       安装时勾选 "Add python.exe to PATH"，装好后再双击本文件。
    echo.
    pause
    exit /b 1
)
echo   [1/4] Python:  %PY%
echo.

echo   [2/4] 检查依赖（首次运行需要联网安装，约 1 分钟）...
"%PY%" -c "import flask, numpy" >nul 2>&1
if errorlevel 1 (
    "%PY%" -m pip install -r requirements.txt -i https://mirrors.aliyun.com/pypi/simple/
    if errorlevel 1 (
        echo.
        echo   [x] 依赖安装失败。可以手动执行：
        echo       "%PY%" -m pip install -r requirements.txt
        echo.
        pause
        exit /b 1
    )
)
echo.

echo   [3/4] 清理占用 8760 端口的旧进程（如有）...
for /f "tokens=5" %%p in ('netstat -ano ^| findstr :8760 ^| findstr LISTENING') do taskkill /PID %%p /F >nul 2>&1
echo.

echo   [4/4] 启动服务，浏览器会自动打开 http://127.0.0.1:8760/
echo.
echo         连上示波器 = 真机模式；没连上 = DEMO（仿真数据，不碰设备）
echo         关掉本窗口即停止服务
echo.
"%PY%" web\app.py --open

echo.
echo   服务已停止。
pause
goto :eof

:try_py
if defined PY goto :eof
if "%~1"=="" goto :eof
if exist "%~1" set "PY=%~1"
goto :eof
