@echo off
rem MovieFilter launcher for Windows. Runs without "pip install".
rem   moviefilter apply in.mp4 -p sepia
rem Set MOVIEFILTER_PYTHON to the path of python.exe to choose the interpreter.
rem Requires: Python 3.11+, NumPy, FFmpeg
setlocal
if defined PYTHONPATH (set "PYTHONPATH=%~dp0src;%PYTHONPATH%") else (set "PYTHONPATH=%~dp0src")
if defined MOVIEFILTER_PYTHON (
    "%MOVIEFILTER_PYTHON%" -m moviefilter %*
) else (
    where py >nul 2>nul
    if not errorlevel 1 (
        py -3 -m moviefilter %*
    ) else (
        python -m moviefilter %*
    )
)
exit /b %ERRORLEVEL%
