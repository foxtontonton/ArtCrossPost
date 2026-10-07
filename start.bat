@echo off
chcp 65001 >nul
echo 正在启动 ArtCrossPost 多平台发布助手...
python app.py
if errorlevel 1 (
    echo.
    echo 程序退出异常，请检查上述错误信息。
    pause
)
