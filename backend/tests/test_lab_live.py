import numpy as np
import pandas as pd

from jarvis.lab import live
from jarvis.lab.rules import Setup


def stats(n, e):
    return {"trades": n, "expectancy": e, "win_rate": 40, "profit_factor": 1.2, "total_r": n * e, "max_dd_r": -3}


def run(rid, key, syms, verdict, n, e, mine=None, tf="HOUR", params="rr=2.0"):
    by = {s: stats(*mine) if mine else stats(n // len(syms), e) for s in syms}
    return {"id": rid, "status": "done", "spec": {"tf": tf, "strategies": [key], "symbols": syms, "days": 730},
            "result": {"strategies": [{"key": key, "verdict": verdict, "summary": stats(n, e), "by_symbol": by,
                                       "folds": [{"params": params}]}]}}


def test_parse_reads_labels_back():
    assert live._parse("fast=9, slow=21, rr=exit rule") == {"fast": 9, "slow": 21, "rr": None}
    assert live._parse("zone=both, entry=ce, target=2.0, need_fvg=True") == {"zone": "both", "entry": "ce", "target": 2.0, "need_fvg": True}


def test_evidence_prefers_the_run_with_the_most_trades_on_this_market():
    runs = [run(3, "icc", ["XAU_USD"], "too few trades", 8, 0.5),                       # newest, tiny
            run(2, "icc", ["XAU_USD", "EUR_USD"], "marginal", 232, 0.04, mine=(117, 0.18)),
            run(1, "icc", ["EUR_USD"], "promising", 900, 0.2)]                           # never saw gold
    ev = live.evidence(runs, "icc", "XAU_USD", "HOUR")
    assert ev["run"] == 2 and ev["this_market"]["trades"] == 117
    assert live.evidence(runs, "icc", "XAU_USD", "MINUTE_15") is None


def test_basis_rules():
    ok = {"verdict": "promising", "summary": stats(200, 0.2), "this_market": stats(50, 0.1)}
    assert live.basis(ok) == "tested"
    assert live.basis({**ok, "this_market": stats(50, -0.1)}) is None  # lost on this very market
    local = {"verdict": "doesn't work", "summary": stats(2000, -0.03), "this_market": stats(295, 0.12)}
    assert live.basis(local) == "this market only"
    assert live.basis({**local, "this_market": stats(60, 0.3)}) is None  # too few trades here
    assert live.basis({"verdict": "marginal", "summary": stats(300, 0.02), "this_market": stats(40, 0.05)}) is None


def row(key, call, live_=True, trusted=True, e=0.15):
    ev = {"summary": stats(150, e), "this_market": stats(150, e), "verdict": "promising"} if trusted else None
    return {"key": key, "name": key.upper(), "trusted": trusted, "basis": "tested" if trusted else None, "evidence": ev,
            "setup": {"call": call, "live": live_, "entry": 100.0, "stop": 99.0, "target": 102.0, "kind": "market", "status": "in trade"}}


def test_decision_needs_a_trusted_live_setup_with_the_trend():
    assert live.decide([row("a", "BUY", trusted=False)], "BUY")["call"] == "NO TRADE"
    assert live.decide([row("a", "BUY", live_=False)], "BUY")["call"] == "NO TRADE"
    assert live.decide([row("a", "BUY"), row("b", "SELL")], "BUY")["call"] == "NO TRADE"   # disagree
    against = live.decide([row("a", "BUY")], "SELL")
    assert against["call"] == "NO TRADE" and "daily trend is SELL" in against["why"]
    d = live.decide([row("a", "BUY", e=0.1), row("b", "BUY", e=0.3), row("c", "SELL", trusted=False)], "BUY")
    assert d["call"] == "BUY" and d["rules"] == ["a", "b"] and "Levels from B" in d["why"]


def test_setup_status_follows_the_bars_after_it():
    c = np.array([100, 100, 101, 102, 103, 104.0])
    df = pd.DataFrame({"time": np.arange(6) * 3600, "open": c, "high": c + 0.5, "low": c - 0.5, "close": c})
    assert live._status(Setup(5, 1, 99.0, 110.0), df, None, None)[0] == "fresh: enter at the next open"
    assert live._status(Setup(1, 1, 99.0, 110.0), df, None, None) == ("in trade", 2)
    assert live._status(Setup(1, 1, 99.0, 102.0), df, None, None)[0] == "target hit"
    assert live._status(Setup(1, -1, 101.0, 90.0), df, None, None)[0] == "stopped out"
    assert live._status(Setup(3, 1, 90.0, 110.0, entry=95.0, expiry=5), df, None, None)[0] == "limit waiting"
    assert live._status(Setup(0, 1, 90.0, 110.0, entry=95.0, expiry=2), df, None, None)[0] == "limit expired"


def test_analyse_runs_every_short_term_rule_on_live_candles():
    rng = np.random.default_rng(1)
    c = 100 * np.exp(np.cumsum(0.003 * rng.standard_normal(300)))
    o = np.r_[100, c[:-1]]
    df = pd.DataFrame({"time": 1_800_000_000 + 900 * np.arange(300), "open": o, "high": np.maximum(o, c) * 1.001,
                       "low": np.minimum(o, c) * 0.999, "close": c})
    a = live.analyse("XAU_USD", "MINUTE_15", df, [], "SELL", float(c[-1]))
    assert len(a["rules"]) == 8 and "trend_following" not in [r["key"] for r in a["rules"]]
    assert a["decision"]["call"] == "NO TRADE" and "positive tested record" in a["decision"]["why"]


def candles(n=300, bar_s=3600, drift=0.0, seed=3):
    rng = np.random.default_rng(seed)
    c = 100 * np.exp(np.cumsum(drift + 0.004 * rng.standard_normal(n)))
    o = np.r_[100, c[:-1]]
    return pd.DataFrame({"time": 1_800_000_000 + bar_s * np.arange(n), "open": o, "high": np.maximum(o, c) * 1.002,
                         "low": np.minimum(o, c) * 0.998, "close": c})


def test_single_strategy_gives_its_own_call_chart_and_lines():
    df = candles()
    for key in ("ema_cross", "donchian_breakout", "bollinger_reversion", "smc_order_block", "icc"):
        r = live.single(key, "XAU_USD", "HOUR", df, [])
        assert r["decision"]["call"] in ("BUY", "SELL", "HOLD")
        assert len(r["candles"]) == 200 and r["candles"][-1]["time"] == int(df.time.iat[-1])
        assert all(m["time"] >= r["candles"][0]["time"] for m in r["marks"])
        live_marks = [m for m in r["marks"] if m["live"]]
        assert (r["decision"]["call"] == "HOLD") == (not live_marks)
    assert set(live.single("ema_cross", "XAU_USD", "HOUR", df, [])["lines"]) == {"EMA 9", "EMA 21"}
    assert len(live.single("bollinger_reversion", "XAU_USD", "HOUR", df, [])["lines"]) == 3


def test_single_trend_following_reads_the_daily_trend():
    up = candles(300, 86_400, drift=0.003)
    r = live.single("trend_following", "XAU_USD", "DAY", up, [])
    assert r["decision"]["call"] == "BUY"  # from a live entry in view, or else from the trend score
