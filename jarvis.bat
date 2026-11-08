@echo off
REM Windows launcher: double-click to open JARVIS (dev build)
cd /d "%~dp0app"
if not exist node_modules call npm install
npm run app
