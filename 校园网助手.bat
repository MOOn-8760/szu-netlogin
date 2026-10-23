@echo off
setlocal
cd /d "%~dp0"
title CampusNet AutoLogin

set "PY="
where py >nul 2>nul
if not errorlevel 1 set "PY=py -3"
if not defined PY (
    where python >nul 2>nul
    if not errorlevel 1 set "PY=python"
)
if not defined PY goto :nopython

%PY% "%~dp0autologin.py" --menu
goto :done

:nopython
echo.
echo   [!] Python 3.8+ was not found on this computer.
echo       https://www.python.org/downloads/
echo.

:done
echo.
pause
