@echo off
rem Store your API keys (Capital.com, Anthropic, Binance demo, Telegram) in Windows Credential Manager.
set "JARVIS_HOME=%APPDATA%\com.jarvis.station"
"%~dp0runtime\python\python.exe" -m jarvis.keys
echo.
echo Done. Restart Ras to use the new keys.
pause
