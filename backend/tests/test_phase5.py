import asyncio

import numpy as np
import pandas as pd
import pytest

from jarvis import backtest, history, journal as jmod, performance
from jarvis.backtest import Cand
from jarvis.bus import Bus
from jarvis.journal import Journal, template_review, review_input, weekly_review
from jarvis.learning import Learning
from jarvis.station.store import Store

# ---------- performance ----------

def test_stats_pf_expectancy_drawdown():
    s = performance.stats([2.0, -1.0, -1.0, 1.5, -1.0])
    assert s["trades"] == 5 and s["win_rate"] == 40.0 and s["profit_factor"] == round(3.5 / 3, 2)
    assert s["expectancy"] == pytest.approx(0.1) and s["max_dd_r"] == -2.0 and s["total_r"] == 0.5
    assert performance.stats([])["trades"] == 0


def test_group_hours_calibration_agents():
    trades = [
        {"r": 1.0, "time": 1_700_000_000_000, "strategies": ["a", "b"], "confidence": 72, "call": "BUY",
         "votes": [{"agent": "macro", "direction": "BUY"}, {"agent": "news", "direction": "SELL"}, {"agent": "sentiment", "direction": "HOLD"}]},
        {"r": -1.0, "time": 1_700_003_600_000, "strategies": ["a"], "confidence": 65, "call": "BUY",
         "votes": [{"agent": "macro", "direction": "BUY"}, {"agent": "news", "direction": "SELL"}]},
    ]
    g = performance.group(trades, "strategies")
    assert g["a"]["trades"] == 2 and g["b"]["trades"] == 1
    assert list(performance.hours(trades, "Asia/Bahrain")) == ["01", "02"]
    assert [c["bucket"] for c in performance.calibration(trades)] == ["60-69", "70-79"]
    a = performance.agents(trades)
    assert a["macro"] == {"graded": 2, "right": 1, "with_call": 2, "accuracy": 50.0}
    assert a["news"]["right"] == 1 and "sentiment" not in a   # HOLD isn't graded


def test_report_best_worst_hours_never_overlap():
    trades = [{"r": r, "time": 1_700_000_000_000 + h * 3_600_000} for h in range(2) for r in (1, 1, -1) if True]
    rep = performance.report(trades, "UTC")
    assert not set(rep["best_hours"]) & set(rep["worst_hours"])

# ---------- backtest mechanics ----------

def _bars(closes, spread=0.02, t0=0, tf=60):
    """1M-style bars with an exact absolute spread around mid."""
    c = np.asarray(closes, float)
    df = pd.DataFrame({"time": t0 + np.arange(len(c)) * tf, "open": c, "high": c + 0.05, "low": c - 0.05, "close": c, "volume": 10})
    return df.assign(**{f"{s}_{k}": df[k] + (spread / 2 if s == "ask" else -spread / 2)
                        for s in ("bid", "ask") for k in ("open", "high", "low", "close")})


def test_resample_and_quotes():
    m1 = _bars(np.arange(10) + 100.0)
    m5 = backtest.resample(m1, 300)
    assert list(m5.time) == [0, 300] and m5.open.iloc[0] == 100 and m5.close.iloc[0] == 104 and m5.high.iloc[1] == 109.05
    assert "bid_high" in m5 and m5.ask_close.iloc[0] == pytest.approx(104.01)
    q = backtest.with_quotes(m1[["time", "open", "high", "low", "close", "volume"]], 0.02)
    assert (q.ask_close - q.bid_close).iloc[0] == pytest.approx(100 * 0.0002)


def test_simulate_fill_target_stop_first_and_costs(cfg):
    up = _bars([100] * 3 + [100, 100.5, 101, 101.5, 102, 102.5] + [102.5] * 5)
    c = Cand(2, 1, 70, stop=99.0, atr5=0.1, strategies=["vwap"], session="24h")
    t = backtest.simulate("perp:X/USDT:USDT", up, [c], 60, cfg, "crypto")
    assert len(t) == 1 and t[0]["status"] == "hit_tp"
    fill = 100.01 + 100.01 * 0.0002                     # ask + 0.02% slippage
    assert t[0]["entry"] == pytest.approx(fill)
    fee_r = 2 * 0.0005 * fill / (fill - 99.0)
    assert t[0]["r"] == pytest.approx(1.8 - fee_r, abs=1e-3)
    assert backtest.simulate("perp:X/USDT:USDT", up, [c], 75, cfg, "crypto") == []   # below threshold
    assert backtest.simulate("perp:X/USDT:USDT", up, [Cand(2, 1, 70, 99.0, veto=["x"])], 60, cfg, "crypto") == []

    both = _bars([100] * 4 + [100] * 6)
    both.loc[4, ["bid_high", "bid_low"]] = [105.0, 95.0]  # stop and target in the same bar
    t2 = backtest.simulate("perp:X/USDT:USDT", both, [c], 60, cfg, "crypto")
    assert t2[0]["status"] == "hit_sl"


def test_simulate_partial_exit(cfg):
    lv = {"entry": 100.0, "entry_low": 99.9, "entry_high": 100.1, "stop": 99.0, "tp1": 102.0, "tp2": 103.0}
    c = Cand(1, 1, 70, 99.0, levels=lv, strategies=["technical"])
    # fills at bar 2's open (100 + slippage); levels shift with the fill: tp1 ~102.02, tp2 ~103.02
    run_up = _bars([100.0] * 3 + [101, 102.1, 103.1] + [103.1] * 4, spread=0.0, tf=900)
    full = backtest.simulate("XAU_USD", run_up, [c], 60, cfg, "intraday")[0]
    half = backtest.simulate("XAU_USD", run_up, [c], 60, cfg, "intraday", partial=0.5)[0]
    assert (full["status"], full["r"]) == ("hit_tp", pytest.approx(2.0))
    assert (half["status"], half["r"]) == ("hit_tp2", pytest.approx(2.5))   # half at 2R, half at 3R
    back = _bars([100.0] * 3 + [101, 102.1, 101, 99.9] + [99.9] * 4, spread=0.0, tf=900)
    be = backtest.simulate("XAU_USD", back, [c], 60, cfg, "intraday", partial=0.5)[0]
    assert be["status"] == "breakeven" and be["r"] == pytest.approx(0.93, abs=0.01)  # half at +2R, half gapped to 99.9: -0.12R


def test_simulate_time_stop_and_one_position(cfg):
    flat = _bars([100] * 40)
    cands = [Cand(2, 1, 70, 99.0, 0.1), Cand(5, 1, 70, 99.0, 0.1), Cand(20, -1, 70, 101.0, 0.1)]
    t = backtest.simulate("perp:X/USDT:USDT", flat, cands, 60, cfg, "crypto")
    assert [x["status"] for x in t] == ["time_stop", "time_stop"]   # 2nd candidate skipped: position still open
    assert t[0]["r"] < 0 and t[1]["call"] == "SELL"


def test_simulate_intraday_invalidation_on_hour_close(cfg):
    closes = [100.0] * 8 + [99.6] * 8
    bars = _bars(closes, tf=900)
    lv = {"entry": 100.0, "entry_low": 99.9, "entry_high": 100.1, "stop": 99.0, "tp1": 102.0, "tp2": 103.0}
    c = Cand(1, 1, 70, 99.0, levels=lv, invalidation=99.8, strategies=["technical"])
    t = backtest.simulate("XAU_USD", bars, [c], 60, cfg, "intraday")
    assert t[0]["status"] == "invalidated" and (t[0]["closed_at"] // 1000) % 3600 == 0


def test_pick_threshold():
    mk = lambda rs: [{"r": r} for r in rs]  # noqa: E731
    assert backtest._pick({50: mk([1] * 3 + [-1] * 3), 60: mk([1] * 4 + [-1]), 70: mk([2] * 2)}, 55, 5) == 60
    assert backtest._pick({50: mk([1])}, 55, 5) == 55


def test_run_walk_forward_structure(cfg, monkeypatch):
    rng = np.random.default_rng(3)
    n = 3 * 1440
    c = 100 * np.exp(np.cumsum(rng.normal(0, 0.0015, n)))
    t0 = 1_800_000_000 - n * 60
    df = pd.DataFrame({"time": t0 + np.arange(n) * 60, "open": c, "high": c * 1.001, "low": c * 0.999, "close": c,
                       "volume": rng.uniform(5, 20, n)})

    async def fake_ensure(sym, tf, days, cfg=None):
        return df

    monkeypatch.setattr(history, "ensure", fake_ensure)
    r = backtest.run({"symbols": ["perp:X/USDT:USDT"], "mode": "crypto", "days": 3, "folds": 2}, cfg)
    assert len(r["folds"]) == 2 and r["candidates"]["perp:X/USDT:USDT"] > 0
    assert set(r["out_of_sample"]) >= {"summary", "equity", "by_strategy", "by_session", "by_hour", "calibration"}
    assert r["folds"][0]["test"] == [0.33, 0.67] and r["notes"]

# ---------- history ----------

def test_history_merge_dedupe(tmp_path, monkeypatch):
    monkeypatch.setattr(history, "DIR", tmp_path)
    a = pd.DataFrame({"time": [0, 60], "close": [1.0, 2.0]})
    b = pd.DataFrame({"time": [60, 120], "close": [2.5, 3.0]})
    history.save("crypto", "perp:BTC/USDT:USDT", "1m", a)
    out = history.save("crypto", "perp:BTC/USDT:USDT", "1m", b)
    assert list(out.time) == [0, 60, 120] and out.close.iloc[1] == 2.5
    assert history.catalog()[0]["rows"] == 3

# ---------- learning ----------

def _closed(store, agent_dir, won, n):
    for k in range(n):
        s = store.insert({"symbol": "EUR_USD", "mode": "intraday", "call": "BUY", "confidence": 70, "created": k,
                          "votes": [{"agent": "technical", "direction": "BUY", "confidence": 70},
                                    {"agent": "macro", "direction": agent_dir, "confidence": 60}]})
        store.close(s, "hit_tp" if won else "hit_sl", 1.8 if won else -1.0, k + 1)


def test_learning_bounded_logged_freeze_reset(cfg, tmp_path):
    store, sc = Store(tmp_path / "s.sqlite"), {**cfg["station"], "weights": dict(cfg["station"]["weights"])}
    L = Learning(store, Bus(), sc, cfg["learning"], "Asia/Bahrain")
    _closed(store, "BUY", True, 10)
    assert L.update() == []                                   # 10 < min_samples
    _closed(store, "BUY", True, 15)                            # macro right 25/25
    ch = L.update()
    assert ch[0]["agent"] == "macro" and ch[0]["new"] == pytest.approx(0.6 + 0.05)   # capped at max_step
    assert sc["weights"]["macro"] == ch[0]["new"] and store.weight_log()[0]["agent"] == "macro"
    L.freeze(True)
    assert L.update() == [] and Learning(store, Bus(), {**cfg["station"], "weights": dict(cfg["station"]["weights"])},
                                         cfg["learning"], "UTC").frozen
    L.reset()
    assert sc["weights"]["macro"] == 0.6 and store.weight_log()[0]["reason"] == "reset to config"
    assert sc["weights"]["technical"] == 1.0                   # technical never learns

def test_learning_scalp_strategy_adjustment(cfg, tmp_path):
    store, sc = Store(tmp_path / "s.sqlite"), {**cfg["station"], "weights": dict(cfg["station"]["weights"])}
    L = Learning(store, Bus(), sc, cfg["learning"], "UTC")
    for k in range(12):  # vwap_fade loses every time: -1R expectancy -> target -15, 3 points per update
        s = store.insert({"symbol": "XAU_USD", "mode": "scalp", "call": "BUY", "confidence": 70, "created": k, "strategies": ["vwap_fade"]})
        store.close(s, "hit_sl", -1.0, k + 1)
    assert L.update()[0] == {"agent": "scalp:vwap_fade", "old": 0, "new": -3, "reason": "expectancy -1.00R over 12 scalps"}
    for _ in range(6):
        L.update()
    assert L.adjust == {"vwap_fade": -15} and Learning(store, Bus(), sc, cfg["learning"], "UTC").adjust == {"vwap_fade": -15}
    L.reset()
    assert L.adjust == {}


# ---------- journal + review ----------

def _pos(sid, opened, closed, pnl, side=1):
    return {"division": "fx", "signal_id": sid, "symbol": "XAU_USD", "mode": "intraday", "side": side, "units": 1.0,
            "entry": 4000.0, "stop": 3990.0, "tp": 4020.0, "risk": 50.0, "opened": opened, "closed": closed,
            "exit": 4020.0 if pnl > 0 else 3990.0, "reason": "target" if pnl > 0 else "stop", "fees": 0.0, "pnl": pnl}


def test_trade_log_written_once_per_closed_trade(cfg, tmp_path):
    store = Store(tmp_path / "s.sqlite")
    s = store.insert({"symbol": "XAU_USD", "mode": "intraday", "call": "BUY", "confidence": 72, "created": 1,
                      "reasons": ["1H uptrend", "DXY falling"]})
    store.save_position(_pos(s["id"], 0, 3_900_000, 98.5))
    store.save_position({**_pos(999, 0, None, 0.0), "pnl": None})  # still open: no log
    j = Journal(Bus(), store, lambda *_: None)
    assert j.log_trades() == 1 and j.log_trades() == 0
    log = store.journal([s["id"]])[s["id"]]["trade_log"]
    assert log.startswith("Opened LONG XAU/USD (intraday, confidence 72) at 4000.00") and "1H uptrend; DXY falling" in log
    assert log.endswith("(target) after 1h 5m: +$98.50 (+1.97R).")


def test_journal_dashboard_numbers():
    day = 86_400_000
    t0 = 1_767_225_600_000  # 2026-01-01 00:00 UTC
    ps = [_pos(1, t0, t0 + 1000, 100.0), _pos(2, t0, t0 + 2000, -50.0), _pos(3, t0 + day, t0 + day + 1, 200.0),
          _pos(4, t0 + 2 * day, t0 + 2 * day + 1, -100.0), _pos(5, t0, None, 0.0)]
    d = jmod.dashboard(ps, 10_000, 10_140.0, "UTC")
    assert (d["trades"], d["wins"], d["win_rate"], d["net"], d["balance"], d["highest"]) == (4, 2, 50.0, 150.0, 10_150.0, 10_250.0)
    assert d["avg_win"] == 150.0 and d["avg_loss"] == 75.0 and d["ratio"] == 2.0 and d["profit_factor"] == 2.0
    assert d["max_dd_pct"] == round(100 / 10_250 * 100, 2)
    assert d["days"]["2026-01-01"] == {"pnl": 50.0, "trades": 2, "wins": 1, "pct": 0.5}
    assert d["days"]["2026-01-02"]["pct"] == round(200 / 10_050 * 100, 2)
    assert d["trade_streak"] == {"current": 1, "current_win": False, "best_win": 1, "worst_loss": 1}
    assert d["day_streak"] == {"current": 1, "current_win": False, "best_win": 2, "worst_loss": 1}
    assert d["score"]["axes"]["Win rate"] == 50 and d["score"]["axes"]["Payoff"] == 100 and 0 < d["score"]["total"] <= 100
    assert d["returns"]["daily"] is not None and d["returns"]["annual"] is None   # 2 days of history: no yearly guess
    assert jmod.dashboard([], 10_000, 10_000, "UTC")["score"]["total"] == 0


def test_journal_snapshot_notes_and_paper(cfg, tmp_path):
    store, bus = Store(tmp_path / "s.sqlite"), Bus()
    df = _bars([100 + i * 0.1 for i in range(200)])
    j = Journal(bus, store, lambda sym, tf: df if tf == "MINUTE" else None)
    s = store.insert({"symbol": "XAU_USD", "mode": "scalp", "call": "BUY", "confidence": 70, "created": 1})
    j.snapshot(s)
    store.save_journal(s["id"], 2, notes="chased it")
    store.save_position({"division": "fx", "signal_id": s["id"], "symbol": "XAU_USD", "opened": 1, "closed": None, "pnl": None})
    e = j.entries()[0]
    assert len(e["journal"]["snapshot"]) == 150 and e["journal"]["notes"] == "chased it" and e["paper"]["symbol"] == "XAU_USD"


def test_weekly_review_falls_back_to_template(cfg, tmp_path, monkeypatch):
    monkeypatch.setattr(jmod, "secret", lambda k, **_: None)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    store, bus = Store(tmp_path / "s.sqlite"), Bus()
    import time
    now = int(time.time() * 1000)
    for k, (r, strat) in enumerate([(1.8, "vwap")] * 3 + [(-1.0, "orb")] * 5):
        s = store.insert({"symbol": "EUR_USD", "mode": "scalp", "call": "BUY", "confidence": 65, "created": now - 3600_000 + k,
                          "strategies": [strat], "session": "overlap", "votes": []})
        store.close(s, "hit_tp" if r > 0 else "hit_sl", r, now - 1000 + k)
    data = review_input(store, now, "Asia/Bahrain")
    assert data["report"]["summary"]["trades"] == 8 and len(data["losses"]) == 5
    text = template_review(data)
    assert "vwap (by_strategy)" in text.split("## What to disable")[0] and "orb (by_strategy)" in text.split("## What to disable")[1]
    b = asyncio.run(weekly_review(store, bus, cfg["review"], "Asia/Bahrain"))
    assert b["source"] == "template" and bus.snapshot["review"]["kind"] == "weekly"


def test_claude_review_request_shape(monkeypatch):
    seen = {}

    class Msgs:
        async def create(self, **kw):
            seen.update(kw)
            from types import SimpleNamespace as NS
            return NS(stop_reason="end_turn", content=[NS(type="text", text="## What worked\n- ok")])

    class Client:
        def __init__(self, api_key):
            self.beta = type("B", (), {"messages": Msgs()})()

    monkeypatch.setattr(jmod, "secret", lambda k, **_: "sk-test")
    monkeypatch.setattr(jmod.anthropic, "AsyncAnthropic", Client)
    out = asyncio.run(jmod.claude_review({"report": {}}, "claude-opus-5-5"))
    assert out.startswith("## What worked") and seen["model"] == "claude-opus-5-5"
    assert seen["fallbacks"] == "default" and seen["betas"] == ["server-side-fallback-2026-07-01"]
    assert seen["thinking"] == {"type": "adaptive"}


class _FakeCapital:
    """Capital.com /prices stand-in: refuses windows wider than `limit` candles (both ends count), and has no prices
    inside `closed` (a weekend). Records the windows it was asked for."""

    def __init__(self, limit=1000, closed=None, other_error=False):
        self.limit, self.closed, self.other_error, self.asked = limit, closed, other_error, []

    async def get(self, path, params):
        import httpx
        f, to = (pd.Timestamp(params[k], tz="UTC") for k in ("from", "to"))
        self.asked.append((f, to))
        req = httpx.Request("GET", "https://x" + path)

        def err(status, code):
            raise httpx.HTTPStatusError(code, request=req, response=httpx.Response(status, request=req, json={"errorCode": code}))
        if self.other_error:
            err(400, "error.invalid.epic")
        if (to - f) / pd.Timedelta(minutes=1) + 1 > self.limit:
            err(400, "error.invalid.max.daterange")
        ts = [x for x in pd.date_range(f.ceil("min"), to, freq="min") if not (self.closed and self.closed[0] <= x < self.closed[1])]
        if not ts:
            err(404, "error.prices.not-found")
        return {"prices": [{"snapshotTimeUTC": x.strftime("%Y-%m-%dT%H:%M:%S"), "lastTradedVolume": 1,
                            **{f"{k}Price": {"bid": 1.0, "ask": 1.1} for k in ("open", "high", "low", "close")}} for x in ts]}


def test_capital_history_fits_the_range_limit(monkeypatch):
    import time as _t
    real_sleep = asyncio.sleep
    monkeypatch.setattr(history.asyncio, "sleep", lambda s: real_sleep(0))
    since = _t.time() // 60 * 60 - 3000 * 60

    for limit in (1000, 300):  # the real limit, and a stricter one the halving must cope with
        fake = _FakeCapital(limit)
        df = asyncio.run(history._capital("GOLD", "MINUTE", since, "demo", fake))
        gaps = df.time.drop_duplicates().sort_values().diff().dropna()
        assert len(df) >= 2990 and gaps.max() <= 60, f"limit {limit}: missing candles"
        assert all((to - f) / pd.Timedelta(minutes=1) + 1 <= max(limit, 1000) for f, to in fake.asked[-3:])


def test_capital_history_empty_windows_and_real_errors(monkeypatch):
    import time as _t
    real_sleep = asyncio.sleep
    monkeypatch.setattr(history.asyncio, "sleep", lambda s: real_sleep(0))
    now = _t.time() // 60 * 60
    since = now - 3000 * 60
    weekend = (pd.Timestamp(since + 1000 * 60, unit="s", tz="UTC"), pd.Timestamp(since + 2000 * 60, unit="s", tz="UTC"))
    df = asyncio.run(history._capital("GOLD", "MINUTE", since, "demo", _FakeCapital(closed=weekend)))
    assert 1900 < len(df) < 2100                                    # the closed stretch is skipped, the rest kept
    with pytest.raises(RuntimeError, match="refused GOLD MINUTE history.*error.invalid.epic"):
        asyncio.run(history._capital("GOLD", "MINUTE", since, "demo", _FakeCapital(other_error=True)))
    everything = (pd.Timestamp(0, unit="s", tz="UTC"), pd.Timestamp(now + 3600, unit="s", tz="UTC"))
    with pytest.raises(RuntimeError, match="no MINUTE history for GOLD"):
        asyncio.run(history._capital("GOLD", "MINUTE", since, "demo", _FakeCapital(closed=everything)))
    assert asyncio.run(history._capital("GOLD", "MINUTE", since, "demo", _FakeCapital(closed=everything), allow_empty=True)).empty


def test_weekend_top_up_keeps_saved_history(cfg, tmp_path, monkeypatch):
    import time as _t
    monkeypatch.setattr(history, "DIR", tmp_path)
    now = _t.time()
    saved = pd.DataFrame({"time": [int(now - 7200 - 60 * k) for k in range(50)][::-1], "open": 1.0, "high": 1.0, "low": 1.0, "close": 1.0, "volume": 1})
    history.save("capital", "USD_JPY", "MINUTE", saved)
    monkeypatch.setattr(history.capital, "has_credentials", lambda: True)

    seen = []

    async def market_closed(epic, tf, since, env, session=None, allow_empty=False):
        seen.append(allow_empty)
        if not allow_empty:
            raise RuntimeError("no MINUTE history")
        return pd.DataFrame()

    monkeypatch.setattr(history, "_capital", market_closed)
    days = (now - saved.time.iloc[0] + 60) / 86400            # saved history covers the start: only the top-up runs
    out = asyncio.run(history.ensure("USD_JPY", "MINUTE", days, cfg))
    assert seen == [True] and len(out) == 50 and out.time.iloc[-1] == saved.time.iloc[-1]


def test_forward_test_verdicts():
    def t(mode, sym, r):
        return {"mode": mode, "symbol": sym, "r": r, "time": 0}
    live = [t("intraday", "XAU_USD", 0.5)] * 20 + [t("intraday", "EUR_USD", -0.3)] * 25 + [t("scalp", "GBP_USD", 0.1)] * 30 \
        + [t("scalp", "USD_JPY", 1.0)] * 3
    oos = lambda sym, e: {"out_of_sample": {"by_symbol": {sym: {"trades": 40, "expectancy": e, "win_rate": 50}}}}  # noqa: E731
    runs = [{"id": 9, "created": 2, "spec": {"mode": "scalp"}, "result": oos("GBP_USD", 0.6)},
            {"id": 8, "created": 1, "spec": {"mode": "intraday"}, "result": oos("XAU_USD", 0.4)},
            {"id": 7, "created": 0, "spec": {"mode": "scalp"}, "result": oos("GBP_USD", 0.0)}]   # older: ignored
    ft = performance.forward_test(live, runs)
    v = {(r["mode"], r["symbol"]): (r["verdict"], r["backtest"] and r["backtest"]["id"]) for r in ft["rows"]}
    assert v == {("intraday", "XAU_USD"): ("on track", 8), ("intraday", "EUR_USD"): ("losing", None),
                 ("scalp", "GBP_USD"): ("below backtest", 9), ("scalp", "USD_JPY"): ("collecting", None)}
    assert ft["total"]["trades"] == 78 and ft["verdict"] == "on track"


def test_edge_verdicts_and_exit_recommendation():
    st = lambda n, e, pf, tot: {"trades": n, "expectancy": e, "profit_factor": pf, "total_r": tot}  # noqa: E731
    run = lambda sym, full, part: {"spec": {"symbols": [sym], "mode": "intraday", "days": 120},  # noqa: E731
                                    "result": {"exits": {"full": full, "partial": part, "fraction": 0.5}}}
    rows = [backtest.edge_verdict(run("XAU_USD", st(49, 0.03, 1.06, 1.5), st(49, 0.2, 1.4, 9.8))),   # partial rescues it
            backtest.edge_verdict(run("EUR_USD", st(40, -0.3, 0.6, -12), st(40, -0.25, 0.7, -10))),
            backtest.edge_verdict(run("GBP_USD", st(12, 0.5, 2.0, 6), st(12, 0.4, 1.8, 5))),
            backtest.edge_verdict(run("USD_JPY", st(60, 0.05, 1.1, 3), st(60, 0.06, 1.1, 3.6)))]
    assert [(r["verdict"], r["best_exit"]) for r in rows] == [("keep on", "partial"), ("switch off", "full"),
                                                               ("too few trades", "full"), ("marginal", "full")]
    assert backtest.recommend_partial(rows) and not backtest.recommend_partial(rows[2:3])


def test_edge_verdict_prefers_a_filter_only_with_a_margin():
    st = lambda n, e: {"trades": n, "expectancy": e, "profit_factor": 1.3 if e > 0 else 0.8, "total_r": n * e}  # noqa: E731
    v = lambda name, n, e, h=(): {"stats": st(n, e), "skip_hours": list(h)}  # noqa: E731
    res = {"exits": {"full": st(60, 0.02), "partial": st(60, 0.0), "fraction": 0.5, "used": 0.0},
           "variants": {"none": v("none", 60, 0.02), "trend": v("trend", 35, 0.15), "chop": v("chop", 50, 0.05),
                        "hours": v("hours", 12, 0.9, [3]), "all": v("all", 30, 0.12, [3])}}
    r = backtest.edge_verdict({"spec": {"symbols": ["XAU_USD"], "mode": "intraday", "days": 120}, "result": res})
    assert r["best_filter"] == "trend" and r["verdict"] == "keep on" and r["filter"] == {"trend": True, "chop": False, "hours": []}
    res["variants"]["trend"] = v("trend", 35, 0.05)                   # +0.03R better: not enough margin
    res["variants"]["all"] = v("all", 30, 0.04, [3])
    r = backtest.edge_verdict({"spec": {"symbols": ["XAU_USD"], "mode": "intraday", "days": 120}, "result": res})
    assert r["best_filter"] == "none" and r["verdict"] == "marginal"


def test_filters_block_reasons():
    from jarvis.station import filters
    f = {"trend": True, "chop": True, "hours": [14]}
    assert filters.reasons(f, 1, 1, False, 9, "daily") == []
    assert filters.reasons(f, 1, -1, True, 14, "daily") == ["filter: daily trend against the trade", "filter: 1H market choppy",
                                                           "filter: 14:00 is a losing hour for this setup"]
    assert filters.reasons(filters.OFF, 1, -1, True, 14, "daily") == []
    trades = [{"time": 1_700_000_000_000 + k * 60_000, "r": -1.0} for k in range(4)]   # all in one hour
    assert filters.bad_hours(trades, "UTC") == [22] and filters.bad_hours(trades[:3], "UTC") == []
