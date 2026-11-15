@echo off
rem MovieFilter GUI launcher for Windows (no console window). Runs without "pip install".
rem Set MOVIEFILTER_PYTHON to the full path of pythonw.exe to choose the interpreter.
rem Requires: Python 3.11+ (with tcl/tk), NumPy, FFmpeg
setlocal
if defined PYTHONPATH (set "PYTHONPATH=%~dp0src;%PYTHONPATH%") else (set "PYTHONPATH=%~dp0src")

set "PYGUI="
set "PYARGS="
if defined MOVIEFILTER_PYTHON (
    set "PYGUI=%MOVIEFILTER_PYTHON%"
    goto :launch
)
rem Resolve full paths first: "start" with a bare command name may fail to find it.
for /f "delims=" %%P in ('where pyw 2^>nul') do if not defined PYGUI set "PYGUI=%%P" & set "PYARGS=-3"
if defined PYGUI goto :launch
for /f "delims=" %%P in ('where pythonw 2^>nul') do if not defined PYGUI set "PYGUI=%%P"
if defined PYGUI goto :launch

echo Python (pyw.exe / pythonw.exe) was not found.
echo Install Python 3.11 or later from https://www.python.org/ ^(check "tcl/tk"^),
echo or set MOVIEFILTER_PYTHON to the full path of pythonw.exe.
pause
exit /b 1

:launch
start "" "%PYGUI%" %PYARGS% -m moviefilter gui %*
if errorlevel 1 (
    echo Failed to start: %PYGUI%
    pause
    exit /b 1
)
exit /b 0
