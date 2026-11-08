import numpy as np
import pandas as pd

from conftest import NOW, frame, frames
from jarvis.bus import Bus
from jarvis.feeds.capital import parse_prices
from jarvis.indicators import adx, atr, ema, rsi
from jarvis.station.agents import RiskReport, Vote, digits, risk_officer, technical
from jarvis.station.commander import decide
from jarvis.station.engine import Station
from jarvis.station.store import Store, blended, check_invalidation, check_outcome

PIP = 0.01
INS = {"pip": PIP, "max_spread": 4.0, "ccy": ["USD"]}

# ---------- indicators ----------

def test_ema_of_constant_is_constant():
    assert np.allclose(ema(pd.Series([5.0] * 50), 21), 5.0)


def test_rsi_extremes():
    assert rsi(pd.Series(np.arange(1, 60, dtype=float))).iloc[-1] == 100
    assert rsi(pd.Series(np.arange(60, 1, -1, dtype=float))).iloc[-1] < 1


def test_atr_constant_range():
    df = pd.DataFrame({"high": [11.0] * 50, "low": [10.0] * 50, "close": [10.5] * 50})
    assert abs(atr(df).iloc[-1] - 1.0) < 1e-9


def test_adx_trend_vs_chop():
    trend = frame(300, 3600, 0.5, 0.1)
    x = np.arange(300)
    c = 100 + np.sin(x / 2)
    chop = pd.DataFrame({"high": c + 0.5, "low": c - 0.5, "close": c})
    assert adx(trend).iloc[-1] > 40 and adx(chop).iloc[-1] < 20


def test_digits():
    assert (digits(0.0001), digits(0.01), digits(0.1)) == (5, 3, 2)

# ---------- technical agent ----------

def test_technical_uptrend_buy_with_ordered_levels(cfg):
    f = frames(0.05)
    v = technical(f["HOUR_4"], f["HOUR"], f["MINUTE_15"], PIP, cfg["station"])
    lv = v.levels
    assert v.direction == "BUY" and v.confidence >= 40
    assert lv["stop"] < lv["entry_low"] < lv["entry"] < lv["entry_high"] < lv["tp1"] < lv["tp2"]
    assert lv["stop"] <= v.invalidation < lv["entry"]


def test_technical_downtrend_sell(cfg):
    f = frames(-0.05)
    v = technical(f["HOUR_4"], f["HOUR"], f["MINUTE_15"], PIP, cfg["station"])
    assert v.direction == "SELL" and v.levels["stop"] > v.levels["entry"] > v.levels["tp1"]


def test_technical_timeframes_disagree_hold(cfg):
    up, down = frames(0.05), frames(-0.05)
    v = technical(up["HOUR_4"], down["HOUR"], up["MINUTE_15"], PIP, cfg["station"])
    assert v.direction == "HOLD" and v.watch

# ---------- risk officer ----------

def _tech(cfg, drift=0.05):
    f = frames(drift)
    return f, technical(f["HOUR_4"], f["HOUR"], f["MINUTE_15"], PIP, cfg["station"])


def test_risk_clear_in_clean_trend(cfg):
    f, v = _tech(cfg)
    px = v.levels["entry"]
    r = risk_officer(v, f["HOUR"], f["MINUTE_15"], px, px + 0.01, "X", INS, [], NOW, cfg["station"])
    assert not r.veto, r.reasons


def test_risk_vetoes_wide_spread_stale_and_missing_quote(cfg):
    f, v = _tech(cfg)
    px = v.levels["entry"]
    assert any("Spread" in x for x in risk_officer(v, f["HOUR"], f["MINUTE_15"], px, px + 0.10, "X", INS, [], NOW, cfg["station"]).reasons)
    assert any("stale" in x for x in risk_officer(v, f["HOUR"], f["MINUTE_15"], px, px + 0.01, "X", INS, [], NOW + 7200, cfg["station"]).reasons)
    assert any("No live quote" in x for x in risk_officer(v, f["HOUR"], f["MINUTE_15"], None, None, "X", INS, [], NOW, cfg["station"]).reasons)


def test_risk_vetoes_volatility_spike(cfg):
    f, v = _tech(cfg)
    m = f["MINUTE_15"].copy()
    m.loc[m.index[-1], "high"] += 30
    px = v.levels["entry"]
    assert any("Volatility" in x for x in risk_officer(v, f["HOUR"], m, px, px + 0.01, "X", INS, [], NOW, cfg["station"]).reasons)

# ---------- commander ----------

LV = {"entry": 100.0, "entry_low": 99.9, "entry_high": 100.1, "stop": 99.0, "tp1": 102.0, "tp2": 103.0}


def test_commander_buy_passes_with_levels(cfg):
    d = decide("X", [Vote("technical", "BUY", 80, ["r"], 99.5, levels=LV)], RiskReport(False, []), 0.0, cfg["station"], 0)
    assert d["call"] == "BUY" and d["rr"] == 2.0 and d["levels"] == LV and not d["hold_why"]


def test_commander_hold_on_low_confidence_and_veto(cfg):
    low = decide("X", [Vote("technical", "BUY", 45, ["r"], levels=LV, watch="pullback")], RiskReport(False, []), 0.0, cfg["station"], 0)
    assert low["call"] == "HOLD" and low["lean"] == "BUY" and "threshold" in low["hold_why"][0] and low["watch"] == ["pullback"]
    veto = decide("X", [Vote("technical", "BUY", 90, ["r"], levels=LV)], RiskReport(True, ["Choppy"]), 0.0, cfg["station"], 0)
    assert veto["call"] == "HOLD" and "Risk Officer veto — Choppy" in veto["hold_why"]


def test_commander_hold_on_conflict(cfg):
    votes = [Vote("technical", "BUY", 90, ["r"], levels=LV), Vote("macro", "SELL", 60, ["m"])]
    d = decide("X", votes, RiskReport(False, []), 0.0, {**cfg["station"], "min_confidence": 0}, 0)
    assert d["call"] == "HOLD" and "conflict" in d["hold_why"][0] and d["confidence"] == 66


def test_commander_fundamentals_support_and_dilute(cfg):
    tech = Vote("technical", "BUY", 60, ["t"], levels=LV)
    agree = decide("X", [tech, Vote("macro", "BUY", 70, ["usd weak"]), Vote("news", "HOLD", 0, ["n"])],
                   RiskReport(False, []), 0.0, cfg["station"], 0)
    assert agree["call"] == "BUY" and agree["confidence"] == 77 and agree["reasons"][0] == "technicals and macro agree"
    assert "usd weak" in agree["reasons"]
    no_setup = decide("X", [Vote("technical", "HOLD", 0, ["t"], watch="w"), Vote("macro", "BUY", 80, ["m"])],
                      RiskReport(False, []), 0.0, cfg["station"], 0)
    assert no_setup["call"] == "HOLD" and "No technical setup" in no_setup["hold_why"][0]

# ---------- outcomes + store ----------

def _sig(call="BUY"):
    lv = LV if call == "BUY" else {**LV, "stop": 101.0, "tp1": 98.0, "tp2": 97.0}
    return {"symbol": "X", "mode": "intraday", "call": call, "levels": lv, "invalidation": 99.5 if call == "BUY" else 100.5,
            "created": 1000, "expires": 5000}


def test_outcomes_buy():
    s = _sig()
    assert check_outcome(s, 100.5, 100.6, 2000) is None
    assert check_outcome(s, 102.0, 102.1, 2000) == ("hit_tp", 2.0)
    assert check_outcome(s, 99.0, 99.1, 2000) == ("hit_sl", -1.0)
    assert check_outcome(s, 100.5, 100.6, 5000) == ("expired", 0.5)


def test_outcomes_partial_then_breakeven_or_tp2():
    s = _sig()                                                   # entry 100, stop 99, tp1 102, tp2 103
    assert check_outcome(s, 102.0, 102.1, 2000, partial=0.5) == ("tp1_partial", 2.0)
    assert s["tp1_taken"] and s["banked_r"] == 1.0 and s["remaining"] == 0.5
    assert check_outcome(s, 101.0, 101.1, 2000, partial=0.5) is None            # still open, stop now at entry
    assert check_outcome(s, 100.0, 100.1, 2000, partial=0.5) == ("breakeven", 0.0)
    assert blended(s, 0.0) == 1.0                                # half at +2R, half at 0 -> +1R
    s2 = _sig()
    check_outcome(s2, 102.0, 102.1, 2000, partial=0.5)
    assert check_outcome(s2, 103.0, 103.1, 2000, partial=0.5) == ("hit_tp2", 3.0) and blended(s2, 3.0) == 2.5
    assert blended(_sig(), -1.0) == -1.0                         # no partial: unchanged


def test_outcomes_sell_uses_ask():
    s = _sig("SELL")
    assert check_outcome(s, 97.9, 98.05, 2000) is None  # bid through TP but ask not yet
    assert check_outcome(s, 97.9, 98.0, 2000) == ("hit_tp", 2.0)


def test_invalidation_only_after_signal_and_beyond_level():
    s = _sig()
    assert check_invalidation(s, 500, 99.0) is None  # candle closed before the signal
    assert check_invalidation(s, 2000, 99.8) is None
    assert check_invalidation(s, 2000, 99.4) == ("invalidated", -0.6)


def test_store_roundtrip(tmp_path):
    st = Store(tmp_path / "s.sqlite")
    s = st.insert(_sig())
    assert [x["id"] for x in st.active()] == [s["id"]]
    st.close(s, "hit_tp", 2.0, 3000)
    assert st.active() == [] and st.history("X")[0]["status"] == "hit_tp"

# ---------- engine ----------

def test_engine_issues_tracks_and_supersedes(cfg, tmp_path):
    bus = Bus()
    ins = {"X": {"pip": PIP, "max_spread": 4.0}}
    st = Station(bus, Store(tmp_path / "s.sqlite"), ins, cfg["station"])
    f = frames(0.05)
    px = float(f["MINUTE_15"].close.iloc[-1])
    st.on_price({"symbol": "X", "bid": px, "ask": px + 0.01}, NOW * 1000)
    d = st.on_frames("X", f, NOW)
    assert d["call"] == "BUY" and st.active["X"]["call"] == "BUY"
    assert st.on_frames("X", f, NOW) is None  # same 15M candle: no re-evaluation

    tp1 = st.active["X"]["levels"]["tp1"]
    st.on_price({"symbol": "X", "bid": tp1, "ask": tp1 + 0.01}, NOW * 1000 + 1)
    assert "X" not in st.active and st.store.history("X")[0]["status"] == "hit_tp"
    assert bus.snapshot["signal:X:intraday"]["status"] == "hit_tp"

    # an approved trend filter: no daily frame / against the daily trend -> HOLD with the reason; with it -> BUY again
    st2 = Station(bus, Store(tmp_path / "s2.sqlite"), ins, cfg["station"])
    st2.filters["X:intraday"] = {"trend": True, "chop": False, "hours": []}
    st2.on_price({"symbol": "X", "bid": px, "ask": px + 0.01}, NOW * 1000)
    down = frame(120, 86400, -0.5)
    d = st2.on_frames("X", {**f, "DAY": down}, NOW)
    assert d["call"] == "HOLD" and "Risk Officer veto — filter: daily trend against the trade" in d["hold_why"]
    st2.last_m15.clear()
    assert st2.on_frames("X", {**f, "DAY": frame(120, 86400, 0.5)}, NOW)["call"] == "BUY"


def test_parse_prices_mid_and_drops_forming_candle():
    p = lambda t, v: {"snapshotTimeUTC": t, "lastTradedVolume": 5,  # noqa: E731
                      **{f"{k}Price": {"bid": v, "ask": v + 2} for k in ("open", "high", "low", "close")}}
    body = {"prices": [p("2027-01-15T08:00:00", 10), p("2027-01-15T08:15:00", 20)]}
    t0 = pd.Timestamp("2027-01-15T08:00:00", tz="UTC").timestamp()
    df = parse_prices(body, "MINUTE_15", t0 + 900 + 60)  # second candle still forming
    assert len(df) == 1 and df.close.iloc[0] == 11 and df.time.iloc[0] == t0
