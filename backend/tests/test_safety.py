import asyncio
import os
import pathlib
import re
import subprocess
import sys

import numpy as np
import pandas as pd
import yaml

from jarvis import catchup
from jarvis.bus import Bus
from jarvis.config import BUNDLED
from jarvis.feeds import macro
from jarvis.guard import allowed
from jarvis.halt import halt
from jarvis.paper import Paper
from jarvis.station.context import Context, live_quote
from jarvis.station.store import Store
from jarvis.trend import TrendDesk

SRC = pathlib.Path(__file__).parents[1] / "jarvis"
NOW = 1_800_000_000
CFG = {"auto_follow": True, "fx": {"balance": 10_000, "risk_pct": 1.0, "currency": "USD", "slippage_pips": 0},
       "crypto": {"balance": 1000, "risk_pct": 1.0, "currency": "USDT", "max_leverage": 3}}
INS = {"XAU_USD": {"pip": 0.1}}


def paper(tmp_path):
    store = Store(tmp_path / "s.sqlite")
    p = Paper(Bus(), store, CFG, INS, {}, 0.0)
    p.set_rules({"XAU_USD": {}})
    p.on_quote("XAU_USD", 2000.0, 2000.4, 0)
    return p, store


def signal(store, call="BUY", stop=1900.0, tp=None, created=0):
    return store.insert({"symbol": "XAU_USD", "mode": "trend", "call": call, "created": created, "expires": 10**15,
                         "levels": {"entry": 2000.0, "stop": stop, "tp1": tp, "tp2": None}})


def bars(rows, start=NOW, step=900):
    o, h, lo, c = (np.array(x, float) for x in zip(*rows))
    return pd.DataFrame({"time": start + step * np.arange(len(rows)), "open": o, "high": h, "low": lo, "close": c, "volume": 1})


def test_the_real_app_survives_trend_and_the_kill_switch_sticks(tmp_path):
    """Trend signal -> alerts, journal, paper; kill; republish; resume: the bugs unit tests missed live in the wiring."""
    env = {**os.environ, "JARVIS_HOME": str(tmp_path)}
    r = subprocess.run([sys.executable, str(pathlib.Path(__file__).parent / "app_scenario.py")], env=env, capture_output=True,
                       text=True, timeout=120, cwd=tmp_path)
    assert r.returncode == 0 and "SCENARIO OK" in r.stdout, r.stdout[-2000:] + r.stderr[-3000:]


# ---------- paper-only guard ----------
def test_no_capital_com_order_code_exists():
    """Capital.com keys can always trade (there are no read-only keys), so the code must have no way to. The only write
    allowed is logging in. Anyone adding one has to change this test on purpose."""
    src = (SRC / "feeds" / "capital.py").read_text(encoding="utf-8")
    assert re.findall(r"\.(?:post|put|delete|patch)\(", src) == [".post("] and '/api/v1/session"' in src
    for f in SRC.rglob("*.py"):
        text = f.read_text(encoding="utf-8")
        assert not re.search(r"api/v1/(positions|workingorders|confirms)", text), f


def test_shipped_config_stays_on_the_demo_environment():
    assert yaml.safe_load((BUNDLED / "config.yaml").read_text(encoding="utf-8"))["capital"]["env"] == "demo"


# ---------- the kill switch and one decision per signal ----------
def test_paper_opens_nothing_while_halted(tmp_path):
    p, store = paper(tmp_path)
    halt.active = True
    try:
        p.on_signal(signal(store), 1)
        assert p.open == {}
    finally:
        halt.active = False


def test_a_closed_position_does_not_reopen_when_its_signal_is_republished(tmp_path):
    p, store = paper(tmp_path)
    s = signal(store)
    p.on_signal(s, 1)
    p.flatten("kill switch", 2)
    p.on_signal(s, 3)
    assert p.open == {}


def test_a_signal_not_taken_stays_not_taken(tmp_path):
    p, store = paper(tmp_path)
    p.set_rules({"XAU_USD": {"min_size": 1000, "step": 1}})
    s = signal(store)
    p.on_signal(s, 1)
    assert p.open == {}
    p.set_rules({"XAU_USD": {}})  # it would fit now, at a different price: too late
    p.on_signal(s, 2)
    assert p.open == {}




# ---------- offline catch-up ----------
def test_stop_passed_while_offline_closes_at_the_stop_with_the_bar_time(tmp_path):
    p, store = paper(tmp_path)
    p.on_signal(signal(store), NOW * 1000 - 1)
    df = bars([(2000, 2010, 1990, 2005), (2005, 2006, 1850, 1860)])
    assert catchup.replay("XAU_USD", {"MINUTE_15": df}, NOW * 1000, p, []) == 1
    [pos] = [x for x in store.positions() if x["closed"]]
    assert pos["exit"] == 1850 and pos["closed"] == (NOW + 900) * 1000 and "offline" in pos["reason"]


def test_a_gap_through_the_stop_fills_at_the_open(tmp_path):
    p, store = paper(tmp_path)
    p.on_signal(signal(store), NOW * 1000 - 1)
    catchup.replay("XAU_USD", {"MINUTE_15": bars([(1800, 1810, 1790, 1805)])}, NOW * 1000, p, [])
    assert [x for x in store.positions() if x["closed"]][0]["exit"] == 1800


def test_stop_counts_before_target_in_the_same_missed_bar(tmp_path):
    p, store = paper(tmp_path)
    p.on_signal(signal(store, tp=2100.0), NOW * 1000 - 1)
    catchup.replay("XAU_USD", {"MINUTE_15": bars([(2000, 2200, 1850, 2000)])}, NOW * 1000, p, [])
    assert [x for x in store.positions() if x["closed"]][0]["reason"].startswith("stop")


def test_nothing_missed_leaves_the_position_open_and_old_bars_are_ignored(tmp_path):
    p, store = paper(tmp_path)
    p.on_signal(signal(store), NOW * 1000 - 1)
    before = bars([(2000, 2000, 1500, 2000)], start=NOW - 900)  # before the gap: already seen live
    calm = bars([(2000, 2010, 1990, 2005)])
    assert catchup.replay("XAU_USD", {"MINUTE_15": pd.concat([before, calm], ignore_index=True)}, NOW * 1000, p, []) == 0
    assert len(p.open) == 1


def test_long_gaps_use_a_timeframe_that_reaches_back():
    m15 = bars([(1, 1, 1, 1)] * 3, start=NOW, step=900)
    day = bars([(1, 1, 1, 1)] * 5, start=NOW - 5 * 86_400, step=86_400)
    assert catchup.bars_since({"MINUTE_15": m15, "DAY": day}, (NOW - 86_400) * 1000).time.iloc[0] >= NOW - 86_400
    assert len(catchup.bars_since({"MINUTE_15": m15, "DAY": day}, (NOW - 86_400) * 1000)) == 1  # the one daily bar from the gap on
    assert catchup.bars_since({}, 0) is None


def test_trend_signal_is_stopped_by_catch_up_too(tmp_path):
    store = Store(tmp_path / "t.sqlite")
    d = TrendDesk(Bus(), store, INS, {}, {}, {"XAU_USD": {"trend": True}})
    s = signal(store)
    d.active["XAU_USD"] = s
    p, _ = paper(tmp_path)
    catchup.replay("XAU_USD", {"MINUTE_15": bars([(2000, 2000, 1850, 1860)])}, NOW * 1000, p,
                   [(d.active, lambda sym, b, a, ms: d.on_price({"symbol": sym, "bid": b, "ask": a}, ms))])
    assert d.active == {} and store.get(s["id"])["status"] == "hit_sl"


# ---------- stale data ----------
def test_an_old_quote_is_not_a_live_market():
    assert live_quote({"bid": 1, "ask": 2, "seen": NOW - 60}, NOW)
    assert not live_quote({"bid": 1, "ask": 2, "seen": NOW - 3600}, NOW)  # Friday's last tick
    assert not live_quote({}, NOW)


def test_trend_opens_nothing_on_a_stale_weekend_quote(tmp_path):
    c = np.linspace(1000, 2000, 300)
    o = np.r_[c[0], c[:-1]]
    day = pd.DataFrame({"time": NOW - 86_400 * np.arange(300)[::-1], "open": o, "high": np.maximum(o, c) * 1.002,
                        "low": np.minimum(o, c) * 0.998, "close": c, "volume": 1})
    prices = {"XAU_USD": {"bid": 2000.0, "ask": 2000.4, "seen": NOW - 3600}}
    d = TrendDesk(Bus(), Store(tmp_path / "t.sqlite"), INS, {"XAU_USD": {"DAY": day}}, prices, {"XAU_USD": {"trend": True}})
    d.tick(NOW)
    assert "XAU_USD" not in d.active and d.calls["XAU_USD"]["live_price"] is False


class FakeClient:
    def __init__(self, body=None, boom=None):
        self.body, self.boom = body, boom

    async def get(self, *_, **__):
        if self.boom:
            raise self.boom
        return self

    def raise_for_status(self):
        pass

    def json(self):
        return self.body


def run_calendar(monkeypatch, client, ctx):
    """Only the calendar job; the other two polls do nothing."""
    monkeypatch.setattr(macro.httpx, "AsyncClient", lambda **_: client)
    calls = {}

    async def every(_minutes, name, fn):
        if name == "calendar":
            await fn()
        calls[name] = True

    monkeypatch.setattr(macro, "_every", every)
    bus = Bus()
    asyncio.run(macro.run(bus, ctx, {"calendar_url": "x", "calendar_poll_minutes": 1, "yields_poll_minutes": 1, "cot_poll_hours": 1}, {}))
    return bus


def test_a_failed_or_empty_calendar_is_reported_and_keeps_known_events(monkeypatch):
    old = [{"title": "CPI"}]
    for client in (FakeClient(boom=RuntimeError("down")), FakeClient(body=[])):
        ctx = Context(calendar=list(old))
        bus = run_calendar(monkeypatch, client, ctx)
        assert ctx.calendar == old and bus.snapshot["status:calendar"]["state"] == "error"
        assert "news calendar" in bus.failures and not bus.healthy()


# ---------- faults ----------
def test_supervise_restarts_a_crashed_loop_and_reports_it(monkeypatch):
    bus, runs = Bus(), []
    real = asyncio.sleep
    monkeypatch.setattr(asyncio, "sleep", lambda _: real(0))

    async def flaky():
        runs.append(1)
        if len(runs) < 3:
            raise ValueError("boom")

    asyncio.run(bus.supervise("journal", flaky))
    assert len(runs) == 3 and bus.failures["journal"]["count"] == 2
    assert bus.snapshot["status:ras"]["state"] == "error"


def test_alerts_about_one_part_are_limited_but_all_are_counted():
    bus = Bus()
    async def go():
        q = bus.subscribe()
        for _ in range(5):
            bus.failed("journal", ValueError("x"))
        return [e for e in (q.get_nowait() for _ in range(q.qsize())) if e["type"] == "fault"]
    assert len(asyncio.run(go())) == 1 and bus.failures["journal"]["count"] == 5


def test_a_crashed_consumers_queue_does_not_pile_up():
    import gc
    bus = Bus()
    async def go():
        bus.subscribe()  # nobody holds it, as after a crash
        gc.collect()
        return len(bus._subs)
    assert asyncio.run(go()) == 0


# ---------- who may call the API ----------
def test_only_the_app_and_this_machine_may_call_in():
    assert allowed("127.0.0.1:18765", "tauri://localhost")
    assert allowed("127.0.0.1:18765", None)  # curl, /health checks
    assert allowed("your-mac.example-tailnet.ts.net", "tauri://localhost")  # the phone
    assert allowed("localhost:18765", "http://localhost:1420")
    assert allowed("[::1]", None)
    assert not allowed("127.0.0.1:18765", "https://evil.example")  # a web page
    assert not allowed("evil.example", None)  # DNS rebinding
    assert not allowed("evil.ts.net.example.com", None)
    assert not allowed(None, None)


def test_the_windows_dashboard_served_by_the_backend_may_call_it():
    assert allowed("127.0.0.1:18765", "http://127.0.0.1:18765")
    assert allowed("localhost:18765", "http://localhost:18765")
    assert not allowed("127.0.0.1:18765", "http://127.0.0.1:9999")  # another local page still can't


def test_the_windows_launchers_use_the_settings_the_backend_reads():
    from pathlib import Path
    w = Path(__file__).resolve().parents[2] / "windows"
    text = "".join(p.read_text(encoding="utf-8") for p in w.iterdir() if p.suffix in (".bat", ".vbs"))
    assert "JARVIS_HOME" in text and "JARVIS_EXIT_WHEN_IDLE" in text and "Ras_HOME" not in text


def test_text_files_are_read_and_written_as_utf8_so_windows_does_not_garble_them():
    import re
    bad = [f"{p.name}:{i}" for p in SRC.rglob("*.py") for i, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1)
           if re.search(r"\.(read_text|write_text)\(", line) and "encoding" not in line]
    assert not bad, bad
