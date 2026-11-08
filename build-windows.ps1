# Builds the Windows installers: app\src-tauri\target\release\bundle\{nsis\JARVIS_*-setup.exe, msi\JARVIS_*.msi}
# Run in PowerShell on a Windows 10/11 PC:   powershell -ExecutionPolicy Bypass -File .\build-windows.ps1
# Needs (install once if missing):
#   uv    : powershell -c "irm https://astral.sh/uv/install.ps1 | iex"
#   Node  : https://nodejs.org (LTS)
#   Rust  : https://rustup.rs  (choose the MSVC toolchain; it offers to install the Visual Studio C++ Build Tools)
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot
foreach ($tool in "uv", "npm", "cargo") {
  if (-not (Get-Command $tool -ErrorAction SilentlyContinue)) { Write-Host "Missing '$tool' - see the header of this script."; exit 1 }
}
Write-Host "==> freezing the Python backend (PyInstaller)"
Push-Location backend
uv sync --python 3.11
uv run pyinstaller jarvis-backend.spec --noconfirm --log-level WARN
if (Test-Path ..\data\models\kokoro) { $env:JARVIS_HOME = (Resolve-Path ..).Path; .\dist\jarvis-backend\jarvis-backend.exe --selftest; Remove-Item Env:JARVIS_HOME }
Pop-Location
Write-Host "==> building the installers"
Push-Location app
npm ci
npx tauri build --bundles nsis,msi
Pop-Location
Get-ChildItem app\src-tauri\target\release\bundle -Recurse -Include *-setup.exe, *.msi | Select-Object FullName, Length
