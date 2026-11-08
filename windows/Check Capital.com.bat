@echo off
rem Tries your Capital.com key on Demo and Live and points Ras at the one that works. Close Ras first.
set "JARVIS_HOME=%APPDATA%\com.jarvis.station"
"%~dp0runtime\python\python.exe" -m jarvis.capital_check
pause
