"""Render the Ras helmet (src/avatar/shell.svg + faces.json) to PNG with headless Chrome.
  python3 scripts/make_icon.py icon out.png      the 1024 px app icon source (transparent background)
  python3 scripts/make_icon.py sheet out.png     every expression, for checking the art
Then:  npx tauri icon out.png"""
import json
import pathlib
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent / "src" / "avatar"
CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
GREEN = "#a6f52b"
NOSE = "<path d='M240 292 L256 276 L272 292' stroke-width='7' />"


def helmet(face: str, ids: str = "") -> str:
    faces = json.loads((ROOT / "faces.json").read_text())
    style = f"<g fill='none' stroke='{GREEN}' stroke-width='13' stroke-linecap='round' stroke-linejoin='round' filter='url(#ras-neon)'>{faces[face]}{NOSE}</g>"
    svg = (ROOT / "shell.svg").read_text().replace("<!-- FACE -->", style).replace("class='fill'", f"fill='{GREEN}' stroke='none'")
    return svg.replace("ras-", f"ras{ids}-")  # a page with several helmets needs unique gradient ids


def shot(html: str, out: str, w: int, h: int) -> None:
    with tempfile.NamedTemporaryFile("w", suffix=".html", delete=False) as f:
        f.write(f"<!doctype html><html><body style='margin:0;background:transparent'>{html}</body></html>")
    subprocess.run([CHROME, "--headless=new", "--disable-gpu", "--hide-scrollbars", "--force-device-scale-factor=1",
                    "--default-background-color=00000000", f"--window-size={w},{h}", f"--screenshot={out}", f"file://{f.name}"],
                   check=True, capture_output=True, timeout=60)


if __name__ == "__main__":
    kind, out = sys.argv[1], sys.argv[2]
    if kind == "icon":
        shot(f"<div style='width:1024px;height:1024px'>{helmet('signature')}</div>", out, 1024, 1024)
    else:
        names = list(json.loads((ROOT / "faces.json").read_text()))
        cells = "".join(f"<div style='width:230px;font:13px sans-serif;text-align:center'>{helmet(n, str(i))}{n}</div>" for i, n in enumerate(names))
        shot(f"<div style='display:flex;flex-wrap:wrap;width:1180px;background:#f6f2fd'>{cells}</div>", out, 1180, 800)
