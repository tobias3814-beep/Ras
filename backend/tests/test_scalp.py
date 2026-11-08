from datetime import datetime, timezone

import numpy as np
import pandas as pd
import pytest

from jarvis.bus import Bus
from jarvis.paper import Paper, units_for
from jarvis.scalp import core, risk, sessions
from jarvis.scalp.book import Book
from jarvis.scalp.crypto import btc_context, imbalance, to_df
from jarvis.scalp.fx import FxScalpDesk
from jarvis.scalp.strategies import (AsianSweep, EMAPullback, FundingOI, LiquiditySweep, LondonORB, OilEIA,
                                     OrderBookImbalance, PostNews, RoundNumber, SqueezeBreakout, VWAPReclaim)
from jarvis.station.context import Context
from jarvis.station.store import Store

UTC = timezone.utc
ms = lambda dt: int(dt.timestamp() * 1000)  # noqa: E731
JAN14 = datetime(2026, 1, 14, tzinfo=UTC)  # a Wednesday, London on GMT (= UTC)


def bars(t0_s: float, tf_s: int, rows: list[tuple], vol: float = 10) -> pd.DataFrame:
    """rows of (o, h, l, c) or (o, h, l, c, v); times are candle opens."""
    out = [{"time": int(t0_s + i * tf_s), "open": r[0], "high": r[1], "low": r[2], "close": r[3],
            "volume": r[4] if len(r) > 4 else vol} for i, r in enumerate(rows)]
    return pd.DataFrame(out)


def flat(n: int, px: float = 100.0, w: float = 0.05) -> list[tuple]:
    return [(px, px + w, px - w, px)] * n


def inp(fast=None, mid=None, bias=None, now=JAN14, regime="range", **extra) -> core.ScalpInput:
    e = bars(0, 60, flat(5))
    return core.ScalpInput("X", bias if bias is not None else e, mid if mid is not None else e, fast if fast is not None else e,
                           100.0, now.timestamp(), regime, extra)

# ---------- sessions ----------

def test_session_windows_follow_dst():
    s = {"tz": "Europe/London", "start": "07:00", "end": "10:00"}
    assert sessions.window(s, JAN14)[0] == ms(datetime(2026, 1, 14, 7, tzinfo=UTC))      # GMT
    assert sessions.window(s, datetime(2026, 7, 14, tzinfo=UTC))[0] == ms(datetime(2026, 7, 14, 6, tzinfo=UTC))  # BST


def test_scalp_session_overlap_tokyo_and_rollover(cfg):
    sc = cfg["scalp"]["sessions"]
    assert sessions.scalp_session("EUR_USD", sc, ms(datetime(2026, 1, 14, 14, tzinfo=UTC))) == "overlap"
    assert sessions.scalp_session("EUR_USD", sc, ms(datetime(2026, 1, 14, 8, tzinfo=UTC))) == "london_open"
    assert sessions.scalp_session("EUR_USD", sc, ms(datetime(2026, 1, 14, 2, tzinfo=UTC))) is None
    assert sessions.scalp_session("USD_JPY", sc, ms(datetime(2026, 1, 14, 2, tzinfo=UTC))) == "tokyo"
    assert sessions.near_rollover(ms(datetime(2026, 1, 14, 22, 10, tzinfo=UTC)), 15)   # 17:10 New York (EST)
    assert not sessions.near_rollover(ms(datetime(2026, 1, 14, 21, 30, tzinfo=UTC)), 15)


def test_session_clock_in_user_tz(cfg):
    rows = {r["name"]: r for r in sessions.today(cfg["scalp"]["sessions"], ms(datetime(2026, 1, 14, 9, tzinfo=UTC)), "Asia/Bahrain")}
    assert rows["london"]["start_local"] == "11:00" and rows["london"]["open"] and not rows["new_york"]["open"]

# ---------- core ----------

def test_regime_trend_and_chop():
    trend = bars(0, 300, [(100 + i * 0.5, 100 + i * 0.5 + 0.6, 100 + i * 0.5 - 0.1, 100 + i * 0.5 + 0.5) for i in range(80)])
    c = 100 + 0.2 * np.sin(np.arange(80))
    chop = bars(0, 300, [(x, x + 0.3, x - 0.3, x) for x in c])
    assert core.regime(trend) == "trend" and core.regime(chop) == "chop"


def test_confluence_and_levels(cfg):
    s = lambda d, sc: core.Setup("s", d, 99.0, sc, "r")  # noqa: E731
    assert core.confluence([s(1, 60), s(1, 55)])[:2] == (1, 70)
    assert core.confluence([s(1, 60), s(-1, 50)])[:2] == (1, 45)
    assert core.confluence([]) == (0, 0, [])
    lv = core.levels(1, 100.0, 99.8, 0.5, cfg["scalp"])  # ATR minimum (0.5) beats the 0.2 structural stop
    assert lv["stop"] == 99.5 and lv["tp1"] == pytest.approx(100.9)

# ---------- FX strategies ----------

def test_london_orb_break_and_retest():
    t_open = JAN14.replace(hour=7).timestamp()
    mid = bars(JAN14.timestamp(), 300, [(100.5, 101.0, 100.0, 100.5)] * 84)  # 00:00-07:00 range 100-101
    fast = bars(t_open - 3600, 60, flat(46, 100.5) + [(101 + i * 0.02, 101.05 + i * 0.02, 100.99 + i * 0.02, 101.02 + i * 0.02) for i in range(13)]
                + [(101.15, 101.2, 101.01, 101.1)])
    x = inp(fast, mid, now=JAN14.replace(hour=7, minute=30), session="london_open",
            windows={"london_open": (t_open * 1000, (t_open + 3 * 3600) * 1000)})
    s = LondonORB().evaluate(x)
    assert s and s.direction == 1 and "pre-London high 101" in s.reason
    assert LondonORB().evaluate(inp(fast, mid, session="ny_open")) is None


def test_asian_sweep_reversal():
    tokyo = (ms(JAN14), ms(JAN14.replace(hour=6)))
    mid = bars(JAN14.timestamp(), 300, [(100.5, 101.0, 100.0, 100.5)] * 95 + [(100.9, 101.3, 100.8, 100.9)])
    s = AsianSweep().evaluate(inp(mid=mid, now=JAN14.replace(hour=8), session="london_open", windows={"tokyo": tokyo}))
    assert s and s.direction == -1 and s.stop > 101.3


def test_vwap_reclaim_on_volume():
    fast = bars(0, 60, flat(28) + [(100, 100.05, 99.85, 99.9), (99.9, 100.35, 99.9, 100.3, 50)])
    s = VWAPReclaim("fx").evaluate(inp(fast, vwap_anchor_s=0))
    assert s and s.direction == 1 and "reclaimed VWAP" in s.reason
    quiet = fast.copy()
    quiet.loc[quiet.index[-1], "volume"] = 10
    assert VWAPReclaim("fx").evaluate(inp(quiet, vwap_anchor_s=0)) is None


def test_ema_pullback_in_15m_trend():
    bias = bars(0, 900, [(100 + i * 0.2,) * 2 + (100 + i * 0.2 - 0.1, 100 + i * 0.2 + 0.1) for i in range(60)])
    rows = [(100 + i * 0.1, 100 + i * 0.1 + 0.05, 100 + i * 0.1 - 0.05, 100 + i * 0.1) for i in range(60)]
    rows[-3:] = [(o, h, c - 1.05, c) for o, h, _, c in rows[-3:]]  # long lower wicks into EMA21
    s = EMAPullback().evaluate(inp(bars(0, 60, rows), bias=bias, regime="trend"))
    assert s and s.direction == 1
    assert EMAPullback().regimes == frozenset({"trend"})


def test_round_number_rejection():
    fast = bars(0, 60, flat(30, 100.3) + [(100.04, 100.08, 99.95, 100.07)])
    s = RoundNumber().evaluate(inp(fast, mid=bars(0, 300, flat(30, 100.3)), round=1.0))
    assert s and s.direction == 1 and "round number 100" in s.reason


def test_post_news_first_clean_break_after_blackout():
    t = JAN14.replace(hour=13, minute=30)
    ev = {"title": "CPI m/m", "country": "USD", "impact": "High", "time": ms(t)}
    mid = bars(t.timestamp() - 3600, 300, [(100.5, 101, 100, 100.5)] * 16 + [(100.9, 101.3, 100.9, 101.2)])
    s = PostNews().evaluate(inp(mid=mid, now=t.replace(minute=55), events=[ev]))
    assert s and s.direction == 1
    assert PostNews().evaluate(inp(mid=mid, now=t.replace(minute=40), events=[ev])) is None  # still in blackout


def test_oil_eia_wednesday_only():
    t = JAN14.replace(hour=15, minute=30)  # 10:30 New York
    ev = {"title": "Crude Oil Inventories", "country": "USD", "impact": "Medium", "time": ms(t)}
    mid = bars(t.timestamp(), 300, [(70.5, 71, 70, 70.5)] * 4 + [(70.4, 70.4, 69.7, 69.8)])
    x = inp(mid=mid, now=t.replace(minute=55), all_events=[ev])
    x.symbol = "WTI_USD"
    assert (s := OilEIA().evaluate(x)) and s.direction == -1
    x.symbol = "XAU_USD"
    assert OilEIA().evaluate(x) is None
    thu = {**ev, "time": ev["time"] + 86_400_000}
    y = inp(mid=bars(t.timestamp() + 86400, 300, [(70.5, 71, 70, 70.5)] * 4 + [(70.4, 70.4, 69.7, 69.8)]),
            now=t.replace(day=15, minute=55), all_events=[thu])
    y.symbol = "WTI_USD"
    assert OilEIA().evaluate(y) is None

# ---------- crypto strategies ----------

def test_liquidity_sweep():
    mid = bars(0, 300, [(100.5, 101, 100, 100.5)] * 25 + [(100.5, 100.6, 99.6, 100.2)])
    assert (s := LiquiditySweep().evaluate(inp(mid=mid))) and s.direction == 1


def test_squeeze_breakout():
    rng = np.random.default_rng(0)
    loud = [(100 + x, 100 + x + 1, 100 + x - 1, 100 + x) for x in rng.normal(0, 2, 100)]
    quiet = flat(28, 100, 0.02)
    mid = bars(0, 300, loud + quiet + [(100, 100.6, 100, 100.5, 100)])
    assert (s := SqueezeBreakout().evaluate(inp(mid=mid))) and s.direction == 1


def test_orderbook_and_imbalance():
    book = {"bids": [[99.99, 9], [99.95, 1], [90, 100]], "asks": [[100.01, 1], [100.05, 1]]}
    assert imbalance(book) == pytest.approx((10 - 2) / 12)
    fast = bars(0, 60, [(100 + i * 0.01,) * 2 + (100 + i * 0.01 - 0.01, 100 + i * 0.01 + 0.01) for i in range(30)])
    s = OrderBookImbalance().evaluate(inp(fast, imbalance=0.8))
    assert s and s.direction == 1 and s.score == 65
    assert OrderBookImbalance().evaluate(inp(fast, imbalance=0.2)) is None


def test_funding_oi_divergence():
    mid = bars(0, 300, [(100 + i * 0.1, 100 + i * 0.1 + 0.05, 100 + i * 0.1 - 0.05, 100 + i * 0.1) for i in range(20)])
    oi = pd.Series(np.linspace(1000, 960, 20))
    s = FundingOI().evaluate(inp(mid=mid, oi=oi, funding=0.0005))
    assert s and s.direction == -1 and s.score == 70
    assert FundingOI().evaluate(inp(mid=mid, oi=pd.Series(np.linspace(1000, 1030, 20)))) is None

# ---------- risk ----------

INS = {"pip": 0.0001, "max_spread": 1.5, "ccy": ["EUR", "USD"]}
LV = {"entry": 1.1000, "entry_low": 1.0999, "entry_high": 1.1001, "stop": 1.0990, "tp1": 1.1018, "tp2": 1.1025}


def test_fx_checks(cfg):
    sc, now = cfg["scalp"], ms(JAN14.replace(hour=14))
    f = bars(0, 60, flat(60, 1.1)).assign(bid_close=1.09995, ask_close=1.10005)
    ok = risk.fx_checks("EUR_USD", INS, 1, LV, 1.09995, 1.10005, f, [], "overlap", 0.0, now, sc, 1.5)
    assert ok == []
    out = risk.fx_checks("EUR_USD", INS, 1, LV, 1.0998, 1.1002, f, [], None, 0.2, now, sc, 1.5)
    assert any("Session inactive" in o for o in out) and any("widened to 4.0x" in o for o in out)
    assert any("Spread 4.0 pips" in o for o in out) and any("DXY filter" in o for o in out)
    assert any("20% of the first target" in o for o in out) and any("Net R:R" in o for o in out)
    short = risk.fx_checks("EUR_USD", INS, -1, {**LV, "stop": 1.1010, "tp1": 1.0982}, 1.09995, 1.10005, f, [], "overlap", 0.2, now, sc, 1.5)
    assert not any("DXY" in o for o in short)  # shorting EUR into a USD spike is allowed


def test_usd_move_basket():
    up = lambda a, b: bars(0, 60, [(x,) * 4 for x in np.linspace(a, b, 16)])  # noqa: E731  (15 bars of move)
    assert risk.usd_move({"EUR_USD": up(1.1, 1.0989), "USD_JPY": up(150, 150.15)}) == pytest.approx(0.1, abs=0.01)


def test_crypto_checks_costs_and_btc(cfg):
    scfg = cfg["crypto"]["scanner"]
    lv = {"entry": 100.0, "stop": 99.8, "tp1": 100.36}
    assert any("clear costs" in o for o in risk.crypto_checks(lv, 99.99, 100.0, 0.05, scfg, None, 1, 1.5))
    wide = {"entry": 100.0, "stop": 98.0, "tp1": 103.6}
    assert risk.crypto_checks(wide, 99.99, 100.0, 0.05, scfg, {"trend": 1, "corr": 0.9}, 1, 1.5) == []
    assert any("BTC filter" in o for o in risk.crypto_checks(wide, 99.99, 100.0, 0.05, scfg, {"trend": -1, "corr": 0.9}, 1, 1.5))


def test_limit_checks(cfg):
    lim, now = cfg["scalp"]["limits"]["fx"], 10_000_000
    sig = lambda r, t, sess="overlap": {"status": "hit_sl" if r < 0 else "hit_tp", "result_r": r, "closed_at": t, "session": sess}  # noqa: E731
    assert any("Cooldown" in o for o in risk.limit_checks([sig(-1, now - 60_000), sig(-1, now - 30_000)], 0, "overlap", lim, now))
    assert not any("Cooldown" in o for o in risk.limit_checks([sig(-1, now - 9_000_000), sig(-1, now - 8_000_000)], 0, "x", lim, now))
    assert any("Daily loss limit hit (-3.5R)" in o for o in risk.limit_checks([sig(-1, 1), sig(1, 2), sig(-2, 3), sig(-1.5, 4)], 0, None, lim, now))
    assert any("this session" in o for o in risk.limit_checks([sig(1, i) for i in range(4)], 0, "overlap", lim, now))
    assert any("concurrent" in o for o in risk.limit_checks([], 2, None, lim, now))


def test_correlation_guard_warns_then_vetoes():
    opens = [{"symbol": "EUR_USD", "call": "BUY"}, {"symbol": "XAU_USD", "call": "BUY"}]
    veto, warn = risk.correlation_guard("GBP_USD", 1, opens[:1], 3)
    assert not veto and "short USD x2" in warn[0]
    veto, warn = risk.correlation_guard("GBP_USD", 1, opens + [{"symbol": "USD_JPY", "call": "SELL"}], 3)
    assert veto and "short USD x4" in veto[0]
    assert risk.correlation_guard("USD_JPY", 1, opens, 3) == ([], [])  # long USD reduces the stack
    assert risk.correlation_guard("perp:ETH/USDT:USDT", 1, [{"symbol": "spot:BTC/USDT", "call": "BUY"}], 3)[1]

# ---------- book ----------

def _scalp(now, call="BUY"):
    lv = {"entry": 100.0, "entry_low": 99.98, "entry_high": 100.02, "stop": 99.8, "tp1": 100.36, "tp2": 100.5}
    if call == "SELL":
        lv = {**lv, "stop": 100.2, "tp1": 99.64, "tp2": 99.5}
    return {"symbol": "X", "mode": "scalp", "call": call, "confidence": 70, "levels": lv, "created": now,
            "expires": now + 3_600_000, "entry_until": now + 720_000, "time_stop_at": now + 600_000}


def test_book_runaway_time_stop_and_tp(cfg, tmp_path):
    bus, now = Bus(), 1_000_000
    b = Book(bus, Store(tmp_path / "s.sqlite"), ("scalp",), cfg["scalp"])
    b.open(_scalp(now))
    b.on_price("X", 100.11, 100.12, now + 1000)                 # +0.55R: ran away from the entry zone
    assert b.active["X"]["actionable"] is False and "ran away" in b.active["X"]["entry_note"]
    b.on_price("X", 100.36, 100.37, now + 2000)
    assert "X" not in b.active and b.store.history("X")[0]["status"] == "hit_tp"

    b.open(_scalp(now))
    b.on_price("X", 100.02, 100.03, now + 601_000)              # 10 min, never +0.5R
    assert b.store.history("X")[0]["status"] == "time_stop" and b.store.history("X")[0]["result_r"] == 0.1

# ---------- paper ----------

def test_paper_sizing_fills_and_pnl(cfg, tmp_path):
    bus, store = Bus(), Store(tmp_path / "s.sqlite")
    ins = {"EUR_USD": {"pip": 0.0001}, "USD_JPY": {"pip": 0.01}}
    big = {**cfg["paper"], "fx": {**cfg["paper"]["fx"], "balance": 10_000}}  # round numbers; the $100 case is tested below
    p = Paper(bus, store, big, ins, {"spot": 0.1, "perp": 0.05}, 0.02)
    p.set_rules({"EUR_USD": {}, "USD_JPY": {}})  # rules loaded but no limits: plain risk sizing (limits tested below)
    assert units_for(50, 1.1, 1.099, 1.0) == pytest.approx(50_000)
    p.on_quote("EUR_USD", 1.10000, 1.10002, 0)
    sig = {"id": 1, "symbol": "EUR_USD", "mode": "scalp", "call": "BUY", "status": "active",
           "levels": {"entry": 1.10002, "stop": 1.09902, "tp1": 1.10182}}
    p.on_signal(sig, 0)
    pos = p.open[1]
    assert pos["entry"] == pytest.approx(1.10004)       # ask + 0.2 pip slippage
    assert pos["units"] * (pos["entry"] - pos["stop"]) == pytest.approx(50.0)  # 0.5% of 10,000
    p.on_quote("EUR_USD", 1.09900, 1.09902, 1)
    closed = store.positions()[0]
    assert closed["reason"] == "stop" and -52 < closed["pnl"] < -50  # stop + slippage
    assert p.snapshot()["accounts"]["fx"]["balance"] == pytest.approx(10_000 + closed["pnl"])

    p.on_quote("USD_JPY", 150.00, 150.02, 2)
    p.on_signal({**sig, "id": 2, "symbol": "USD_JPY", "levels": {"entry": 150.02, "stop": 149.82, "tp1": 150.38}}, 2)
    p.on_quote("USD_JPY", 150.38, 150.40, 3)
    jpy = store.positions()[0]
    assert jpy["reason"] == "target" and 80 < jpy["pnl"] < 95   # ~1.7R in USD after JPY conversion

    p.on_quote("perp:ETH/USDT:USDT", 2000.0, 2000.1, 4)
    p.on_signal({**sig, "id": 3, "symbol": "perp:ETH/USDT:USDT", "mode": "crypto",
                 "levels": {"entry": 2000.1, "stop": 1990.0, "tp1": 2020.0}}, 4)
    eth = p.open[3]
    assert eth["fees"] == pytest.approx(eth["units"] * eth["entry"] * 0.0005)
    p.on_signal({**sig, "id": 3, "symbol": "perp:ETH/USDT:USDT", "mode": "crypto", "status": "time_stop",
                 "levels": eth and {"entry": 2000.1, "stop": 1990.0, "tp1": 2020.0}}, 5)
    assert 3 not in p.open and store.positions()[0]["reason"] == "signal time stop"

def test_paper_small_account_follows_broker_rules(cfg, tmp_path):
    bus, store = Bus(), Store(tmp_path / "s.sqlite")
    ins = {"XAU_USD": {"pip": 0.1}, "EUR_USD": {"pip": 0.0001}}
    p = Paper(bus, store, cfg["paper"], ins, {}, 0.02)
    store.save_position({"division": "fx", "signal_id": 99, "symbol": "EUR_USD", "opened": 0, "closed": 1, "pnl": -153.0})
    p.reset("fx", 100.0, 10)                                   # fresh $100 account: the old -153 doesn't count
    assert p.balance("fx") == 100.0 and p.snapshot()["accounts"]["fx"]["start"] == 100.0
    p.rules = {"XAU_USD": {"min_size": 0.01, "step": 0.01, "margin_pct": 5}, "EUR_USD": {"min_size": 100, "step": 100, "margin_pct": 3.33}}
    gold = {"id": 1, "symbol": "XAU_USD", "mode": "intraday", "call": "BUY", "status": "active",
            "levels": {"entry": 4000.0, "stop": 3990.0, "tp1": 4020.0, "tp2": 4030.0}}
    p.on_quote("XAU_USD", 4000.0, 4000.0, 20)
    p.on_signal(gold, 20)                                      # $0.50 risk / ~$10 stop = 0.0499 oz -> 0.04
    pos = p.open[1]
    assert pos["units"] == pytest.approx(0.04) and pos["risk"] == pytest.approx(0.04 * 10.02, rel=1e-3)
    assert pos["margin"] == pytest.approx(0.04 * 4000.02 * 0.05, rel=1e-3)
    p.on_signal({**gold, "id": 2, "levels": {"entry": 4000.0, "stop": 3800.0, "tp1": 4400.0}}, 21)   # 0.01 oz = $2 risk > 2%
    assert 2 not in p.open and store.journal([2])[2]["trade_log"].startswith("Not taken on paper: minimum size 0.01 risks $2.00")
    p.on_quote("EUR_USD", 1.1, 1.1, 22)
    p.on_signal({**gold, "id": 3, "symbol": "EUR_USD", "levels": {"entry": 1.1, "stop": 1.099, "tp1": 1.102}}, 22)
    assert p.open[3]["units"] == 400                           # $0.50 / 0.00102 = 490 -> stepped down to 400
    p.exits["partial"] = 0.5                                   # gold hits TP1: 0.02 oz banked, 0.02 runs to TP2 at entry stop
    p.on_quote("XAU_USD", 4020.5, 4020.5, 24)
    g = p.open[1]
    assert g["tp1_taken"] and g["units"] == pytest.approx(0.02) and g["stop"] == g["entry"] and g["tp"] == 4030.0
    p.on_quote("XAU_USD", 4000.0, 4000.0, 25)                  # back to entry: rest closes flat, banked half stays
    done = [x for x in store.positions() if x["signal_id"] == 1][0]
    assert done["reason"] == "breakeven" and done["pnl"] == pytest.approx(0.02 * (4020.0 - 4000.02), abs=0.05)
    p.exits["partial"] = 0.0
    p.rules["EUR_USD"]["margin_pct"] = 50                      # margin now limits the size: $91.98 free / $0.55 per unit
    p.on_signal({**gold, "id": 4, "symbol": "EUR_USD", "levels": {"entry": 1.1, "stop": 1.099, "tp1": 1.102}}, 23)
    assert p.open[4]["units"] == 100


# ---------- crypto helpers ----------

def test_to_df_drops_forming_candle_and_btc_context():
    df = to_df([[0, 1, 1, 1, 1, 1], [60_000, 1, 1, 1, 1, 1]], "1m", 100)
    assert list(df.time) == [0]
    btc = bars(0, 300, [(100 + i, 101 + i, 99 + i, 100 + i) for i in range(60)])
    ctx = btc_context(btc.assign(close=btc.close * 2), btc)
    assert ctx["trend"] == 1 and ctx["corr"] > 0.99

# ---------- FX desk end to end ----------

def _desk(cfg, tmp_path, prices):
    bus = Bus()
    book = Book(bus, Store(tmp_path / "s.sqlite"), ("scalp",), cfg["scalp"])
    ins = {"XAU_USD": {**cfg["capital"]["instruments"]["XAU_USD"]}}
    return bus, book, FxScalpDesk(bus, book, ins, cfg["scalp"], cfg["station"], Context(), prices,
                                  {"XAU_USD": {"intraday": True, "scalp": True}}, lambda: list(book.active.values()), "Asia/Bahrain")


def _gold_frames(t_end: float):
    up = lambda n, tf, step: bars(t_end - n * tf, tf, [(2000 + i * step, 2000 + i * step + step / 2,  # noqa: E731
                                                       2000 + i * step - step / 2, 2000 + i * step) for i in range(n)])
    m1 = up(80, 60, 0.3)
    m1.loc[m1.index[-3:], "low"] = m1.close.iloc[-3:] - 3.2  # pullback wicks into EMA21
    m1 = m1.assign(bid_close=m1.close - 0.1, ask_close=m1.close + 0.1)
    return {"MINUTE_15": up(80, 900, 2.0), "MINUTE_5": up(80, 300, 1.0), "MINUTE": m1}


def test_fx_desk_scalp_buy_in_session_hold_outside(cfg, tmp_path):
    t = datetime(2026, 1, 14, 14, 5, tzinfo=UTC).timestamp()  # London–NY overlap
    f = _gold_frames(t)
    px = float(f["MINUTE"].close.iloc[-1])
    bus, book, desk = _desk(cfg, tmp_path, {"XAU_USD": {"bid": px - 0.1, "ask": px + 0.1}})
    d = desk.on_frames("XAU_USD", f, t)
    assert d["mode"] == "scalp" and d["priority"] == 2 and d["session"] == "overlap"
    assert d["call"] == "BUY", d["hold_why"]
    s = book.active["XAU_USD"]
    assert s["say"].startswith("Scalp buy gold at") and s["entry_until"] - s["created"] == 12 * 60_000
    assert bus.snapshot["call:XAU_USD:scalp"]["call"] == "BUY"

    t2 = datetime(2026, 1, 14, 3, 5, tzinfo=UTC).timestamp()  # Asia: gold not allowed
    _, _, desk2 = _desk(cfg, tmp_path / "b", {"XAU_USD": {"bid": px - 0.1, "ask": px + 0.1}})
    d2 = desk2.on_frames("XAU_USD", _gold_frames(t2), t2)
    assert d2["call"] == "HOLD" and any("Session inactive" in r for r in d2["hold_why"])
