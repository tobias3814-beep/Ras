#!/bin/sh
# Builds the portable Windows download ON A MAC (or Linux): dist-windows/Ras-windows.zip
# Contents: official Windows embeddable Python 3.11 (python.org), Windows wheels of every backend package (PyPI, via uv),
# the backend source, the built dashboard, and the launcher scripts in windows/.
set -e
cd "$(dirname "$0")"
PYV=3.11.9
OUT=dist-windows/Ras
rm -rf dist-windows && mkdir -p "$OUT/runtime/python" "$OUT/runtime/app"
echo "==> Windows embeddable Python $PYV"
curl -fsSL -o dist-windows/python-embed.zip "https://www.python.org/ftp/python/$PYV/python-$PYV-embed-amd64.zip"
unzip -q dist-windows/python-embed.zip -d "$OUT/runtime/python" && rm dist-windows/python-embed.zip
# search path: stdlib zip, then our packages and app (relative to python.exe); "import site" enables site-packages
printf 'python311.zip\r\n.\r\n..\\site-packages\r\n..\\app\r\nimport site\r\n' > "$OUT/runtime/python/python311._pth"
echo "==> Windows packages (win_amd64 wheels)"
(cd backend && uv export --no-dev --no-hashes --format requirements-txt --no-emit-project -q > ../dist-windows/requirements.txt)
uv pip install -q --python-platform x86_64-pc-windows-msvc --python-version 3.11 --target "$OUT/runtime/site-packages" \
  -r dist-windows/requirements.txt
echo "==> backend + dashboard"
cp -R backend/jarvis backend/run_backend.py "$OUT/runtime/app/"
find "$OUT/runtime/app" -name __pycache__ -prune -exec rm -rf {} +
cp config.yaml "$OUT/runtime/config.yaml"
(cd app && npm run build --silent)
cp -R app/dist "$OUT/runtime/web"
cp windows/Ras.vbs windows/*.bat windows/README-WINDOWS.txt windows/LICENSE.txt "$OUT/"
cp assets/app-icon.png "$OUT/runtime/jarvis.png"
cp app/src-tauri/icons/icon.ico "$OUT/runtime/ras.ico"
echo "==> zip"
(cd dist-windows && rm -f requirements.txt && zip -qr -9 Ras-windows.zip Ras)
ls -lh dist-windows/Ras-windows.zip
