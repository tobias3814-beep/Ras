@echo off
rem Removes the Ras program and its shortcuts. Your data (%APPDATA%\com.jarvis.station) and saved keys are kept.
set "DEST=%LOCALAPPDATA%\Programs\Ras"
if exist "%DEST%\Stop Ras.bat" call "%DEST%\Stop Ras.bat" >nul 2>&1
del "%APPDATA%\Microsoft\Windows\Start Menu\Programs\Ras.lnk" 2>nul
powershell -NoProfile -Command "Remove-Item -LiteralPath (Join-Path ([Environment]::GetFolderPath('Desktop')) 'Ras.lnk') -ErrorAction SilentlyContinue"
cd /d "%TEMP%"
start "" /b cmd /c "timeout /t 2 >nul & rmdir /s /q \"%DEST%\""
echo Ras removed. Your journal, settings and keys are kept.
pause
