import asyncio

import numpy as np
import pandas as pd
import pytest

from jarvis import timemachine as tm
from jarvis.bus import Bus
from jarvis.station.store import Store

DAY = 86_400
T0 = 1_800_000_000 - 1_800_000_000 % DAY  # a midnight UTC


def bars(days: int, bar_s: int = 60, drift: float = 0.0, noise: float = 0.001, wick: float = 0.0005, seed: int = 3,
         start: float = 100.0) -> pd.DataFrame:
    """Contiguous synthetic candles from T0: log-return drift + noise per bar, symmetric wicks."""
    n = days * DAY // bar_s
    rng = np.random.default_rng(seed)
    close = start * np.exp(np.cumsum(drift + noise * rng.standard_normal(n)))
    open_ = np.r_[start, close[:-1]]
    w = np.abs(wick * rng.standard_normal(n)) * close
    return pd.DataFrame({"time": T0 + bar_s * np.arange(n), "open": open_, "high": np.maximum(open_, close) + w,
                         "low": np.minimum(open_, close) - w, "close": close, "volume": 1})


def run(df, side=1, entry=100.0, stop=99.5, tp1=100.5, horizon=60, cost=0.0, **kw):
    return tm.simulate(df, 60, side, entry, stop, tp1, float(df.time.iloc[-1] + 60), horizon, cost, **kw)


def test_same_seed_same_numbers_and_odds_add_up():
    df = bars(30)
    a, b = run(df), run(df)
    assert a == b
    assert a["p_tp1"] + a["p_stop"] + a["p_neither"] == pytest.approx(1.0, abs=1e-3)
    assert a["replays"] == 10_000 and a["seed"] == 19
    assert run(df, seed=20)["p_tp1"] != a["p_tp1"]


def test_uptrend_favours_a_buy_and_hurts_a_sell():
    df = bars(30, drift=0.0004)
    buy = run(df)
    sell = run(df, side=-1, stop=100.5, tp1=99.5)
    assert buy["p_tp1"] > 0.7 and buy["lean"] == "good" and buy["verdict"].startswith("Strongly toward TP1")
    assert sell["p_stop"] > 0.7 and sell["lean"] == "bad" and "toward the stop" in sell["verdict"]


def test_bar_touching_both_counts_as_the_stop():
    df = bars(30)
    df["close"] = df["open"] = 100.0
    df["high"], df["low"] = 102.0, 98.0
    o = run(df, stop=99.0, tp1=101.0)
    assert o["p_stop"] == 1.0 and o["p_tp1"] == 0.0


def test_flat_market_stays_open_and_costs_come_off_r():
    df = bars(30, noise=0.0, wick=0.0)
    o = run(df, cost=0.05)  # risk 0.5 -> 0.1R of costs
    assert o["p_neither"] == 1.0
    assert o["cost_r"] == pytest.approx(0.1) and o["avg_r"] == pytest.approx(-0.1)
    assert o["lean"] == "flat" and o["verdict"].startswith("Rarely reaches TP1 or the stop")


def test_breakeven_needs_more_wins_after_costs():
    assert tm.breakeven(1.0, 0.0) == pytest.approx(0.5)
    assert tm.breakeven(2.0, 0.0) == pytest.approx(1 / 3)
    assert tm.breakeven(1.0, 0.1) == pytest.approx(0.55)


def test_verdict_bands_and_notes():
    base = {"r_tp1": 1.0, "cost_r": 0.0, "avg_r": 0.01, "p_neither": 0.2}
    assert tm.verdict({**base, "p_tp1": 0.41, "p_stop": 0.39})["verdict"].startswith("Close to a coin flip")
    assert tm.verdict({**base, "r_tp1": 2.0, "p_tp1": 0.27, "p_stop": 0.53})["verdict"].startswith("Close to break-even")
    lean = tm.verdict({**base, "p_tp1": 0.48, "p_stop": 0.32})
    assert lean["verdict"].startswith("Leans toward TP1") and lean["lean"] == "good"
    v = tm.verdict({**base, "p_tp1": 0.10, "p_stop": 0.40, "p_neither": 0.5, "avg_r": 0.2})
    assert v["lean"] == "bad" and len(v["notes"]) == 2  # mostly open at expiry, and avg R points the other way


def test_pools_keep_short_gaps_skip_weekends_and_wrap_midnight():
    df = bars(4)
    df = df[(df.time < T0 + DAY // 2) | (df.time >= T0 + DAY // 2 + 3600)]  # an hour missing on day 1
    df = df[(df.time < T0 + 2 * DAY) | (df.time >= T0 + 3 * DAY)].reset_index(drop=True)  # a "weekend" day missing
    ratios, pool, traded = tm.pools(df, 60, 2)
    assert len(ratios) == len(df) - 2  # only the very first bar and the one after the long gap lack a previous close
    assert len(pool) == 1440 and len(pool[0]) == 5 * 3 - 2  # slots 1438..2 on 3 days, less the first bar of day 1 and day 4
    assert traded.all()


def test_daily_break_is_skipped_not_an_error():
    """Metals / oil: no bars 21:00-22:00 UTC every day. A 12h signal across it still gets odds."""
    df = bars(60, bar_s=900)
    df = df[((df.time % DAY) < 21 * 3600) | ((df.time % DAY) >= 22 * 3600)].reset_index(drop=True)
    _, _, traded = tm.pools(df, 900, 2)
    assert (~traded).sum() == 4
    start = float(df.time.iloc[-1] + 900)
    start += (18 * 3600 - start % DAY) % DAY  # 18:00 UTC: the next 12h cross the break
    o = tm.simulate(df, 900, 1, 100.0, 99.0, 101.0, start, 48)
    assert "error" not in o and o["bars"] == 44
    assert "closed" in tm.simulate(df, 900, 1, 100.0, 99.0, 101.0, start + 3 * 3600, 4)["error"]


def test_refuses_thin_history_and_bad_levels():
    assert "not enough history" in run(bars(3))["error"]
    assert "wrong side" in run(bars(30), stop=100.5)["error"]
    assert "expires" in run(bars(30), horizon=0)["error"]


def test_cost_px_matches_the_backtester(cfg):
    fx = {"mode": "scalp", "symbol": "EUR_USD", "spread": 0.0001, "levels": {"entry": 1.1}}
    pip = cfg["capital"]["instruments"]["EUR_USD"]["pip"]
    assert tm.cost_px(fx, cfg) == pytest.approx(0.0001 + 2 * cfg["paper"]["fx"]["slippage_pips"] * pip)
    sc = cfg["crypto"]["scanner"]
    cr = {"mode": "crypto", "symbol": "perp:ETH/USDT:USDT", "market": "perp", "spread": 0.1, "levels": {"entry": 2000.0}}
    assert tm.cost_px(cr, cfg) == pytest.approx(0.1 + 2 * (sc["slippage_pct"] + sc["fees_pct"]["perp"]) / 100 * 2000)


def _signal(created_s: float) -> dict:
    return {"symbol": "perp:ETH/USDT:USDT", "mode": "crypto", "market": "perp", "call": "BUY", "created": int(created_s * 1000),
            "expires": int((created_s + 3600) * 1000), "spread": 0.0,
            "levels": {"entry": 100.0, "stop": 99.5, "tp1": 100.5, "tp2": 101.0}}


def test_service_adds_odds_saves_and_republishes(tmp_path, cfg):
    df = bars(30)
    created = float(df.time.iloc[-1] + 60)
    store, bus = Store(tmp_path / "s.sqlite"), Bus()
    sig = store.insert(_signal(created))
    live = {sig["id"]: sig}
    seen_bars = []

    async def load(symbol, tf, days):
        seen_bars.append((symbol, tf))
        return pd.concat([df, bars(1, start=500.0).assign(time=lambda d: d.time + int(created) - T0)])  # + bars after the signal

    machine = tm.TimeMachine(bus, store, live.get, cfg, load)
    q = bus.subscribe()
    asyncio.run(machine.attach(sig["id"]))

    assert seen_bars == [("perp:ETH/USDT:USDT", "1m")]
    assert sig["odds"]["replays"] == 10_000 and "verdict" in sig["odds"]
    assert sig["odds"] == run(df, cost=tm.cost_px(sig, cfg))  # bars after the signal were ignored
    assert store.get(sig["id"])["odds"] == sig["odds"]
    published = [q.get_nowait()["odds"] for _ in range(q.qsize())]
    assert published[0] == {"pending": True} and published[-1] == sig["odds"]


def test_service_keeps_odds_for_a_signal_closed_meanwhile_and_reports_errors(tmp_path, cfg):
    store, bus = Store(tmp_path / "s.sqlite"), Bus()
    sig = store.insert(_signal(T0 + 30 * DAY))
    live = {sig["id"]: sig}

    async def closing_load(symbol, tf, days):
        live.pop(sig["id"])  # TP hit while the replays ran
        return bars(30)

    asyncio.run(tm.TimeMachine(bus, store, live.get, cfg, closing_load).attach(sig["id"]))
    assert "verdict" in store.get(sig["id"])["odds"]

    sig2 = store.insert(_signal(T0 + 30 * DAY))

    async def empty(symbol, tf, days):
        return pd.DataFrame()

    asyncio.run(tm.TimeMachine(bus, store, {sig2["id"]: sig2}.get, cfg, empty).attach(sig2["id"]))
    assert sig2["odds"]["error"].startswith("no saved 1m candles")


def test_run_loop_only_picks_up_new_active_signals(tmp_path, cfg):
    store, bus = Store(tmp_path / "s.sqlite"), Bus()
    machine = tm.TimeMachine(bus, store, lambda sid: None, cfg)
    started = []

    async def fake_attach(sid):
        started.append(sid)

    machine.attach = fake_attach

    async def go():
        task = asyncio.create_task(machine.run())
        await asyncio.sleep(0)
        lv = {"entry": 1, "stop": 0.9, "tp1": 1.1}
        for e in ({"id": 1, "status": "active", "levels": lv}, {"id": 1, "status": "active", "levels": lv},
                  {"id": 2, "status": "hit_tp", "levels": lv}, {"id": 3, "status": "active", "levels": lv, "odds": {"p_tp1": 0.5}},
                  {"id": 4, "status": "active", "levels": lv, "odds": {"pending": True}},
                  {"id": 5, "status": "active", "levels": lv, "odds": {"error": "not enough history"}}):
            bus.publish({**e, "type": "signal", "symbol": "X", "mode": "scalp"})
        await asyncio.sleep(0.01)
        task.cancel()

    asyncio.run(go())
    assert started == [1, 4, 5]  # once each; closed and done ones skipped; pending or failed before a restart are redone
