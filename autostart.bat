@echo off
chcp 65001 >nul
rem 养老监护系统 - 开机自启（静默启动守护进程，最小化窗口，不阻塞）

set "PY310=C:\Users\DELL\AppData\Local\Programs\Python\Python310"
set "PATH=%PY310%;%PY310%\Scripts;%PATH%"

rem 守护进程会在新窗口内运行（最小化），崩溃自愈 / Ollama 看门狗 / 优雅停机
start "养老监护-守护" /min /d "%~dp0" cmd /k "chcp 65001 >nul && python supervisor.py"

rem 立即退出，不 pause，避免开机时留下残留黑窗
exit /b
