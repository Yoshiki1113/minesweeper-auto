@echo off
rem Locate a usable Python. Add your own python.exe path to the list below if needed.
set "PYTHONIOENCODING=utf-8"
set "PY="
set "PYW="
if exist "C:\Users\sp\anaconda3\python.exe" set "PY=C:\Users\sp\anaconda3\python.exe"
if not defined PY if exist "D:\software\Anaconda3\python.exe" set "PY=D:\software\Anaconda3\python.exe"
if not defined PY if exist "%LOCALAPPDATA%\Programs\Python\Python312\python.exe" set "PY=%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
if not defined PY if exist "%LOCALAPPDATA%\Programs\Python\Python311\python.exe" set "PY=%LOCALAPPDATA%\Programs\Python\Python311\python.exe"
if not defined PY for %%P in (python.exe) do if not defined PY set "PY=%%~$PATH:P"
if defined PY set "PYW=%PY:python.exe=pythonw.exe%"
if defined PYW if not exist "%PYW%" set "PYW=%PY%"