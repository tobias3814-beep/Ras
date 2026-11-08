"""The real app wiring with no network: run by test_safety.py in its own process, with JARVIS_HOME set to a temp folder."""
import asyncio
import os
import time

import numpy as np
import pandas as pd
from fastapi.testclient import TestClient

from jarvis.config import ROOT

assert str(ROOT) == os.environ["JARVIS_HOME"], "must never touch the real data folder"

from jarvis import main as m  # noqa: E402
from jarvis.halt import halt  # noqa: E402

SYM = "XAU_USD"
said: list[str] = []


def daily(closes: np.ndarray, end: float) -> pd.DataFrame:
    o = np.r_[closes[0], closes[:-1]]
    t = (end - end % 86_400) - 86_400 * np.arange(len(closes))[::-1]
    return pd.DataFrame({"time": t, "open": o, "high": np.maximum(o, closes) * 1.002, "low": np.minimum(o, closes) * 0.998,
                         "close": closes, "volume": 1})


async def main() -> None:
    m.voice.say = lambda text, *_: said.append(text)
    tasks = [asyncio.create_task(c()) for c in (m.paper.run, m.journal.run, m.trend_desk.run, m.voice.announcer, m.station.run, m.book.run)]
    await asyncio.sleep(0)
    now = time.time()
    m.paper.set_rules({SYM: {}})
    m.modes[SYM].update(trend=True, intraday=False, scalp=False)
    m.station.frames[SYM] = {"DAY": daily(np.linspace(1000, 2000, 300), now)}
    m.station.on_price({"symbol": SYM, "bid": 2000.0, "ask": 2000.4}, int(now * 1000))

    # a trend call and signal go through alerts, journal and paper without killing any of them
    m.trend_desk.evaluate(SYM, now)
    await asyncio.sleep(0.05)
    assert not [t for t in tasks if t.done()], [t.exception() for t in tasks if t.done()]
    sig = m.trend_desk.active[SYM]
    assert not m.bus.failures, m.bus.failures
    assert sig["id"] in m.paper.open, "paper followed the trend signal"
    assert m.store.journal([sig["id"]])[sig["id"]]["snapshot"], "journal saved its chart"
    assert any("Trend buy" in s for s in said), said
    assert not any("1-hour" in s for s in said), said

    # the kill switch closes everything, gives no outcome, and is logged
    await m.kill("test")
    await asyncio.sleep(0.05)
    assert m.paper.open == {} and m.store.active() == [] and m.trend_desk.active == {}
    closed = m.store.get(sig["id"])
    assert closed["status"] == "killed" and closed["result_r"] is None
    assert m.store.get_setting("control_log", [])[-1]["what"] == "kill"

    # a republished signal must not reopen, halted or after resume
    m.bus.publish({**sig, "type": "signal"})
    await asyncio.sleep(0.05)
    assert m.paper.open == {}
    client = TestClient(m.app, base_url="http://127.0.0.1")
    assert client.post("/api/resume").status_code == 200 and not halt.active
    m.bus.publish({**sig, "type": "signal"})
    await asyncio.sleep(0.05)
    assert m.paper.open == {}

    # the forward test runs on the real database; signals carry the settings that made them; tuning is frozen
    ft = client.get("/api/forward-test").json()
    assert ft["trials"] == 0 and ft["verdict"] == "collecting", ft
    assert all(r["live"]["trades"] == 0 for r in ft["rows"]) and ("trend", SYM) in [(r["mode"], r["symbol"]) for r in ft["rows"]]  # killed signals don't count, but the desk still shows
    assert "weights" in m.store.get(sig["id"])["stamp"] and m.learning.frozen

    # a broken account limit pauses new trades but leaves positions and signals alone, also across a restart's check
    assert m.watch.check(100.0, int(time.time() * 1000)) == ""
    assert "daily loss" in m.watch.check(90.0, int(time.time() * 1000))
    m.watch.reset(100.0, int(time.time() * 1000))

    # only the app may call in, and health reports faults
    assert client.get("/health", headers={"Origin": "tauri://localhost"}).json()["ok"] is True
    assert client.post("/api/resume", headers={"Origin": "https://evil.example"}).status_code == 403
    assert TestClient(m.app, base_url="http://evil.example").get("/health").status_code == 403
    assert TestClient(m.app, base_url="https://your-mac.example-tailnet.ts.net").get("/health").status_code == 200
    m.bus.publish({"type": "status", "feed": "capital", "state": "starting", "detail": ""}, key="status:capital")
    assert client.get("/health").json()["ok"] is False  # a feed that never came up, long after any fault has aged out
    m.bus.publish({"type": "status", "feed": "capital", "state": "live", "detail": ""}, key="status:capital")
    assert client.get("/health").json()["ok"] is True
    m.bus.failed("test part", ValueError("boom"))
    h = client.get("/health").json()
    assert h["ok"] is False and h["faults"]["test part"]["count"] == 1
    await asyncio.sleep(0.05)
    assert any("test part" in s for s in said), said
    for t in tasks:
        t.cancel()
    print("SCENARIO OK")


asyncio.run(main())
