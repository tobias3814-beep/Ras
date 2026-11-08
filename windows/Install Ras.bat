@echo off
rem Installs Ras for this Windows user: copies it to %LOCALAPPDATA%\Programs\Ras and adds Start Menu and Desktop shortcuts.
rem No administrator rights needed. Your data stays in %APPDATA%\com.jarvis.station.
setlocal
set "DEST=%LOCALAPPDATA%\Programs\Ras"
echo Installing Ras to %DEST% ...
if exist "%DEST%\Stop Ras.bat" call "%DEST%\Stop Ras.bat" >nul 2>&1
robocopy "%~dp0." "%DEST%" /MIR /NFL /NDL /NJH /NJS /NP /XF "Install Ras.bat" >nul
if errorlevel 8 (
  echo Copying failed. Close Ras and try again.
  pause
  exit /b 1
)
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$s = New-Object -ComObject WScript.Shell;" ^
  "foreach ($dir in @([Environment]::GetFolderPath('Programs'), [Environment]::GetFolderPath('Desktop'))) {" ^
  "  $l = $s.CreateShortcut((Join-Path $dir 'Ras.lnk'));" ^
  "  $l.TargetPath = \"$env:SystemRoot\System32\wscript.exe\";" ^
  "  $l.Arguments = '\"' + $env:LOCALAPPDATA + '\Programs\Ras\Ras.vbs\"';" ^
  "  $l.WorkingDirectory = $env:LOCALAPPDATA + '\Programs\Ras';" ^
  "  $l.IconLocation = $env:LOCALAPPDATA + '\Programs\Ras\runtime\ras.ico';" ^
  "  $l.Description = 'Ras paper-trading bot'; $l.Save() }"
echo.
echo Ras is installed. Open it from the Start Menu or the Desktop icon "Ras".
if not exist "%APPDATA%\com.jarvis.station\keys-done" (
  echo.
  echo First time: enter your Capital.com keys now. Press Enter to skip any you don't have.
  call "%DEST%\Set up keys.bat"
  if not exist "%APPDATA%\com.jarvis.station" mkdir "%APPDATA%\com.jarvis.station"
  echo done> "%APPDATA%\com.jarvis.station\keys-done"
)
pause
