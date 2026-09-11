@echo off
setlocal EnableExtensions
cd /d "%~dp0"

echo JobMatch first launch may download Python and extras. Leave this window open.
echo.

set "PATH=%USERPROFILE%\.local\bin;%USERPROFILE%\.cargo\bin;%PATH%"
where uv >nul 2>&1
if errorlevel 1 (
  echo Installing the JobMatch engine...
  powershell -NoProfile -ExecutionPolicy Bypass -Command "irm https://astral.sh/uv/install.ps1 | iex"
  set "PATH=%USERPROFILE%\.local\bin;%USERPROFILE%\.cargo\bin;%PATH%"
)

where uv >nul 2>&1
if errorlevel 1 (
  echo Could not install uv. Check your internet connection and try again.
  pause
  exit /b 1
)

uv python install 3.12
if errorlevel 1 goto :fail
uv sync
if errorlevel 1 goto :fail
uv run playwright install chromium
if errorlevel 1 goto :fail
uv run jobmatch app
echo.
echo JobMatch stopped.
pause
exit /b 0

:fail
echo JobMatch could not finish setup.
pause
exit /b 1
