@echo off
chcp 65001 >nul
cd /d %~dp0
D:\software\Anaconda3\python.exe ms.py start %*
echo.
pause
