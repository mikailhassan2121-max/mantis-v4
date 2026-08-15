@echo off
setlocal
cd /d "%~dp0"

set "MANTIS_PYTHON=%~dp0.venv\Scripts\python.exe"
if exist "%MANTIS_PYTHON%" goto run

where py >nul 2>nul
if not errorlevel 1 (
  py -3 mantis_v4_live.py %*
  exit /b %errorlevel%
)

where python >nul 2>nul
if errorlevel 1 (
  echo Python was not found. Run setup_mantis.ps1 after installing Python 3.11 or newer.
  exit /b 2
)
set "MANTIS_PYTHON=python"

:run
"%MANTIS_PYTHON%" mantis_v4_live.py %*
exit /b %errorlevel%
