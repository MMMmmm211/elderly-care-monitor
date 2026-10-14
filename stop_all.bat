@echo off
chcp 65001 >nul
title 养老监护系统 - 停止

echo 正在停止所有服务...
echo stop > "%~dp0supervisor.stop"
timeout /t 8 /nobreak >nul

rem 兜底：若守护进程未响应，仅结束本项目相关进程（避免误杀其它 python）
echo 检查是否有残留进程...
powershell -NoProfile -ExecutionPolicy Bypass -Command "Get-CimInstance Win32_Process | Where-Object { $_.Name -eq 'python.exe' -and ($_.CommandLine -match 'supervisor\.py|video_publisher|vision_analysis_node|risk_manager|alert_delivery_node|review_agent_node|web_ui\.py') } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }"
echo 已停止。
pause
