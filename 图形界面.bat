@echo off
setlocal
cd /d "%~dp0"
title CampusNet AutoLogin - GUI

set "PYW="
where pyw >nul 2>nul
if not errorlevel 1 set "PYW=pyw -3"
if not defined PYW (
    where pythonw >nul 2>nul
    if not errorlevel 1 set "PYW=pythonw"
)

if defined PYW (
    start "" %PYW% "%~dp0gui.py"
    exit /b 0
)

set "PY="
where py >nul 2>nul
if not errorlevel 1 set "PY=py -3"
if not defined PY (
    where python >nul 2>nul
    if not errorlevel 1 set "PY=python"
)
if not defined PY goto :nopython

%PY% "%~dp0gui.py"
goto :done

:nopython
echo.
echo   [!] Python 3.8+ was not found on this computer.
echo       https://www.python.org/downloads/
echo.
pause

:done
