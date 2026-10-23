@echo off
setlocal
cd /d "%~dp0"
title CampusNet AutoLogin - Setup

set "PY="
where py >nul 2>nul
if not errorlevel 1 set "PY=py -3"
if not defined PY (
    where python >nul 2>nul
    if not errorlevel 1 set "PY=python"
)

if not defined PY goto :nopython

%PY% "%~dp0autologin.py" --setup
goto :done

:nopython
echo.
echo   [!] Python 3.8+ was not found on this computer.
echo.
echo       Please install it from:
echo         https://www.python.org/downloads/
echo.
echo       During installation, check "Add Python to PATH".
echo.

:done
echo.
pause
