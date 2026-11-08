"""Entry point for the frozen backend (PyInstaller). The installed app starts this with JARVIS_HOME set.
`jarvis-backend --keys` stores the API keys in the OS keychain; `jarvis-backend --selftest` checks the bundled speech engines (TTS -> wake word -> STT) without starting the server."""
import multiprocessing
import sys


def selftest() -> int:
    import asyncio

    import numpy as np
    import websockets

    from jarvis.feeds.capital import TLS, WS_URL

    async def tls() -> None:  # handshake only, no login
        async with websockets.connect(WS_URL, ssl=TLS, open_timeout=15):
            pass
    try:
        asyncio.run(tls())
        print("tls OK: Capital.com stream certificate verified")
    except Exception as e:
        print(f"tls FAILED: {e!r}")
        return 1

    from jarvis.config import ROOT, load_config
    from jarvis.voice.engines import FRAME, STT, TTS, WakeWord, resample
    cfg = load_config()["voice"]
    m = ROOT / cfg["models_dir"]
    s, sr = TTS(m, cfg["tts_voice"], 1.0).synth("Hey Jarvis. What's the call on gold?")
    x = resample(s, sr)
    pcm = np.concatenate([np.zeros(16000, np.int16), (np.clip(x, -1, 1) * 32767).astype(np.int16), np.zeros(16000, np.int16)])
    ww = WakeWord(m)
    wake = max(ww.score(pcm[i:i + FRAME]) for i in range(0, len(pcm) - FRAME, FRAME))
    text = STT(m, cfg["whisper_model"]).transcribe(x)
    ok = wake > 0.5 and "gold" in text.lower()
    print(f"selftest {'OK' if ok else 'FAILED'}: wake={wake:.3f} heard={text!r}")
    return 0 if ok else 1


if __name__ == "__main__":
    multiprocessing.freeze_support()
    if getattr(sys, "frozen", False):  # bundled Python has no system CA store: use the bundled one everywhere
        import os

        import certifi
        os.environ.setdefault("SSL_CERT_FILE", certifi.where())
    if "--selftest" in sys.argv:
        sys.exit(selftest())
    if "--keys" in sys.argv:  # the installed app has no Python: `jarvis-backend --keys` stores the API keys
        from jarvis.keys import main as keys_main
        keys_main()
        input("Done. Restart Ras to use the new keys. Press Enter to close.")
        sys.exit(0)
    import uvicorn

    from jarvis.main import app, cfg

    uvicorn.run(app, host=cfg["server"]["host"], port=cfg["server"]["port"], log_config=None)
