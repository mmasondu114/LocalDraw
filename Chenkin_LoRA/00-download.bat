@echo off
setlocal
chcp 65001 >nul
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"
cd /d "%~dp0"
title Chenkin LoRA - Genshin and Arknights
where py >nul 2>nul
if not errorlevel 1 (
  py -3 -u "%~dp0download.py" %*
  goto finished
)
where python >nul 2>nul
if not errorlevel 1 (
  python -u "%~dp0download.py" %*
  goto finished
)
echo Python 3.9 or newer is required. Install Python, then run this BAT again.
set "download_result=2"
goto exit_script
:finished
set "download_result=%errorlevel%"
:exit_script
echo.
echo Exit code: %download_result%   0=complete   3=Civitai API Key required
echo Run this BAT again to resume. Keep all .aria2 files.
pause
exit /b %download_result%
