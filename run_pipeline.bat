@echo off
chcp 65001 >nul
rem ============================================
rem  Elderly care vision pipeline launcher
rem  【已废弃】仅启动 2 个节点（开发阶段），生产请用 start_all.bat / supervisor.py
rem  Prereqs: 1) Ollama running + llava:7b pulled
rem ============================================

rem Force Python 3.10 (ROS2 Humble matched version)
set "PY310=C:\Users\DELL\AppData\Local\Programs\Python\Python310"
set "PATH=%PY310%;%PY310%\Scripts;%PATH%"

call E:\ros2\ros2-windows\setup.bat
cd /d "%~dp0"

echo [1/2] Starting video publisher -^> /camera/image
start "video_publisher" /d "%~dp0" cmd /k "chcp 65001 >nul && python video_publisher.py --ros-args -p source:=test_room.jpg"

timeout /t 2 /nobreak >nul

echo [2/2] Starting vision analysis -^> /vision/analysis
start "vision_analysis" /d "%~dp0" cmd /k "chcp 65001 >nul && python vision_analysis_node.py"

echo.
echo Both nodes started. View results with:
echo   ros2 topic echo vision/analysis
echo.
pause
