@echo off
rem Double-click to set up (first time only) and start TTS Studio.
rem Everything goes inside this folder: uv, its own Python, the voice model. To remove TTS Studio, delete the folder.
setlocal
title TTS Studio
cd /d "%~dp0"
set "HERE=%~dp0"
set "UV=%HERE%tools\uv\uv.exe"
set "UV_PYTHON_INSTALL_DIR=%HERE%tools\python"
set "UV_CACHE_DIR=%HERE%tools\cache"
set "PY=%HERE%.venv\Scripts\python.exe"

if exist "%HERE%.venv\setup-done.txt" goto start

echo.
echo  Setting up TTS Studio. This happens once and takes a few minutes.
echo  Everything is installed inside this folder. To remove TTS Studio, delete the folder.
echo.

if exist "%UV%" goto have_uv
echo  [1 of 3] Downloading the installer, uv 0.13.5 ...
powershell -NoProfile -ExecutionPolicy Bypass -Command "$env:UV_UNMANAGED_INSTALL=$env:HERE + 'tools\uv'; irm https://astral.sh/uv/0.13.5/install.ps1 | iex"
if not exist "%UV%" goto failed
:have_uv

echo  [2 of 3] Getting Python 3.12 for TTS Studio ...
"%UV%" venv --python 3.12 --managed-python --quiet "%HERE%.venv"
if errorlevel 1 goto failed

echo  [3 of 3] Installing the voice engine, Kokoro ...
"%UV%" pip install --quiet --python "%PY%" -r "%HERE%requirements.txt"
if errorlevel 1 goto failed

echo Set up by "Start TTS Studio (Windows).bat"> "%HERE%.venv\setup-done.txt"
echo.
echo  Setup finished.

:start
echo.
"%PY%" "%HERE%app.py" --open
rem 3 = it was already running; the browser has been opened
if errorlevel 3 if not errorlevel 4 exit /b 0
echo.
echo  TTS Studio has stopped. You can close this window.
pause >nul
exit /b 0

:failed
echo.
echo  Setup didn't finish. Check your internet connection, then double-click this file again:
echo  it picks up where it stopped. If it keeps failing, copy the messages above into an issue at
echo  https://github.com/twogunrock/tts-studio/issues
echo.
pause
exit /b 1
