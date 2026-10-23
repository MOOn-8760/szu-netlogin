@echo off
setlocal enableextensions
if /i "%~1"=="--run" goto run

set "APPDIR=%~dp0"
if "%APPDIR:~-1%"=="\" set "APPDIR=%APPDIR:~0,-1%"

echo Cleaning szu-netlogin ...
taskkill /f /im szu-netlogin.exe >nul 2>&1
reg delete "HKCU\Software\Microsoft\Windows\CurrentVersion\Run" /v CampusNetAutoLogin /f >nul 2>&1
rmdir /s /q "%APPDATA%\campusnet" >nul 2>&1
rmdir /s /q "%APPDATA%\CampusNetAutoLogin" >nul 2>&1

set "SZUDIR=%APPDIR%"
copy /y "%~f0" "%TEMP%\szu-remove.bat" >nul 2>&1
if not exist "%TEMP%\szu-remove.bat" goto nocopy
start "" /min cmd /c "%TEMP%\szu-remove.bat" --run
exit /b 0

:nocopy
echo Cannot copy the cleanup script to TEMP.
echo Please delete this folder manually.
pause
exit /b 1

:run
cd /d "%TEMP%"
for /l %%i in (1,1,60) do (
  if not exist "%SZUDIR%" goto done
  rmdir /s /q "%SZUDIR%" >nul 2>&1
  ping -n 2 127.0.0.1 >nul 2>&1
)
:done
del "%~f0" >nul 2>&1
exit /b 0
