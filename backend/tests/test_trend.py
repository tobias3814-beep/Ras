import numpy as np
import pandas as pd
import pytest

from jarvis.bus import Bus
from jarvis.halt import halt
from jarvis.paper import Paper
from jarvis.station.store import Store
from jarvis.trend import TrendDesk, score

INS = {"XAU_USD": {"pip": 0.1}, "EUR_USD": {"pip": 0.0001}}
NOW = 1_800_000_000


def daily(closes: list[float], end: int = NOW) -> pd.DataFrame:
    c = np.array(closes, float)
    o = np.r_[c[0], c[:-1]]
    return pd.DataFrame({"time": end - 86_400 * np.arange(len(c))[::-1], "open": o, "high": np.maximum(o, c) * 1.002,
                         "low": np.minimum(o, c) * 0.998, "close": c, "volume": 1})


def desk(tmp_path, modes=None):
    store = Store(tmp_path / "t.sqlite")
    frames, prices = {}, {}
    d = TrendDesk(Bus(), store, INS, frames, prices, modes or {"XAU_USD": {"trend": True}, "EUR_USD": {"trend": False}})
    return d, store, frames, prices


def test_score_is_the_average_sign_of_the_lookbacks():
    up = list(np.linspace(100, 200, 300))
    assert score(pd.Series(up)) == 1.0 and score(pd.Series(up[::-1])) == -1.0
    assert score(pd.Series(up[:200])) is None  # needs 252 days
    mixed = list(np.linspace(100, 200, 260)) + list(np.linspace(200, 180, 40))  # 1m and 3m down, 6m and 12m up
    assert score(pd.Series(mixed)) == 0.0


def test_opens_in_the_trend_with_a_3_atr_stop_and_no_target(tmp_path):
    d, store, frames, prices = desk(tmp_path)
    frames["XAU_USD"] = {"DAY": daily(list(np.linspace(1000, 2000, 300)))}
    prices["XAU_USD"] = {"bid": 2000.0, "ask": 2000.4}
    d.tick(NOW)
    s = d.active["XAU_USD"]
    assert s["call"] == "BUY" and s["mode"] == "trend" and s["levels"]["entry"] == 2000.4
    assert s["levels"]["tp1"] is None and s["levels"]["stop"] < 2000.4
    assert store.active()[0]["id"] == s["id"]
    d.tick(NOW + 60)  # same daily candle: nothing new
    assert len(store.active()) == 1


def test_flips_when_the_trend_turns_and_closes_the_old_one(tmp_path):
    d, store, frames, prices = desk(tmp_path)
    frames["XAU_USD"] = {"DAY": daily(list(np.linspace(1000, 2000, 300)))}
    prices["XAU_USD"] = {"bid": 2000.0, "ask": 2000.4}
    d.tick(NOW)
    first = d.active["XAU_USD"]["id"]
    frames["XAU_USD"] = {"DAY": daily(list(np.linspace(2000, 900, 301)), end=NOW + 86_400)}
    prices["XAU_USD"] = {"bid": 900.0, "ask": 900.4}
    d.tick(NOW + 86_400)
    assert d.active["XAU_USD"]["call"] == "SELL"
    old = store.get(first)
    assert old["status"] == "invalidated" and old["close_note"] == "daily trend flipped"


def test_stop_closes_it_and_mode_off_closes_it(tmp_path):
    d, store, frames, prices = desk(tmp_path)
    frames["XAU_USD"] = {"DAY": daily(list(np.linspace(1000, 2000, 300)))}
    prices["XAU_USD"] = {"bid": 2000.0, "ask": 2000.4}
    d.tick(NOW)
    stop = d.active["XAU_USD"]["levels"]["stop"]
    d.on_price({"symbol": "XAU_USD", "bid": stop - 1, "ask": stop - 0.6}, NOW * 1000 + 1)
    assert "XAU_USD" not in d.active and store.active() == []
    d.tick(NOW + 1)  # same candle, so it is not re-opened
    assert "XAU_USD" not in d.active
    d.last_bar.clear()
    d.tick(NOW + 2)  # like the tested rule, a stop means waiting for a flip even on later candles
    assert "XAU_USD" not in d.active and "Stopped out" in d.calls["XAU_USD"]["hold_why"][0]
    frames["XAU_USD"] = {"DAY": daily(list(np.linspace(2000, 900, 301)), end=NOW + 86_400)}
    d.tick(NOW + 86_400)
    assert d.active["XAU_USD"]["call"] == "SELL"
    d.modes["XAU_USD"]["trend"] = False
    d.tick(NOW + 3)
    assert "XAU_USD" not in d.active and store.active() == []


def test_disabled_markets_and_the_halt_open_nothing(tmp_path):
    d, store, frames, prices = desk(tmp_path)
    frames["EUR_USD"] = {"DAY": daily(list(np.linspace(1.0, 1.2, 300)))}
    prices["EUR_USD"] = {"bid": 1.2, "ask": 1.2001}
    d.tick(NOW)
    assert "EUR_USD" not in d.active
    frames["XAU_USD"] = {"DAY": daily(list(np.linspace(1000, 2000, 300)))}
    prices["XAU_USD"] = {"bid": 2000.0, "ask": 2000.4}
    halt.active = True
    try:
        d.tick(NOW)
        assert "XAU_USD" not in d.active
    finally:
        halt.active = False


def test_restart_picks_up_open_trend_positions(tmp_path):
    d, store, frames, prices = desk(tmp_path)
    frames["XAU_USD"] = {"DAY": daily(list(np.linspace(1000, 2000, 300)))}
    prices["XAU_USD"] = {"bid": 2000.0, "ask": 2000.4}
    d.tick(NOW)
    d2 = TrendDesk(Bus(), store, INS, frames, prices, d.modes)
    assert d2.active["XAU_USD"]["id"] == d.active["XAU_USD"]["id"]
    d2.tick(NOW + 60)  # same direction after a restart: kept, not re-opened
    assert len(store.active()) == 1


def test_paper_follows_a_trend_signal_without_a_target(tmp_path):
    d, store, frames, prices = desk(tmp_path)
    cfg = {"auto_follow": True, "fx": {"balance": 10_000, "risk_pct": 1.0, "currency": "USD", "slippage_pips": 0},
           "crypto": {"balance": 1000, "risk_pct": 1.0, "currency": "USDT", "max_leverage": 3}}
    paper = Paper(Bus(), store, cfg, INS, {}, 0.0)
    paper.set_rules({"XAU_USD": {}})
    frames["XAU_USD"] = {"DAY": daily(list(np.linspace(1000, 2000, 300)))}
    prices["XAU_USD"] = {"bid": 2000.0, "ask": 2000.4}
    paper.on_quote("XAU_USD", 2000.0, 2000.4, NOW * 1000)
    d.tick(NOW)
    s = d.active["XAU_USD"]
    paper.on_signal(s, NOW * 1000)
    [pos] = paper.open.values()
    assert pos["tp"] is None and pos["risk"] == pytest.approx(100.0)
    paper.on_quote("XAU_USD", 2500.0, 2500.4, NOW * 1000 + 10)  # far up: no target, still open
    assert pos["signal_id"] in paper.open


def test_paper_waits_for_dealing_rules_before_opening(tmp_path):
    """At start-up Ras re-announces open signals before Capital.com's dealing rules arrive: sizing then would skip the
    broker's minimum size and the risk cap. Paper waits and opens once the rules are in, with them applied."""
    store = Store(tmp_path / "w.sqlite")
    cfg = {"auto_follow": True, "fx": {"balance": 100, "risk_pct": 1.0, "currency": "USD", "slippage_pips": 0, "max_risk_pct": 2.0},
           "crypto": {"balance": 1000, "risk_pct": 1.0, "currency": "USDT", "max_leverage": 3}}
    paper = Paper(Bus(), store, cfg, {"XAG_USD": {"pip": 0.01}}, {}, 0.0)
    paper.on_quote("XAG_USD", 53.40, 53.42, 0)
    sig = {"id": 7, "symbol": "XAG_USD", "mode": "trend", "call": "SELL", "status": "active",
           "levels": {"entry": 53.40, "stop": 58.70, "tp1": None}}
    paper.on_signal(sig, 0)
    assert paper.open == {} and 7 in paper.waiting
    paper.set_rules({"XAG_USD": {"min_size": 1, "step": 0.1, "margin_pct": 10}}, 1)
    assert paper.open == {} and paper.waiting == {}  # minimum size 1 risks $5.30 = 5.3% of $100 > 2% cap: skipped
    paper.on_signal({**sig, "id": 8, "levels": {"entry": 53.40, "stop": 54.40, "tp1": None}}, 2)
    assert paper.open[8]["units"] == 1  # with the rules in, a trade that fits opens straight away


def test_market_closed_shows_the_call_but_trades_only_once_prices_return(tmp_path):
    d, store, frames, prices = desk(tmp_path)
    frames["XAU_USD"] = {"DAY": daily(list(np.linspace(1000, 2000, 300)))}
    d.tick(NOW)  # weekend: no quotes at all
    call = d.calls["XAU_USD"]
    assert call["call"] == "BUY" and call["live_price"] is False and "Market closed" in call["warnings"][0]
    assert call["levels"]["entry"] == pytest.approx(2000.0) and "XAU_USD" not in d.active and store.active() == []
    prices["XAU_USD"] = {"bid": 2001.0, "ask": 2001.4}  # Monday open, same daily candle
    d.tick(NOW + 60)
    assert d.active["XAU_USD"]["call"] == "BUY" and d.active["XAU_USD"]["levels"]["entry"] == 2001.4
