@echo off
chcp 65001 >nul
title 养老监护系统 - 取消开机自启

echo 正在取消开机自启...
reg delete "HKCU\Software\Microsoft\Windows\CurrentVersion\Run" /v "ElderCareMonitor" /f >nul 2>&1
if %errorlevel%==0 (
  echo 已取消开机自启。
) else (
  echo 未找到开机自启项（可能尚未设置）。
)
pause
