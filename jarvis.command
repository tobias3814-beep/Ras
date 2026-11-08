#!/bin/sh
# macOS launcher: double-click to open JARVIS (dev build)
cd "$(dirname "$0")/app" || exit 1
. "$HOME/.cargo/env" 2>/dev/null
[ -d node_modules ] || npm install
npm run app
