@echo off
chcp 65001 >nul
cd /d "%~dp0"
start "" "D:\software\Anaconda3\pythonw.exe" "%~dp0ms_gui.py"
