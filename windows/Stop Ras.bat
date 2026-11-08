@echo off
rem Stops the Ras backend right away (normally it stops by itself a minute after you close the window).
powershell -NoProfile -Command "Get-Process pythonw -ErrorAction SilentlyContinue | Where-Object { $_.Path -like \"*\runtime\python\pythonw.exe\" } | Stop-Process"
echo Ras stopped.
timeout /t 2 >nul
