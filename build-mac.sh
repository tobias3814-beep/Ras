#!/bin/sh
# Builds the installable macOS app: dist -> app/src-tauri/target/release/bundle/{macos/Ras.app, dmg/Ras_*.dmg}
# Needs: uv, Node 20+, Rust (rustup). Run from anywhere.
set -e
cd "$(dirname "$0")"
. "$HOME/.cargo/env" 2>/dev/null || true
echo "==> freezing the Python backend (PyInstaller)"
(cd backend && uv sync && uv run pyinstaller jarvis-backend.spec --noconfirm --log-level WARN)
echo "==> self-test of the frozen speech engines (needs data/models; skipped if absent)"
[ -d data/models/kokoro ] && JARVIS_HOME="$PWD" backend/dist/jarvis-backend/jarvis-backend --selftest || true
echo "==> building the app"
(cd app && npm ci && CI=true npx tauri build --bundles app,dmg)  # CI=true: skip Finder styling, which can leave the dmg "busy"
ls -lh app/src-tauri/target/release/bundle/dmg/*.dmg
