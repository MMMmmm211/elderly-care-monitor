@echo off
chcp 65001 >nul
title 养老监护系统 - 启动

rem 1) 检查 Ollama
curl -s http://localhost:11434/api/tags >nul 2>&1
if errorlevel 1 (
  echo [1/2] Ollama 未运行，正在启动...
  start "ollama" ollama serve
  timeout /t 8 /nobreak >nul
) else (
  echo [1/2] Ollama 已在运行
)

rem 2) 启动进程守护（5 个 ROS2 节点 + Web 界面，崩溃自动重启）
echo [2/2] 启动监控管线 + Web 界面（进程守护）
set "PY310=C:\Users\DELL\AppData\Local\Programs\Python\Python310"
set "PATH=%PY310%;%PY310%\Scripts;%PATH%"
start "养老监护-守护" /d "%~dp0" cmd /k "chcp 65001 >nul && python supervisor.py"

echo.
echo 已启动完成。
echo   手机端:  http://本机IP:5000/mobile
echo   桌面端:  http://本机IP:5000
echo 停止请运行: stop_all.bat
echo.
pause
