@echo off
chcp 65001 >nul
cd /d "%~dp0"
call "%~dp0_find_python.bat"
if not defined PY (
  echo [ERROR] Python not found. Edit _find_python.bat and add your python.exe path.
  pause
  exit /b 1
)
"%PY%" record.py %*
echo.
pause
