@echo off
setlocal

set "ROOT=%~dp0"
cd /d "%ROOT%"

where py >nul 2>&1
if errorlevel 1 (
    echo Python Launcher for Windows was not found. Install Python 3.11 or newer and try again.
    exit /b 1
)

if not exist ".venv\Scripts\python.exe" (
    echo Creating build environment...
    py -3 -m venv .venv
    if errorlevel 1 exit /b %errorlevel%
)

echo Installing build dependencies...
".venv\Scripts\python.exe" -m pip install --upgrade pip
if errorlevel 1 exit /b %errorlevel%

".venv\Scripts\python.exe" -m pip install PyQt6==6.11.0 Pillow==12.3.0 keyboard==0.13.5 mouse==0.7.1 pyinstaller==6.22.2
if errorlevel 1 exit /b %errorlevel%

echo Building Conjure Crosshair.exe...
".venv\Scripts\python.exe" -m PyInstaller --clean --noconfirm build.spec
if errorlevel 1 exit /b %errorlevel%

set "ISCC="
for %%P in ("%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe" "%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe" "%ProgramFiles%\Inno Setup 6\ISCC.exe") do if exist "%%~P" set "ISCC=%%~P"
if not defined ISCC for /f "delims=" %%P in ('where ISCC.exe 2^>nul') do if not defined ISCC set "ISCC=%%P"
if not defined ISCC (
    echo Inno Setup 6 was not found. It is required to create the installer.
    echo Install Inno Setup 6 and run this script again.
    exit /b 1
)

echo Creating installer...
if not exist "release" mkdir "release"
"%ISCC%" "/DMyOutputDir=release" installer.iss
if errorlevel 1 exit /b %errorlevel%

echo Installer is in release\Conjure Crosshair.exe
exit /b 0