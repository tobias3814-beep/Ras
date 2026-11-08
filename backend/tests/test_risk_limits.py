import asyncio
import re

import numpy as np
import pandas as pd
import pytest

from jarvis import performance
from jarvis.bus import Bus
from jarvis.execution import Executor
from jarvis.feeds.capital import QuoteCheck, bad_bars
from jarvis.halt import halt
from jarvis.lab import account, engine, stats
from jarvis.lab.rules import Plan, Setup
from jarvis.limits import AccountWatch, entry_block
from jarvis.paper import Paper
from jarvis.station.store import Store
from jarvis.voice import announce

INS = {"XAU_USD": {"pip": 0.1}, "XAG_USD": {"pip": 0.01}, "EUR_USD": {"pip": 0.0001}}
CFG = {"auto_follow": True, "fx": {"balance": 100, "risk_pct": 1.0, "currency": "USD", "slippage_pips": 0, "max_risk_pct": 5},
       "crypto": {"balance": 1000, "risk_pct": 1.0, "currency": "USDT", "max_leverage": 3}}


def pos(sym, risk, side=1, mode="trend"):
    return {"symbol": sym, "risk": risk, "side": side, "mode": mode}


# ---------- entry limits ----------
def test_total_open_risk_is_capped():
    open_ = [pos("XAU_USD", 5), pos("EUR_USD", 5)]
    assert entry_block(open_, "WTI_USD", "trend", 1, 1, 100) == ""
    assert "total open risk" in entry_block(open_, "WTI_USD", "trend", 1, 3, 100)


def test_markets_that_move_together_share_a_cap():
    assert "metals" in entry_block([pos("XAU_USD", 5)], "XAG_USD", "trend", 1, 2, 100)
    assert entry_block([pos("XAU_USD", 5)], "EUR_USD", "trend", 1, 2, 100) == ""


def test_short_term_desks_cannot_trade_against_an_open_position_but_trend_can():
    open_ = [pos("EUR_USD", 1, side=-1)]
    assert "opposite" in entry_block(open_, "EUR_USD", "scalp", 1, 1, 100)
    assert entry_block(open_, "EUR_USD", "trend", 1, 1, 100) == ""
    assert entry_block(open_, "EUR_USD", "scalp", -1, 1, 100) == ""


def test_config_can_change_a_limit():
    assert entry_block([], "WTI_USD", "trend", 1, 3, 100, {"max_open_risk_pct": 2}) != ""


def test_paper_journals_a_trade_a_limit_stopped(tmp_path):
    store = Store(tmp_path / "s.sqlite")
    p = Paper(Bus(), store, CFG, INS, {}, 0.0)
    p.set_rules({"XAU_USD": {}, "XAG_USD": {}})
    p.on_quote("XAU_USD", 2000.0, 2000.0, 0)
    p.on_quote("XAG_USD", 50.0, 50.0, 0)
    sig = lambda sym, e, st: store.insert({"symbol": sym, "mode": "trend", "call": "BUY", "created": 0, "expires": 10**15,  # noqa: E731
                                           "levels": {"entry": e, "stop": st, "tp1": None, "tp2": None}})
    p.open[99] = {"division": "fx", "signal_id": 99, "symbol": "XAU_USD", "side": 1, "risk": 5.5, "margin": 0.0, "mode": "trend"}
    s = sig("XAG_USD", 50.0, 49.0)
    p.on_signal(s, 1)
    assert s["id"] not in p.open and "metals" in store.journal([s["id"]])[s["id"]]["trade_log"]


# ---------- account limits ----------
DAY = 1_800_000_000_000 - 1_800_000_000_000 % 86_400_000


def test_daily_loss_counts_from_the_days_start_and_rolls_over(tmp_path):
    w = AccountWatch(Store(tmp_path / "w.sqlite"), None, "UTC")
    assert w.check(100.0, DAY) == "" and w.check(96.0, DAY + 1000) == ""
    assert "daily loss" in w.check(94.0, DAY + 2000)
    assert w.check(94.0, DAY + 86_400_000) == ""  # a new day


def test_drawdown_counts_from_the_peak_over_days(tmp_path):
    w = AccountWatch(Store(tmp_path / "w.sqlite"), None, "UTC")
    w.check(200.0, DAY)
    for d in range(1, 40):
        assert w.check(200.0 - d * 1.5, DAY + d * 86_400_000) == ""
    assert "drawdown" in w.check(135.0, DAY + 41 * 86_400_000)


def test_baselines_survive_a_restart_and_resume_starts_them_again(tmp_path):
    s = Store(tmp_path / "w.sqlite")
    w = AccountWatch(s, None, "UTC")
    w.check(100.0, DAY)
    w2 = AccountWatch(s, None, "UTC")
    assert "daily loss" in w2.check(90.0, DAY + 1)
    w2.reset(90.0, DAY + 2)
    assert w2.check(90.0, DAY + 3) == "" and AccountWatch(s, None, "UTC").check(89.0, DAY + 4) == ""


def test_a_broken_limit_is_announced():
    said = announce.classify({"type": "limit", "why": "daily loss limit: down $6.00 today, over 5%", "created": 10}, set(), 0)
    assert "daily loss limit" in said[0] and said[1] == 4


# ---------- data checks ----------
def q(bid, ask, sym="XAU_USD"):
    return {"symbol": sym, "bid": bid, "ask": ask}


def test_impossible_quotes_are_dropped():
    c = QuoteCheck()
    assert c.ok(q(100.0, 100.1)) and not c.ok(q(100.2, 100.1)) and not c.ok(q(0, 100.1)) and not c.ok(q(-1, 100.1))


def test_one_bad_tick_is_ignored_but_a_real_gap_gets_through():
    c = QuoteCheck()
    assert c.ok(q(100.0, 100.1))
    assert not c.ok(q(50.0, 50.1)) and c.ok(q(100.1, 100.2))  # a spike that came straight back
    assert not c.ok(q(90.0, 90.1)) and c.ok(q(90.1, 90.2)) and c.ok(q(90.2, 90.3))  # Monday's gap, confirmed by the next tick


def test_bad_candles_are_found():
    good = pd.DataFrame({"open": [1.0, 1.1], "high": [1.2, 1.2], "low": [0.9, 1.0], "close": [1.1, 1.1]})
    assert bad_bars({"HOUR": good}) == []
    for k, v in (("high", 1.0), ("low", 1.15), ("close", np.nan), ("open", -1.0)):
        bad = good.copy()
        bad.loc[1, k] = v
        assert bad_bars({"HOUR": good, "DAY": bad}) == ["DAY"], k


# ---------- account simulation ----------
def t(sym="EUR_USD", time=0, closed=1, r=1.0, entry=1.1, risk_px=0.01, call="BUY"):
    return {"symbol": sym, "time": time, "closed_at": closed, "r": r, "entry": entry, "risk_px": risk_px, "call": call, "mode": "trend"}


SPEC = {"balance": 100, "risk_pct": 0.5, "max_risk_pct": 2.0, "rules": {"EUR_USD": {"min_size": 100, "step": 100, "margin_pct": 3.3},
                                                                       "WTI_USD": {"min_size": 1, "step": 0.1, "margin_pct": 10}}}


def test_the_minimum_size_decides_what_the_account_can_take():
    out = account.simulate([t(risk_px=0.01, r=1.0), t("WTI_USD", time=2, closed=3, entry=70.0, risk_px=7.0)], SPEC)
    assert out["taken"] == 1 and out["too_big"] == 1  # 1 barrel at a $7 stop risks 7% of $100
    assert out["final"] == pytest.approx(101.0)  # 100 units x 0.01 = $1 risk, up 1R


def test_margin_and_overlaps_are_shared_between_trades():
    spec = {**SPEC, "rules": {"EUR_USD": {"min_size": 100, "step": 100, "margin_pct": 60}}, "limits": {"max_cluster_risk_pct": 100, "max_open_risk_pct": 100}}
    out = account.simulate([t(closed=10), t(time=1, closed=11)], spec)
    assert out["taken"] == 1 and out["no_margin"] == 1


def test_account_limits_apply_inside_the_simulation():
    out = account.simulate([t(closed=10), t("GBP_USD", time=1, closed=11)], {**SPEC, "limits": {"max_cluster_risk_pct": 1.4}})
    assert out["taken"] == 1 and out["limits"] == 1


def test_drawdown_is_reported_in_percent_of_the_account():
    out = account.simulate([t(closed=1, r=-5.0), t(time=2, closed=3, r=-5.0)], SPEC)
    assert out["max_dd_pct"] > 5 and out["final"] < 100 and account.simulate([], SPEC) is None


def test_drawdown_range_grows_with_a_worse_sequence_and_needs_enough_trades():
    rng = np.random.default_rng(1)
    good = stats.drawdown_range(list(rng.choice([2.0, -1.0], 300, p=[0.5, 0.5])))
    poor = stats.drawdown_range(list(rng.choice([2.0, -1.0], 300, p=[0.3, 0.7])))
    assert good["bad"] >= good["typical"] > 0 and poor["typical"] > good["typical"]
    assert stats.drawdown_range([1.0] * 5) is None


# ---------- random entries, cost stress ----------
def walk(n=800):
    rng = np.random.default_rng(2)
    c = 100 * np.exp(np.cumsum(0.003 * rng.standard_normal(n)))
    df = pd.DataFrame({"time": 3600 * np.arange(n), "open": np.r_[100, c[:-1]], "close": c, "volume": 1})
    df["high"], df["low"] = df[["open", "close"]].max(axis=1) * 1.001, df[["open", "close"]].min(axis=1) * 0.999
    for s, x in (("bid", -0.01), ("ask", 0.01)):
        for k in ("open", "high", "low", "close"):
            df[f"{s}_{k}"] = df[k] + x
    return df


def test_widen_multiplies_the_spread_and_leaves_the_mid():
    df = walk(10)
    w = engine.widen(df, 3)
    assert (w.ask_close - w.bid_close).iloc[0] == pytest.approx(0.06) and w.close.iloc[0] == df.close.iloc[0]


def test_wider_spreads_cost_more():
    df, plan = walk(), Plan([Setup(i, 1, 0.0, None, max_bars=5) for i in range(10, 700, 20)])
    plan = Plan([Setup(s.i, 1, float(df.close[s.i]) - 2, float(df.close[s.i]) + 2, max_bars=5) for s in plan.setups])
    rs = lambda d: np.mean([x["r"] for x in engine.simulate("X", "k", d, plan, 0.01, 0, 0.0, 3600)])  # noqa: E731
    assert rs(engine.widen(df, 3)) < rs(df)


def test_random_plan_keeps_count_and_distances():
    df = walk()
    plan = Plan([Setup(50, 1, float(df.close[50]) - 2, float(df.close[50]) + 4, max_bars=9), Setup(60, -1, float(df.close[60]) + 3, None)])
    out = engine.random_plan(df, plan, 10, np.random.default_rng(1)).setups
    assert len(out) == 2 and out[0].max_bars == 9
    for s, risk in zip(out, (2, 3)):
        assert (float(df.close[s.i]) - s.stop) * s.side == pytest.approx(risk)
    assert (out[0].target - float(df.close[out[0].i])) * out[0].side == pytest.approx(4) and out[1].target is None


# ---------- walk-forward honesty ----------
def test_a_trade_running_into_the_fold_is_purged_from_in_sample():
    mk = lambda i, ex, r: {"symbol": "X", "i": i, "i_exit": ex, "r": r, "time": i, "closed_at": ex, "days": 1}  # noqa: E731
    bridge = [mk(i, 99, 9.0) for i in range(0, 40, 4)]  # looks superb only because it runs into the test period
    plain = [mk(i, i + 1, 0.1) for i in range(0, 90)]
    res = engine.evaluate("icc", [({"rr": 1.5}, bridge), ({"rr": 3.0}, plain)], {"X": (0, 100)}, 2, "month")
    assert all(f["params"] == "rr=3.0" for f in res["folds"])


def test_a_missing_spread_never_borrows_from_later_bars():
    rows = [(100.0, 100.2, 99.8, 100.0)] * 6
    o, h, lo, c = (np.array(x) for x in zip(*rows))
    df = pd.DataFrame({"time": np.arange(6), "open": o, "high": h, "low": lo, "close": c, "volume": 1})
    for s, x in (("bid", 0.0), ("ask", 0.0)):
        for k in ("open", "high", "low", "close"):
            df[f"{s}_{k}"] = df[k] + x
    df.loc[1, ["ask_close", "bid_close"]] = [100.1, 99.9]
    df.loc[4, ["ask_close", "bid_close"]] = [101.0, 99.0]  # a much wider spread later on
    out = engine.with_spread(df)
    assert out.ask_close[3] - out.bid_close[3] == pytest.approx(0.2, rel=1e-3)  # bar 3 knows only bar 1
    assert out.ask_close[5] - out.bid_close[5] > 0.2


# ---------- alarms ----------
def test_alarms_fire_on_risk_not_on_returns():
    live = lambda dd, sd, n=20: {"trades": n, "max_dd_r": dd, "sd": sd}  # noqa: E731
    bt = {"drawdown_bad": 8.0, "sd": 1.0}
    assert performance.alarms(live(-5.0, 1.2), bt, [0.01] * 20) == []
    assert "bad case" in performance.alarms(live(-9.0, 1.2), bt, [])[0]
    assert "swing" in performance.alarms(live(-1.0, 2.5), bt, [])[0]
    assert "slippage" in performance.alarms(live(-1.0, 1.0), None, [0.2] * 12)[0]
    assert performance.alarms(live(-1.0, 2.5, n=5), bt, [0.2] * 3) == []  # too few to say


# ---------- replay parity ----------
def test_replay_sees_as_many_candles_as_live():
    import pathlib
    import jarvis.backtest as bt
    src = (pathlib.Path(bt.__file__).parents[0] / "feeds" / "capital.py").read_text()
    assert {int(x) for x in re.findall(r'"max": (\d+)', src)} == {bt.LIVE_BARS}


def test_live_trend_score_matches_the_tested_rule_on_every_bar():
    from jarvis.lab.rules import trend_following
    from jarvis.trend import score
    rng = np.random.default_rng(4)
    c = 100 * np.exp(np.cumsum(0.01 * rng.standard_normal(700)))
    o = np.r_[c[0], c[:-1]]
    df = pd.DataFrame({"time": 86_400 * np.arange(700), "open": o, "high": np.maximum(o, c) * 1.001, "low": np.minimum(o, c) * 0.999, "close": c})
    plan = trend_following(df, {"lookback": "blend", "atr_stop": 3.0, "_bar_s": 86_400})
    for i in range(253, 700, 7):
        s = score(df.close.iloc[: i + 1])
        assert np.sign(s) == (1 if not plan.exit_long[i] and plan.exit_short[i] else -1 if plan.exit_long[i] else 0)


# ---------- dormant crypto execution ----------
class FakeExchange:
    def __init__(self, size=0):
        self.size = size

    async def fetch_positions(self, _):
        return [{"symbol": "BTC/USDT:USDT", "contracts": self.size}]


def test_an_order_stuck_submitting_gets_resolved(tmp_path, monkeypatch):
    from jarvis import execution
    monkeypatch.setattr(execution, "has_keys", lambda: False)
    cfg = {"environment": "demo", "confirm_phrase": "x", "risk_pct": 1, "max_notional_usdt": 1, "leverage": 1, "trailing_pct": 1}
    store = Store(tmp_path / "e.sqlite")
    for sid, size, want in ((1, 0, "failed"), (2, 1, "open")):
        ex = Executor(Bus(), store, cfg, halt, exchange=FakeExchange(size))
        x = {"signal_id": sid, "symbol": "perp:BTC/USDT:USDT", "status": "submitting", "opened": 1}
        store.save_execution(x)
        asyncio.run(ex.check_open())
        assert store.execution_for(sid)["status"] == want, execution.__name__
        store.db.execute("DELETE FROM executions")


def test_equity_is_only_trusted_once_open_positions_have_prices(tmp_path):
    store = Store(tmp_path / "m.sqlite")
    p = Paper(Bus(), store, CFG, INS, {}, 0.0)
    assert p.marks_known()
    p.open[1] = {"division": "fx", "signal_id": 1, "symbol": "EUR_USD", "side": 1, "risk": 1.0, "margin": 0.0, "mode": "trend"}
    assert not p.marks_known()  # just after a start: it would read as flat at the entry price
    p.quotes["EUR_USD"] = (1.1, 1.1001)
    assert p.marks_known()


# ---------- trade-count alarm ----------
def test_count_alarm_speaks_only_on_a_large_gap():
    assert "Only 2" in performance.count_alarm(2, 30.0)
    assert performance.count_alarm(20, 30.0) == ""  # within the range
    assert performance.count_alarm(0, 5.0) == ""  # too few expected to read anything into silence
    assert "9 signals" in performance.count_alarm(9, 1.0)  # a desk firing far too often
    assert performance.count_alarm(4, 0.1) == "" and performance.count_alarm(3, None) == ""


def test_forward_test_scales_the_backtest_pace_to_time_online_and_shows_silent_desks():
    oos = {"out_of_sample": {"by_symbol": {"EUR_USD": {"trades": 300, "expectancy": 0.1, "win_rate": 50}}}}
    runs = [{"id": 1, "created": 1, "spec": {"mode": "scalp", "days": 60, "folds": 3}, "result": oos}]  # 300 trades in 45 days
    ft = performance.forward_test([], runs, window_days=9.0, enabled=[("scalp", "EUR_USD")])
    assert "about 60" in ft["rows"][0]["alarms"][0] and ft["rows"][0]["verdict"] == "collecting"
    ok = performance.forward_test([], runs, window_days=9.0, signals={("scalp", "EUR_USD"): 55}, enabled=[("scalp", "EUR_USD")])
    assert ok["rows"][0]["alarms"] == []  # skipped signals count: a small account shouldn't look like a quiet desk
    assert performance.forward_test([], runs, enabled=[("scalp", "EUR_USD")])["rows"][0]["alarms"] == []


# ---------- random entries on the desk replays ----------
def m1_bars(n=3000, seed=2):
    rng = np.random.default_rng(seed)
    c = 100 * np.exp(np.cumsum(0.0006 * rng.standard_normal(n)))
    df = pd.DataFrame({"time": 1_800_000_000 + 60 * np.arange(n), "open": np.r_[100, c[:-1]], "close": c, "volume": 1})
    df["high"], df["low"] = df[["open", "close"]].max(axis=1) * 1.0003, df[["open", "close"]].min(axis=1) * 0.9997
    for s, x in (("bid", -0.001), ("ask", 0.001)):
        for k in ("open", "high", "low", "close"):
            df[f"{s}_{k}"] = df[k] + x
    return df


def test_random_cands_keep_the_hour_stop_distance_and_score():
    from jarvis.backtest import Cand, random_cands
    df = m1_bars()
    cands = [Cand(i, 1, 70 + i % 5, float(df.close[i]) - 0.5, atr5=0.2, session="x") for i in range(400, 2800, 100)] + [Cand(2900, 1, 70, 90.0, veto=["max spread"])]
    out = random_cands(cands, df, np.random.default_rng(1))
    assert len(out) == len(cands) - 1 and [c.i for c in out] == sorted(c.i for c in out)  # vetoed setups don't count
    assert sorted(c.score for c in out) == sorted(c.score for c in cands[:-1]) and {c.direction for c in out} == {1, -1}
    assert all(c.i >= 400 for c in out)
    hours = (df.time.to_numpy() // 3600) % 24
    assert sorted(hours[c.i] for c in out) == sorted(hours[c.i] for c in cands[:-1])
    for c in out:
        assert (float(df.close[c.i]) - c.stop) * c.direction == pytest.approx(0.5, abs=1e-9)
    assert random_cands([], df, np.random.default_rng(1)) == []


def test_random_entries_compare_the_rule_with_its_shuffles(monkeypatch):
    from jarvis import backtest
    df = m1_bars()
    monkeypatch.setattr(backtest, "simulate", lambda *a, **k: [{"r": 0.0}])
    monkeypatch.setattr(backtest, "random_cands", lambda c, b, r: c)
    data = {"X": (df, [backtest.Cand(400, 1, 70, 99.0)])}
    assert backtest.random_entries(data, 0.3, 60, {}, "scalp", 0.0) == {"rule": 0.3, "expectancy": 0.0, "top": 0.0, "beats": True}
    assert backtest.random_entries(data, -0.1, 60, {}, "scalp", 0.0)["beats"] is False
    assert backtest.random_entries(data, None, 60, {}, "scalp", 0.0) is None


# ---------- replay against live ----------
def live_cfg():
    import yaml
    from jarvis.config import BUNDLED
    return yaml.safe_load((BUNDLED / "config.yaml").read_text())


def fx_m1(n=6000, seed=4):
    """A wavy, noisy EUR/USD through the London and New York sessions, so the scalp rules actually fire."""
    rng = np.random.default_rng(seed)
    c = 1.1 * np.exp(np.cumsum(np.sin(np.arange(n) / 90) * 0.0001 + 0.0003 * rng.standard_normal(n)))
    start = 1_800_000_000 - 1_800_000_000 % 86_400
    df = pd.DataFrame({"time": start + 60 * np.arange(n), "open": np.r_[c[0], c[:-1]], "close": c, "volume": 100 + rng.integers(0, 200, n)})
    df["high"], df["low"] = df[["open", "close"]].max(axis=1) + 0.00018, df[["open", "close"]].min(axis=1) - 0.00018
    for s, x in (("bid", -0.00002), ("ask", 0.00002)):
        for k in ("open", "high", "low", "close"):
            df[f"{s}_{k}"] = df[k] + x
    return df


def test_the_live_scalp_desk_and_the_replay_call_the_same_trades(tmp_path):
    """Live builds its frames like the poller (the last 300 closed bars of each timeframe); replay must reach the same
    decision from one minute series. This is the check for indicator windows drifting apart."""
    from jarvis import backtest
    from jarvis.scalp.book import Book
    from jarvis.scalp.fx import FxScalpDesk
    from jarvis.station.context import Context
    cfg, sym = live_cfg(), "EUR_USD"
    df = fx_m1()
    cands = {c.i: c for c in backtest.scalp_candidates(sym, df, cfg, "fx", start=4500)}
    assert cands, "the sample must contain setups for this test to mean anything"
    store, bus = Store(tmp_path / "p.sqlite"), Bus()
    desk = FxScalpDesk(bus, Book(bus, store, ("scalp",), cfg["scalp"]), cfg["capital"]["instruments"], cfg["scalp"], cfg["station"], Context(),
                       {}, {sym: {"scalp": True}}, lambda: [], "UTC", {})
    m5, m15, h1 = (backtest.resample(df, s) for s in (300, 900, 3600))
    compared = 0
    for i in list(cands)[:40]:
        t = float(df.time.iloc[i] + 60)
        desk.frames[sym] = {"MINUTE": df.iloc[i - 299:i + 1], "MINUTE_5": backtest._closed(m5, 300, t, 300),
                            "MINUTE_15": backtest._closed(m15, 900, t, 300), "HOUR": backtest._closed(h1, 3600, t, 300)}
        d = desk.evaluate(sym, t)
        assert d["lean"] == ("BUY" if cands[i].direction > 0 else "SELL") and d["confidence"] == cands[i].score, i
        compared += 1
    assert compared == len(list(cands)[:40])


def signal_at(sig_id, mode, call, created):
    return {"id": sig_id, "symbol": "EUR_USD", "mode": mode, "call": call, "created": created}


def test_parity_check_sorts_live_signals_by_what_the_replay_makes_of_them(monkeypatch):
    from jarvis import parity
    cfg = live_cfg()
    df = fx_m1()
    monkeypatch.setattr(parity, "_replay_calls", lambda sig, d, i, c: {1: ["BUY"], 2: ["SELL"], 3: []}[sig["id"]])
    at = int((df.time.iloc[5000] + 60) * 1000)
    out = parity.check([signal_at(1, "scalp", "BUY", at), signal_at(2, "scalp", "BUY", at), signal_at(3, "scalp", "BUY", at),
                        signal_at(4, "crypto", "BUY", at),  # not checked
                        signal_at(5, "scalp", "BUY", int(df.time.iloc[0] * 1000) - 10**8)],  # before the history
                       lambda s, t: df, cfg)
    assert [r["result"] for r in out["rows"]] == ["match", "opposite", "no setup", "no data"]
    assert (out["checked"], out["matched"], out["no_data"]) == (3, 1, 1)


def test_parity_reruns_only_candles_up_to_the_signal(monkeypatch):
    from jarvis import parity
    df = fx_m1(1000)
    seen = {}

    def fake(sig, d, i, c):
        seen.update(i=i, last=d.time.iloc[i])
        return []
    monkeypatch.setattr(parity, "_replay_calls", fake)
    at_s = float(df.time.iloc[800]) + 60 + 2  # 2s after that candle closed
    parity.check([signal_at(1, "scalp", "BUY", int(at_s * 1000))], lambda s, t: df, {})
    assert seen["i"] == 800


def test_a_failed_download_is_reported_not_fatal():
    from jarvis import parity

    def boom(s, t):
        raise RuntimeError("rate limited")
    out = parity.check([signal_at(1, "intraday", "BUY", 10**12)], boom, {})
    assert out["rows"][0]["result"] == "no data" and "rate limited" in out["rows"][0]["why"] and out["checked"] == 0


def test_parity_trend_uses_the_live_trend_score():
    from jarvis import parity
    c = np.linspace(100, 200, 500)
    df = pd.DataFrame({"time": 86_400 * np.arange(500), "open": c, "high": c, "low": c, "close": c, "volume": 1})
    assert parity._replay_calls({"mode": "trend", "symbol": "EUR_USD"}, df, 450, {}) == ["BUY"]
