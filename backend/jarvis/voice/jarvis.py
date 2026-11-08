"""The voice loop: wake word / push-to-talk -> STT -> assistant -> TTS, plus the alert queue with priorities and
quiet mode. Publishes {"type": "voice"} state for the avatar (idle / listening / thinking / speaking / alert)."""
import asyncio
import itertools
import logging
import pathlib
import re
import time
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo



from jarvis.bus import Bus
from jarvis.station.store import Store
from jarvis.voice import announce
from jarvis.voice.assistant import Assistant
from jarvis.voice.engines import RATE, STT, TTS, Audio, Endpointer, WakeWord

log = logging.getLogger("jarvis.voice")


def sentences(text: str) -> list[str]:
    """Split for streaming synthesis; keeps '2,410.50' and 'Mr.' intact."""
    parts = re.split(r"(?<=[.!?;])\s+(?=[A-Z“\"'])", text.strip())
    return [p for p in parts if p]


class Voice:
    def __init__(self, bus: Bus, store: Store, cfg: dict[str, Any], root: pathlib.Path, tz: str) -> None:
        self.bus, self.store, self.cfg, self.tz = bus, store, cfg, tz
        self.models = root / cfg["models_dir"]
        saved = store.get_setting("voice", {})
        self.min_priority: int = saved.get("min_priority", cfg["min_priority"])
        self.muted_until: int = saved.get("muted_until", 0)
        self.state, self.tone, self.ready, self.error = "idle", "normal", False, ""
        self.heard = self.reply = ""
        self.assistant: Assistant | None = None
        self.tts: TTS | None = None
        self.stt: STT | None = None
        self.wake: WakeWord | None = None
        self.audio: Audio | None = None
        self.queue: asyncio.PriorityQueue[tuple[int, int, float, str, int, str]] = asyncio.PriorityQueue()
        self._seq = itertools.count()
        self._ptt = False
        self._asking = 0  # questions in flight: the avatar shows "thinking" between spoken notices
        self._said_risk: set[str] = set()
        self._risk_day = ""
        self.start_ms = int(time.time() * 1000)

    # ---------- state ----------
    def publish(self, level: float = 0.0) -> None:
        self.bus.publish({"type": "voice", "state": self.state, "tone": self.tone, "level": round(level, 3),
                          "ready": self.ready, "error": self.error, "heard": self.heard, "reply": self.reply,
                          "min_priority": self.min_priority, "muted_until": self.muted_until,
                          "wake_word": "Hey Jarvis"}, key="voice")

    def _set(self, state: str, tone: str | None = None) -> None:
        self.state = state
        if tone:
            self.tone = tone
        self.publish()

    def set_quiet(self, minutes: int | None = None, unmute: bool = False, min_priority: int | None = None) -> dict[str, Any]:
        if unmute:
            self.muted_until = 0
        elif minutes:
            self.muted_until = int(time.time() * 1000) + int(minutes) * 60_000
        if min_priority is not None:
            self.min_priority = max(1, min(5, int(min_priority)))
        self.store.set_setting("voice", {"min_priority": self.min_priority, "muted_until": self.muted_until})
        self.publish()
        return {"muted_until": self.muted_until, "min_priority": self.min_priority}

    def muted(self) -> bool:
        return time.time() * 1000 < self.muted_until

    # ---------- speaking ----------
    def say(self, text: str, priority: int, tone: str = "normal") -> bool:
        """Queue speech. Replies (5) always speak; mute lets only risk warnings (4+) through."""
        if priority < 5 and (priority < self.min_priority or (self.muted() and priority < 4)):
            log.info("suppressed (p%s): %s", priority, text[:60])
            return False
        self.queue.put_nowait((-priority, next(self._seq), time.time(), text, priority, tone))
        return True

    async def _speak(self, text: str, tone: str) -> None:
        self.reply = text
        if self.tts is None or self.audio is None:  # text-only mode
            self._set("alert" if tone != "normal" else "speaking", tone)
            await asyncio.sleep(min(6, 0.06 * len(text)))
            return
        loop = asyncio.get_running_loop()
        level = lambda v: loop.call_soon_threadsafe(self.publish, v)  # noqa: E731
        parts = sentences(text)
        nxt = asyncio.create_task(asyncio.to_thread(self.tts.synth, parts[0]))
        self._set("alert" if tone != "normal" else "speaking", tone)
        for i in range(len(parts)):
            samples, sr = await nxt
            if i + 1 < len(parts):  # synthesise the next sentence while this one plays
                nxt = asyncio.create_task(asyncio.to_thread(self.tts.synth, parts[i + 1]))
            await asyncio.to_thread(self.audio.play, samples, sr, level)

    async def speaker(self) -> None:
        while True:
            _, _, t, text, priority, tone = await self.queue.get()
            if priority < 4 and time.time() - t > 90:  # stale alert: the moment has passed
                continue
            try:
                await self._speak(text, tone)
            except Exception as e:
                log.warning("speech failed: %r", e)
            if self.wake:
                self.wake.reset()  # don't wake on our own voice
            self._set("thinking" if self._asking else "idle", "normal")

    def stop(self) -> None:
        while not self.queue.empty():
            self.queue.get_nowait()
        if self.audio:
            self.audio.stop_playback()

    # ---------- listening ----------
    async def ask(self, text: str) -> tuple[str, str]:
        self.heard = text
        self._asking += 1
        self._set("thinking")
        try:
            reply, who = await self.assistant.ask(text) if self.assistant else ("Still waking up, sir.", "none")
        finally:
            self._asking -= 1
        self.say(reply, 5)
        if self.state == "thinking" and not self._asking:
            self._set("idle")
        return reply, who

    def ptt(self, down: bool) -> None:
        self._ptt = down

    async def listener(self) -> None:
        q = self.audio.frames
        ep: Endpointer | None = None
        by_ptt = False
        while True:
            frame = await q.get()
            if self.state in ("speaking", "alert", "thinking"):  # half duplex: never listen to ourselves
                ep = None
                continue
            if ep is None:
                if self._ptt or (self.wake and self.wake.score(frame) >= self.cfg["wake_threshold"]):
                    ep, by_ptt = Endpointer(self.cfg["silence_ms"], self.cfg["max_listen_seconds"]), self._ptt
                    self.heard = ""
                    self._set("listening")
                continue
            done = ep.feed(frame)
            if by_ptt:  # push-to-talk: listen until the key is released
                done = not self._ptt or len(ep.frames) >= ep.max_frames
            if done:
                audio, ep = ep.audio(), None
                if self.wake:
                    self.wake.reset()
                self._set("thinking")
                text = await asyncio.to_thread(self.stt.transcribe, audio) if len(audio) > RATE // 4 else ""
                text = re.sub(r"^(hey,?\s+)?(jarvis|ras)[,.!]?\s*", "", text, flags=re.I).strip()
                if text:
                    await self.ask(text)
                else:
                    self._set("idle")

    # ---------- alerts ----------
    async def announcer(self) -> None:
        q = self.bus.subscribe()
        while True:
            e = await q.get()
            try:
                if e.get("type") == "kill" and e.get("source") == "voice":
                    continue  # the spoken reply already covers it
                if e.get("type") == "call":
                    day = datetime.now(ZoneInfo(self.tz)).date().isoformat()
                    if day != self._risk_day:
                        self._said_risk, self._risk_day = set(), day
                if said := announce.classify(e, self._said_risk, self.start_ms):
                    self.say(*said)
            except Exception as ex:
                self.bus.failed("voice alerts", ex)

    # ---------- startup ----------
    def _load(self) -> None:
        """Heavy: ~30-60s on first start (plus the first-run model download). Runs in a thread."""
        from jarvis.voice import models
        if need := models.missing(self.models, self.cfg["whisper_model"]):
            log.info("voice models missing: %s", need)

            def progress(msg: str) -> None:
                self.error = msg
                self.publish()

            models.ensure(self.models, self.cfg["whisper_model"], progress)
            self.error = "loading voice models…"
            self.publish()
        self.tts = TTS(self.models, self.cfg["tts_voice"], self.cfg["tts_speed"], self.cfg.get("tts_robot", 0.0), self.cfg.get("tts_pitch", 0.0))
        self.stt = STT(self.models, self.cfg["whisper_model"])
        self.wake = WakeWord(self.models)

    async def run(self, assistant: Assistant) -> None:
        self.assistant = assistant
        tasks = [asyncio.create_task(self.speaker()), asyncio.create_task(self.announcer())]
        self.publish()
        if self.cfg.get("enabled", True):
            try:
                await asyncio.to_thread(self._load)
                dev = self.store.get_setting("voice_devices", {})  # picked in Settings, overrides config.yaml
                self.audio = Audio(asyncio.get_running_loop(), dev.get("input") or self.cfg.get("input_device"),
                                   dev.get("output") or self.cfg.get("output_device"))
                # opening the mic can block while macOS shows its permission prompt: never on the event loop
                await asyncio.wait_for(asyncio.to_thread(self.audio.start_input), timeout=30)
                tasks.append(asyncio.create_task(self.listener()))
                self.ready = True
            except TimeoutError:
                self.error = "voice off: microphone permission pending — allow it in System Settings › Privacy › Microphone, then restart"
                log.warning(self.error)
            except Exception as e:  # no mic, no models, no permission: keep text chat working
                self.error = f"voice off: {e}"
                log.warning("voice unavailable: %r", e)
        self.publish()
        await asyncio.gather(*tasks)


