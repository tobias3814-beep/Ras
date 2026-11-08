"""First-run download of the voice models into <data>/models (same files and sources as the dev setup)."""
import logging
import pathlib
from collections.abc import Callable

import httpx

log = logging.getLogger("jarvis.voice.models")
KOKORO = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/"
KOKORO_FILES = ("kokoro-v1.0.onnx", "voices-v1.0.bin")


def missing(models: pathlib.Path, whisper: str) -> list[str]:
    out = [f"kokoro/{f}" for f in KOKORO_FILES if not (models / "kokoro" / f).exists()]
    if not (models / "wakeword" / "hey_jarvis_v0.1.onnx").exists():
        out.append("wakeword")
    if not any((models / "whisper").glob(f"models--*{whisper}*")):
        out.append(f"whisper {whisper}")
    return out


def ensure(models: pathlib.Path, whisper: str, progress: Callable[[str], None]) -> None:
    (models / "kokoro").mkdir(parents=True, exist_ok=True)
    for f in KOKORO_FILES:
        dst = models / "kokoro" / f
        if dst.exists():
            continue
        tmp = dst.with_suffix(dst.suffix + ".part")
        with httpx.stream("GET", KOKORO + f, follow_redirects=True, timeout=60) as r:
            r.raise_for_status()
            total, done = int(r.headers.get("content-length") or 0), 0
            with open(tmp, "wb") as out:
                for chunk in r.iter_bytes(1 << 20):
                    out.write(chunk)
                    done += len(chunk)
                    progress(f"downloading voice: {f} {done * 100 // total if total else 0}%")
        tmp.rename(dst)  # only a complete file gets the real name
    if not (models / "wakeword" / "hey_jarvis_v0.1.onnx").exists():
        progress("downloading wake word…")
        import openwakeword.utils
        openwakeword.utils.download_models(["hey_jarvis"], target_directory=str(models / "wakeword"))
    progress(f"downloading speech recognition ({whisper})…")
    from faster_whisper.utils import download_model
    download_model(whisper, cache_dir=str(models / "whisper"))
