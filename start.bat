@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo ============================================
echo  ArtCrossPost 多平台发布助手 - 启动器
echo ============================================
echo.

rem 定位 Python 解释器：优先 python，备选 py 启动器
set "PYCMD="
where python >nul 2>&1
if not errorlevel 1 set "PYCMD=python"
if not defined PYCMD (
    where py >nul 2>&1
    if not errorlevel 1 set "PYCMD=py -3"
)
if not defined PYCMD (
    echo [错误] 找不到 Python 解释器！
    echo.
    echo 可能原因：
    echo  1. 未安装 Python 3.10+，请从 python.org 下载安装；
    echo  2. 安装时未勾选 "Add python.exe to PATH"。
    echo.
    echo 提示：若双击 app.py 能直接运行，说明 Python 已安装，
    echo       只是 PATH 没配，装个 "Add to PATH" 或用 py 启动器即可。
    echo.
    pause
    exit /b 1
)

echo 使用解释器: %PYCMD%
%PYCMD% --version
echo.
echo 正在启动 app.py ...
echo --------------------------------------------
%PYCMD% app.py
set "EXITCODE=%errorlevel%"
echo --------------------------------------------
echo.
if "%EXITCODE%"=="0" (
    echo 程序正常退出。
) else (
    echo 程序异常退出，退出码: %EXITCODE%
    echo 请把上方错误信息截图发给开发者排查。
)
echo.
echo 按任意键关闭窗口...
pause >nul
