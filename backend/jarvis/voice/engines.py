"""Local speech engines: openWakeWord ("hey jarvis"), faster-whisper STT, Kokoro TTS, sounddevice audio I/O.
All models live under data/models; nothing leaves the machine."""
import asyncio
import logging
import pathlib
import shutil
import threading
from collections.abc import Callable
from typing import Any

import numpy as np

log = logging.getLogger("jarvis.voice")
RATE = 16_000   # mic / wake word / whisper
FRAME = 1280    # 80 ms, what openWakeWord expects
DOMAIN_PROMPT = ("Hey Jarvis. Ras. Cable, euro, dollar-yen, gold, silver, oil, bitcoin, ether, solana. "
                 "Buy, sell, hold, scalp, intraday, stop, target, confidence, mute.")


def espeak_data_dir() -> str:
    """espeak-ng (used by Kokoro) fails on data paths containing spaces, e.g. '~/Library/Application Support'.
    If ours has one, copy the 19 MB data folder once to a space-free place."""
    import espeakng_loader
    src = pathlib.Path(espeakng_loader.get_data_path())
    if " " not in str(src):
        return str(src)
    for base in (pathlib.Path.home() / ".jarvis", pathlib.Path("C:/ProgramData/jarvis")):
        if " " in str(base):
            continue
        dst = base / "espeak-ng-data"
        if not (dst / "phontab").exists():
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copytree(src, dst, dirs_exist_ok=True)
        return str(dst)
    raise RuntimeError("no space-free folder for espeak-ng data; move the project to a path without spaces")


def robotize(samples: np.ndarray, sr: int, amount: float = 0.6, carrier_hz: float = 110.0, comb_ms: float = 5.0) -> np.ndarray:
    """Robot voice: ring modulation (the metallic buzz) blended in by `amount`, then a short comb filter (the ring of a
    helmet). 0 leaves the voice untouched; above about 0.8 words get harder to follow. Peak level is kept."""
    if amount <= 0 or len(samples) == 0:
        return samples
    amount = min(amount, 1.0)
    t = np.arange(len(samples)) / sr
    ring = samples * np.sin(2 * np.pi * carrier_hz * t)
    y = (1 - amount * 0.65) * samples + amount * 0.65 * ring
    d = max(1, int(sr * comb_ms / 1000))
    wet = y.copy()
    for k in range(1, 5):  # a few decaying repeats 5 ms apart
        wet[k * d:] += (0.45 * amount) ** k * y[:-k * d]
    peak, new = float(np.abs(samples).max()), float(np.abs(wet).max())
    return (wet * (peak / new if new > 0 else 1.0)).astype(np.float32)


def pitch_up(samples: np.ndarray, semitones: float) -> np.ndarray:
    """Raise pitch and formants together by resampling, which is what makes a voice sound younger and smaller.
    It also speeds the audio up by the same ratio, so TTS asks Kokoro for slower speech to cancel that."""
    if semitones == 0 or len(samples) == 0:
        return samples
    return np.interp(np.arange(0, len(samples), 2 ** (semitones / 12)), np.arange(len(samples)), samples).astype(np.float32)


class TTS:
    def __init__(self, models: pathlib.Path, voice: str, speed: float, robot: float = 0.0, pitch: float = 0.0) -> None:
        from kokoro_onnx import EspeakConfig, Kokoro
        self.k = Kokoro(str(models / "kokoro/kokoro-v1.0.onnx"), str(models / "kokoro/voices-v1.0.bin"),
                        espeak_config=EspeakConfig(data_path=espeak_data_dir()))
        self.voice, self.speed, self.robot, self.pitch = voice, speed, robot, pitch

    def synth(self, text: str) -> tuple[np.ndarray, int]:
        speed = max(0.5, self.speed / 2 ** (self.pitch / 12))  # pitch_up speeds it back up
        lang = "en-gb" if self.voice.startswith("b") else "en-us"
        samples, sr = self.k.create(text, voice=self.voice, speed=speed, lang=lang)
        return robotize(pitch_up(samples.astype(np.float32), self.pitch), sr, self.robot), sr


class STT:
    def __init__(self, models: pathlib.Path, name: str) -> None:
        from faster_whisper import WhisperModel
        self.m = WhisperModel(name, device="cpu", compute_type="int8", download_root=str(models / "whisper"))

    def transcribe(self, audio16k: np.ndarray) -> str:
        segs, _ = self.m.transcribe(audio16k, language="en", beam_size=1, vad_filter=True, initial_prompt=DOMAIN_PROMPT)
        return " ".join(s.text for s in segs).strip()


class WakeWord:
    def __init__(self, models: pathlib.Path) -> None:
        from openwakeword.model import Model
        d = models / "wakeword"
        self.m = Model(wakeword_models=[str(d / "hey_jarvis_v0.1.onnx")], inference_framework="onnx",
                       melspec_model_path=str(d / "melspectrogram.onnx"), embedding_model_path=str(d / "embedding_model.onnx"))

    def score(self, frame: np.ndarray) -> float:
        return float(max(self.m.predict(frame).values()))

    def reset(self) -> None:
        self.m.reset()


def resample(x: np.ndarray, sr: int, to: int = RATE) -> np.ndarray:
    if sr == to:
        return x.astype(np.float32)
    return np.interp(np.arange(0, len(x), sr / to), np.arange(len(x)), x).astype(np.float32)


def rms(x: np.ndarray) -> float:
    x = x.astype(np.float32) / (32768.0 if x.dtype == np.int16 else 1.0)
    return float(np.sqrt(np.mean(x * x))) if len(x) else 0.0


class Endpointer:
    """Ends an utterance after `silence_ms` of quiet once speech started (adaptive noise floor), or at max length."""

    def __init__(self, silence_ms: int, max_s: float, start_timeout_s: float = 5.0) -> None:
        self.need_quiet = silence_ms / 80
        self.max_frames, self.timeout = int(max_s / 0.08), int(start_timeout_s / 0.08)
        self.frames: list[np.ndarray] = []
        self.noise, self.started, self.quiet = 0.005, False, 0

    def feed(self, frame: np.ndarray) -> bool:
        """Returns True when the utterance is complete."""
        self.frames.append(frame)
        level = rms(frame)
        if not self.started:
            if level > max(0.015, self.noise * 3):
                self.started = True
            else:
                self.noise = 0.9 * self.noise + 0.1 * level
            return len(self.frames) >= self.timeout
        self.quiet = self.quiet + 1 if level < max(0.01, self.noise * 2) else 0
        return self.quiet >= self.need_quiet or len(self.frames) >= self.max_frames

    def audio(self) -> np.ndarray:
        return np.concatenate(self.frames).astype(np.float32) / 32768.0 if self.frames else np.zeros(0, np.float32)


class Audio:
    """Mic frames into an asyncio queue; speaker playback in a thread with a live level callback."""

    def __init__(self, loop: asyncio.AbstractEventLoop, input_device: Any = None, output_device: Any = None) -> None:
        import sounddevice as sd
        self.sd, self.loop = sd, loop
        self.input_device, self.output_device = input_device, output_device
        self.frames: asyncio.Queue[np.ndarray] = asyncio.Queue(maxsize=200)
        self._stop_playback = threading.Event()

    def _on_input(self, indata: np.ndarray, *_: Any) -> None:
        frame = indata[:, 0].copy()
        self.loop.call_soon_threadsafe(lambda: self.frames.full() or self.frames.put_nowait(frame))

    def start_input(self) -> None:
        self.stream = self.sd.InputStream(samplerate=RATE, channels=1, dtype="int16", blocksize=FRAME,
                                          device=self.input_device, callback=self._on_input)
        self.stream.start()

    def play(self, samples: np.ndarray, sr: int, on_level: Callable[[float], None]) -> None:
        """Blocking: run in a thread. Calls on_level ~20x/s for the avatar."""
        self._stop_playback.clear()
        chunk = sr // 20
        with self.sd.OutputStream(samplerate=sr, channels=1, dtype="float32", device=self.output_device) as out:
            for i in range(0, len(samples), chunk):
                if self._stop_playback.is_set():
                    break
                part = samples[i:i + chunk]
                on_level(min(1.0, rms(part) * 6))
                out.write(part.reshape(-1, 1))
        on_level(0.0)

    def stop_playback(self) -> None:
        self._stop_playback.set()
