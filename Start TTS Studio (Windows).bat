@echo off
rem Double-click to set up TTS Studio (first time, or after an update) and start it.
rem Everything goes inside this folder: uv, its own Python, the voice model. To remove TTS Studio, delete the folder.
setlocal
title TTS Studio
cd /d "%~dp0"
set "HERE=%~dp0"
set "UV=%HERE%tools\uv\uv.exe"
set "UV_PYTHON_INSTALL_DIR=%HERE%tools\python"
set "UV_CACHE_DIR=%HERE%tools\cache"
set "PY=%HERE%.venv\Scripts\python.exe"
rem The set-up is current when its copy of requirements.txt matches this one; a changed list means install again.
set "DONE=%HERE%.venv\installed-requirements.txt"

if not exist "%DONE%" goto setup
fc /b "%HERE%requirements.txt" "%DONE%" >nul 2>&1
if errorlevel 1 goto setup
goto start

:setup
echo.
echo  Setting up TTS Studio. The first time takes a few minutes; after an update, less.
echo  Everything is installed inside this folder. To remove TTS Studio, delete the folder.
echo.

if exist "%UV%" goto have_uv
echo  [1 of 3] Downloading the installer, uv 0.13.5 ...
powershell -NoProfile -ExecutionPolicy Bypass -Command "$env:UV_UNMANAGED_INSTALL=$env:HERE + 'tools\uv'; irm https://astral.sh/uv/0.13.5/install.ps1 | iex"
if not exist "%UV%" goto failed
:have_uv

if exist "%PY%" goto have_python
echo  [2 of 3] Getting Python 3.12 for TTS Studio ...
"%UV%" venv --python 3.12 --managed-python --quiet "%HERE%.venv"
if errorlevel 1 goto failed
:have_python

echo  [3 of 3] Installing the voice engine, Kokoro ...
"%UV%" pip install --quiet --python "%PY%" -r "%HERE%requirements.txt"
if errorlevel 1 goto failed

copy /y "%HERE%requirements.txt" "%DONE%" >nul
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
echo  Setup didn't finish. If TTS Studio is open in another window, close that window first.
echo  Check your internet connection, then double-click this file again: it picks up where it stopped.
echo  If it keeps failing, copy the messages above into an issue at
echo  https://github.com/twogunrock/tts-studio/issues
echo.
pause
exit /b 1
