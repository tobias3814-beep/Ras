# PyInstaller spec: one-folder build of the JARVIS backend -> dist/jarvis-backend/
# Build: uv run pyinstaller jarvis-backend.spec --noconfirm
from PyInstaller.utils.hooks import collect_all, collect_submodules

datas, binaries, hidden = [("../config.yaml", ".")], [], []
# packages that load data files / native libs at runtime
for pkg in ("kokoro_onnx", "espeakng_loader", "phonemizer", "language_tags", "segments", "csvw", "openwakeword",
            "faster_whisper", "ctranslate2", "onnxruntime", "_sounddevice_data", "tzdata", "certifi"):
    d, b, h = collect_all(pkg)
    datas += d; binaries += b; hidden += h
hidden += collect_submodules("ccxt") + collect_submodules("uvicorn") + collect_submodules("keyring.backends") + ["sounddevice"]

a = Analysis(["run_backend.py"], pathex=["."], binaries=binaries, datas=datas, hiddenimports=hidden,
             excludes=["tkinter", "matplotlib", "IPython", "pytest"], noarchive=False)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="jarvis-backend", console=True, upx=False)
coll = COLLECT(exe, a.binaries, a.datas, name="jarvis-backend", upx=False)
