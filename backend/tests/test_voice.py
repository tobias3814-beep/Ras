import asyncio
import json
import os
import pathlib
import time
from types import SimpleNamespace as NS

import httpx
import numpy as np
import pytest

from jarvis.bus import Bus
from jarvis.config import ROOT
from jarvis.station.store import Store
from jarvis.voice import announce
from jarvis.voice.assistant import Assistant, Claude, Ollama, Tools, clean
from jarvis.voice.engines import FRAME, Endpointer
from jarvis.voice.jarvis import Voice, sentences

# ---------- text helpers ----------

def test_sentences_keep_numbers_together():
    assert sentences("Gold is at 2,410.50. Stop at 2,400. Target 2,430!") == ["Gold is at 2,410.50.", "Stop at 2,400.", "Target 2,430!"]


def test_clean_strips_think_and_markdown():
    assert clean("<think>hmm</think>Gold is on **HOLD**.") == "Gold is on HOLD."


def test_endpointer_speech_then_silence_and_timeout():
    ep = Endpointer(silence_ms=400, max_s=10)
    loud = (np.sin(np.arange(FRAME) / 3) * 8000).astype(np.int16)
    quiet = np.zeros(FRAME, np.int16)
    for _ in range(5):
        ep.feed(quiet)
    assert not any(ep.feed(loud) for _ in range(10))
    assert [ep.feed(quiet) for _ in range(5)][-1] is True and ep.started
    idle = Endpointer(400, 10, start_timeout_s=0.4)
    assert [idle.feed(quiet) for _ in range(5)][-1] is True and not idle.started

# ---------- announcements ----------

INTRA = {"status": "active", "mode": "intraday", "symbol": "XAU_USD", "call": "BUY", "confidence": 74,
         "votes": [{"agent": "technical", "direction": "BUY"}, {"agent": "macro", "direction": "BUY"},
                   {"agent": "news", "direction": "HOLD"}],
         "levels": {"entry_low": 2410.0, "entry_high": 2414.0}}


def test_announce_intraday_matches_the_spec_example():
    text, prio, tone = announce.new_signal(INTRA)
    assert text == "Sir, gold flipped to BUY on the 1-hour, confidence 74. Macro and technicals agree, news is neutral. Entry zone 2,410 to 2,414."
    assert (prio, tone) == (2, "normal")


def test_announce_scalp_crypto_outcome_briefing():
    assert announce.new_signal({**INTRA, "mode": "scalp", "say": "Scalp buy gold at 2,412."}) == ("Scalp buy gold at 2,412.", 3, "normal")
    assert announce.new_signal({**INTRA, "mode": "crypto", "symbol": "perp:SOL/USDT:USDT", "say": None})[1] == 1
    assert announce.outcome({**INTRA, "status": "hit_tp", "result_r": 2.0})[0] == "Gold intraday hit its target, plus 2.0 R."
    assert announce.outcome(INTRA) is None
    assert announce.briefing({"kind": "morning", "text": "Good morning, sir."}) == ("Good morning, sir.", 2, "normal")
    assert announce.briefing({"kind": "weekly", "text": "x"}) is None


def test_risk_warning_once_per_rule():
    said: set[str] = set()
    call = {"mode": "scalp", "hold_why": ["Risk Officer veto — Cooldown after 2 straight losses — 30 min"], "warnings": []}
    first = announce.risk_warning(call, said)
    assert first[1:] == (4, "warning") and "cup of tea" in first[0]
    assert announce.risk_warning(call, said) is None
    breach = announce.risk_warning({"mode": "crypto", "hold_why": ["Risk Officer veto — Daily loss limit hit (-3.2R)"], "warnings": []}, said)
    assert breach[2] == "breach"

# ---------- priorities / quiet mode ----------

def _voice(tmp_path, **over):
    import yaml
    cfg = {**yaml.safe_load((ROOT / "config.yaml").read_text())["voice"], "enabled": False, **over}
    return Voice(Bus(), Store(tmp_path / "s.sqlite"), cfg, ROOT, "Asia/Bahrain")


def test_priority_and_mute_rules(tmp_path):
    v = _voice(tmp_path)  # min_priority 2
    assert not v.say("crypto", 1) and v.say("intraday", 2) and v.say("scalp", 3)
    v.set_quiet(minutes=20)
    assert v.muted() and not v.say("scalp", 3) and v.say("risk", 4) and v.say("reply", 5)
    v.set_quiet(unmute=True, min_priority=4)
    assert not v.muted() and not v.say("scalp", 3)
    assert _voice(tmp_path).min_priority == 4   # persisted
    order = []
    while not v.queue.empty():
        order.append(v.queue.get_nowait()[4])
    assert order == [5, 4, 3, 2]                # highest priority speaks first


def test_text_only_ask_speaks_reply_and_publishes(tmp_path):
    v = _voice(tmp_path)

    class FakeAssistant:
        async def ask(self, text):
            return f"echo {text}", "fake"

    v.assistant = FakeAssistant()
    reply, who = asyncio.run(v.ask("call on gold"))
    assert (reply, who) == ("echo call on gold", "fake") and v.queue.get_nowait()[3] == "echo call on gold"
    assert v.bus.snapshot["voice"]["heard"] == "call on gold"

# ---------- tools ----------

def _tools(tmp_path, cfg, quiet=lambda **k: k):
    bus = Bus()
    bus.publish({**INTRA, "type": "call", "created": int(time.time() * 1000), "hold_why": [], "watch": [], "warnings": [],
                 "risk": {"veto": False}, "votes": [{"agent": "technical", "direction": "BUY", "confidence": 70, "reasons": ["trend up"]}]},
                key="call:XAU_USD:intraday")
    bus.publish({"type": "board", "rows": [{"key": "perp:BTC/USDT:USDT", "base": "BTC", "market": "perp", "call": "HOLD", "score": 40,
                                            "decision": None}]}, key="board")
    return Tools(bus, Store(tmp_path / "s.sqlite"), cfg["capital"]["instruments"], cfg, quiet)


def test_tools_resolve_and_get_call(cfg, tmp_path):
    t = _tools(tmp_path, cfg)
    assert [t.resolve(x) for x in ("cable", "Gold", "dollar-yen", "EUR/USD", "bitcoin", "nonsense")] == \
        ["GBP_USD", "XAU_USD", "USD_JPY", "EUR_USD", "perp:BTC/USDT:USDT", None]
    c = t.run("get_call", {"instrument": "gold"})
    assert c["calls"]["intraday"]["call"] == "BUY" and c["calls"]["intraday"]["votes"][0]["why"] == "trend up"
    assert "error" in t.run("get_call", {"instrument": "nonsense"}) and "error" in t.run("nope", {})
    assert "error" in t.run("get_calendar", {"bogus": 1})   # bad args come back as an error, never raise


def test_risk_status_losing_streak(cfg, tmp_path):
    t = _tools(tmp_path, cfg)
    now = int(time.time() * 1000)
    for k, r in enumerate([1.0, -1.0, -1.0]):
        s = t.store.insert({"symbol": "EUR_USD", "mode": "scalp", "call": "BUY", "created": now - 1000 + k})
        t.store.close(s, "hit_sl" if r < 0 else "hit_tp", r, now - 500 + k)
    rs = t.risk_status()
    assert rs["scalp"] == {"signals_today": 3, "net_r_today": -1.0, "losing_streak": 2}

# ---------- assistant ----------

def test_local_mute_commands(cfg, tmp_path):
    seen = []
    a = Assistant(_tools(tmp_path, cfg, lambda **k: seen.append(k) or k), [], 6)
    assert asyncio.run(a.ask("Jarvis, mute for 20 minutes"))[1] == "local" and seen[-1]["minutes"] == 20
    asyncio.run(a.ask("be quiet for 2 hours"))
    assert seen[-1]["minutes"] == 120
    asyncio.run(a.ask("ok you can talk again"))
    assert seen[-1]["unmute"] is True


def test_provider_fallback_order(cfg, tmp_path):
    calls = []

    class Down:
        name = "claude"

        async def chat(self, h, t):
            raise RuntimeError("no key")

    class Local:
        name = "ollama"

        async def chat(self, h, t):
            return "<think>x</think>Gold is on **BUY**, sir."

    async def fb(name):
        calls.append(name)

    a = Assistant(_tools(tmp_path, cfg), [Down(), Local()], 6, fb)
    assert asyncio.run(a.ask("what's gold doing?")) == ("Gold is on BUY, sir.", "ollama") and calls == ["ollama"]
    assert a.history[-1]["content"] == "Gold is on BUY, sir."
    none = Assistant(_tools(tmp_path, cfg), [Down()], 6)
    assert asyncio.run(none.ask("hello"))[1] == "none" and none.history == []


def test_claude_tool_loop_and_request_shape(cfg, tmp_path):
    reqs = []

    class Msgs:
        async def create(self, **kw):
            reqs.append(kw)
            if len(reqs) == 1:
                return NS(stop_reason="tool_use", content=[NS(type="tool_use", id="t1", name="get_call", input={"instrument": "gold"})])
            return NS(stop_reason="end_turn", content=[NS(type="text", text="Gold is a BUY at 74, sir.")])

    c = Claude("claude-opus-5-5", "low")
    c.client = NS(beta=NS(messages=Msgs()))
    out = asyncio.run(c.chat([{"role": "user", "content": "gold?"}], _tools(tmp_path, cfg)))
    assert out == "Gold is a BUY at 74, sir."
    r = reqs[0]
    assert r["model"] == "claude-opus-5-5" and r["output_config"] == {"effort": "low"} and r["thinking"] == {"type": "adaptive"}
    assert r["fallbacks"] == "default" and r["betas"] == ["server-side-fallback-2026-07-01"]
    result = reqs[1]["messages"][-1]["content"][0]
    assert result["tool_use_id"] == "t1" and json.loads(result["content"])["calls"]["intraday"]["call"] == "BUY"


def test_ollama_tool_loop(cfg, tmp_path, monkeypatch):
    bodies = []
    import jarvis.voice.assistant as A
    monkeypatch.setattr(A, "total_memory", lambda: 32 * A.GB)  # a Mac big enough for the configured model
    monkeypatch.setattr(A, "free_memory", lambda: 20 * A.GB)

    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/api/tags":
            return httpx.Response(200, json={"models": [{"name": "qwen3:8b", "size": int(5.2 * A.GB)}]})
        body = json.loads(req.content)
        bodies.append(body)
        if len(bodies) == 1:
            return httpx.Response(200, json={"message": {"role": "assistant", "content": "", "tool_calls": [
                {"function": {"name": "get_call", "arguments": {"instrument": "gold"}}}]}})
        return httpx.Response(200, json={"message": {"role": "assistant", "content": "Gold: BUY."}})

    o = Ollama("qwen3:8b", "http://ollama")
    o.http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    assert asyncio.run(o.chat([{"role": "user", "content": "gold?"}], _tools(tmp_path, cfg))) == "Gold: BUY."
    assert bodies[0]["think"] is False and bodies[0]["tools"][0]["function"]["name"] == "get_call"
    assert bodies[1]["messages"][-1]["role"] == "tool" and "BUY" in bodies[1]["messages"][-1]["content"]

# ---------- real engines (opt-in: ~1 min, needs data/models) ----------

@pytest.mark.skipif(not os.environ.get("JARVIS_VOICE_TESTS"), reason="set JARVIS_VOICE_TESTS=1 to run the real models")
def test_round_trip_tts_wake_stt():
    from jarvis.voice.engines import STT, TTS, WakeWord, resample
    m = pathlib.Path(ROOT / "data" / "models")
    s, sr = TTS(m, "bm_george", 1.0).synth("Hey Jarvis. What's the call on gold?")
    x = resample(s, sr)
    pcm = np.concatenate([np.zeros(16000, np.int16), (np.clip(x, -1, 1) * 32767).astype(np.int16), np.zeros(16000, np.int16)])
    ww = WakeWord(m)
    assert max(ww.score(pcm[i:i + FRAME]) for i in range(0, len(pcm) - FRAME, FRAME)) > 0.5
    assert "gold" in STT(m, "base.en").transcribe(x).lower()


def test_robot_voice_changes_the_sound_but_keeps_length_and_level():
    from jarvis.voice.engines import robotize
    sr = 24_000
    t = np.arange(sr) / sr
    x = (0.5 * np.sin(2 * np.pi * 440 * t)).astype(np.float32)
    assert robotize(x, sr, 0.0) is x                          # 0 = natural voice
    y = robotize(x, sr, 0.6)
    assert len(y) == len(x) and y.dtype == np.float32
    assert np.abs(y).max() == pytest.approx(0.5, rel=1e-3)     # no clipping, same peak
    spec = np.abs(np.fft.rfft(y))                              # ring modulation adds sidebands around the tone
    assert spec[440 + 110] > 0.05 * spec[440] and spec[440 - 110] > 0.05 * spec[440]
    assert robotize(np.zeros(0, np.float32), sr, 0.6).size == 0


def test_pitch_up_raises_the_pitch_and_shortens_by_the_same_ratio():
    from jarvis.voice.engines import pitch_up
    sr = 24_000
    tone = np.sin(2 * np.pi * 220 * np.arange(sr) / sr).astype(np.float32)
    assert pitch_up(tone, 0) is tone
    up = pitch_up(tone, 12)  # an octave
    assert len(up) == sr // 2
    peak = np.abs(np.fft.rfft(up)).argmax() * sr / len(up)
    assert peak == pytest.approx(440, abs=3)
