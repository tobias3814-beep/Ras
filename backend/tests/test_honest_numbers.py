import numpy as np
import pandas as pd
import pytest

from jarvis import performance
from jarvis.bus import Bus
from jarvis.feeds.capital import parse_rules
from jarvis.feeds.news import clean_title
from jarvis.lab import engine, stats
from jarvis.learning import Learning
from jarvis.paper import DAY_MS, Paper
from jarvis.station.agents import RiskReport, Vote
from jarvis.station.engine import Station
from jarvis.station.store import Store

INS = {"XAU_USD": {"pip": 0.1}, "EUR_USD": {"pip": 0.0001}}
CFG = {"auto_follow": True, "fx": {"balance": 10_000, "risk_pct": 1.0, "currency": "USD", "slippage_pips": 0},
       "crypto": {"balance": 1000, "risk_pct": 1.0, "currency": "USDT", "max_leverage": 3}}
T0 = 1_800_000_000_000 - 1_800_000_000_000 % DAY_MS  # a midnight UTC


def paper(tmp_path, rules):
    store = Store(tmp_path / "s.sqlite")
    p = Paper(Bus(), store, CFG, INS, {}, 0.0)
    p.set_rules({"XAU_USD": rules})
    p.on_quote("XAU_USD", 2000.0, 2000.0, 0)
    return p, store


def signal(store, call="BUY"):
    return store.insert({"symbol": "XAU_USD", "mode": "trend", "call": call, "created": T0, "expires": 10**15,
                         "levels": {"entry": 2000.0, "stop": 1900.0 if call == "BUY" else 2100.0, "tp1": None, "tp2": None}})


# ---------- financing ----------
def test_rules_read_the_brokers_overnight_rates():
    r = parse_rules({"dealingRules": {"minDealSize": {"value": 1}, "minSizeIncrement": {"value": 0.1}},
                     "instrument": {"marginFactor": 10, "marginFactorUnit": "PERCENTAGE",
                                    "overnightFee": {"longRate": -0.0152, "shortRate": 0.007}}})
    assert r == {"min_size": 1, "step": 0.1, "margin_pct": 10, "fin_long": -0.0152, "fin_short": 0.007}


def test_a_long_pays_and_a_short_earns_per_night_at_the_brokers_rate(tmp_path):
    p, store = paper(tmp_path, {"fin_long": -0.02, "fin_short": 0.01})
    p.on_signal(signal(store), T0)
    pos = p.open[1]
    notional = pos["units"] * pos["entry"]
    assert p.financing(pos, T0 + 3_600_000) == 0  # same day, before the 22:00 UTC roll
    assert p.financing(pos, T0 + 3 * DAY_MS) == pytest.approx(-0.02 / 100 * notional * 3)
    p.on_signal(signal(store, "SELL"), T0)
    assert p.financing(p.open[2], T0 + 3 * DAY_MS) == pytest.approx(0.01 / 100 * p.open[2]["units"] * p.open[2]["entry"] * 3)


def test_financing_reaches_pnl_and_the_open_account(tmp_path):
    p, store = paper(tmp_path, {"fin_long": -0.02, "fin_short": 0.01})
    p.on_signal(signal(store), T0)
    pos = p.open[1]
    cost = 0.02 / 100 * pos["units"] * pos["entry"] * 10
    p._close(pos, 2000.0, "test", T0 + 10 * DAY_MS, slip=False)
    assert pos["pnl"] == pytest.approx(-cost, abs=0.01) and pos["r_net"] == pytest.approx(-cost / pos["risk"], abs=1e-3)


def test_unknown_rate_falls_back_to_a_cost_not_to_free(tmp_path):
    p, store = paper(tmp_path, {})
    p.on_signal(signal(store), T0)
    assert p.financing(p.open[1], T0 + 365 * DAY_MS) == pytest.approx(-0.04 * p.open[1]["units"] * p.open[1]["entry"], rel=0.01)


def test_lab_charges_long_and_short_their_own_rates():
    n = 12
    df = pd.DataFrame({"time": 86_400 * np.arange(n), "open": 100.0, "high": 100.5, "low": 99.5, "close": 100.0, "volume": 1})
    for s in ("bid", "ask"):
        for k in ("open", "high", "low", "close"):
            df[f"{s}_{k}"] = df[k]
    from jarvis.lab.rules import Plan, Setup
    run = lambda side: engine.simulate("X", "k", df, Plan([Setup(0, side, 100 - side * 10, None)]), 0.01, 0, 10.0, 86_400, funding_short=-5.0)[0]  # noqa: E731
    assert run(1)["r"] < 0 < run(-1)["r"]  # long pays 10%/yr, short earns 5%/yr


# ---------- forward test ----------
def t(mode, sym, r):
    return {"mode": mode, "symbol": sym, "r": r, "time": 0}


def test_a_small_gap_inside_the_noise_is_not_a_verdict():
    rng = np.random.default_rng(1)
    live = [t("trend", "XAU_USD", float(x)) for x in rng.choice([2.0, -1.0], 40)]
    ft = performance.forward_test(live, [])
    assert ft["rows"][0]["verdict"] in ("can't tell yet", "on track") and ft["rows"][0]["range"][0] < ft["rows"][0]["range"][1]
    assert performance.forward_test(live[:19], [])["rows"][0]["verdict"] == "collecting"


def test_clear_losses_still_say_losing():
    live = [t("trend", "XAU_USD", -1.0 + 0.01 * (i % 3)) for i in range(25)]
    assert performance.forward_test(live, [])["rows"][0]["verdict"] == "losing"


def test_forward_test_uses_net_fills_and_ignores_forced_closes():
    base = {"symbol": "XAU_USD", "mode": "trend", "opened": 10, "closed": 20, "pnl": -0.5, "risk": 0.5}
    trades = performance.paper_trades([{**base, "reason": "stop", "r_net": -1.1}, {**base, "reason": "kill switch"},
                                       {**base, "reason": "account reset"}, {**base, "reason": "stop"},
                                       {**base, "reason": "stop", "opened": 1}, {**base, "closed": None}], since=5)
    assert [x["r"] for x in trades] == [-1.1, -1.0]


def test_feasibility_separates_the_strategy_from_what_the_account_could_take():
    sig = lambda i, status, r: {"id": i, "mode": "trend", "symbol": "WTI_USD", "status": status, "result_r": r}  # noqa: E731
    f = performance.feasibility([sig(1, "hit_sl", -1.0), sig(2, "invalidated", 3.0), sig(3, "active", None), sig(4, "killed", None)], {1})
    assert f[("trend", "WTI_USD")] == {"signals": 3, "taken": 1, "skipped": 2, "signal_r": 1.0}


# ---------- luck checks ----------
def test_deflated_sharpe_reproduces_the_published_example():
    """Bailey & Lopez de Prado 2014: annual SR 2.5, 5 years, skew -3, kurtosis 10, V[SR] 0.5 across trials."""
    sr, n = 2.5 / 250 ** 0.5, 1250
    denom = 1 + 3 * sr + (10 - 1) / 4 * sr ** 2
    from statistics import NormalDist
    assert NormalDist().cdf((sr - stats.expected_max_sharpe(100, 0.5 / 250)) * (n - 1) ** 0.5 / denom ** 0.5) == pytest.approx(0.90, abs=0.01)
    assert stats.deflated_sharpe([1.0, -1.0] * 20, 1, 0.0) == pytest.approx(0.5)


def test_more_trials_lower_the_deflated_sharpe():
    rng = np.random.default_rng(3)
    rs = list(rng.normal(0.15, 1, 400))
    assert stats.deflated_sharpe(rs, 1000, 0.02) < stats.deflated_sharpe(rs, 10, 0.02) < stats.deflated_sharpe(rs, 1, 0.02)


def test_overfitting_probability_tells_noise_from_a_real_winner():
    rng = np.random.default_rng(5)
    noise = [rng.normal(0, 1, (40, 30)) for _ in range(30)]
    assert 0.4 < np.mean([stats.pbo(n) for n in noise]) < 0.6  # a coin flip on average: any one sample varies a lot
    real = noise[0].copy()
    real[:, 0] += 2.0
    assert stats.pbo(real) < 0.05
    assert stats.pbo(noise[0][:, :1]) is None


def test_reality_check_on_the_best_of_many_noise_variants():
    rng = np.random.default_rng(6)
    noise = rng.normal(0, 1, (40, 30))
    real = noise.copy()
    real[:, 0] += 1.5
    assert stats.reality_check(noise) > 0.05 and stats.reality_check(real) < 0.05
    assert stats.reality_check(np.full((40, 2), -1.0)) == 1.0


def zero_edge_runs(rng, combos=12, trades=120):
    runs = []
    for c in range(combos):
        rs = rng.choice([2.0, -1.0], trades, p=[1 / 3, 2 / 3])
        runs.append(({"rr": c}, [{"symbol": "X", "i": i * 10, "r": float(r), "time": i, "closed_at": i, "days": 1} for i, r in enumerate(rs)]))
    return runs


def test_zero_edge_rules_rarely_survive_the_checks():
    rng = np.random.default_rng(9)
    labels = [engine.evaluate("icc", zero_edge_runs(rng), {"X": (0, 1200)}, 4, "year", prior_trials=100)["verdict"] for _ in range(40)]
    assert labels.count("survives checks") <= 1


def test_a_strong_edge_can_survive():
    rng = np.random.default_rng(10)
    runs = zero_edge_runs(rng)
    runs[0] = ({"rr": 0}, [{"symbol": "X", "i": i * 5, "r": float(r), "time": i, "closed_at": i, "days": 1}
                           for i, r in enumerate(rng.choice([2.0, -1.0], 240, p=[0.7, 0.3]))])
    assert engine.evaluate("icc", runs, {"X": (0, 1200)}, 4, "year", prior_trials=100)["verdict"] == "survives checks"


def test_fresh_counts_only_trades_after_the_freeze():
    rng = np.random.default_rng(1)
    r = engine.evaluate("icc", zero_edge_runs(rng, 3), {"X": (0, 1200)}, 4, "year", fresh_since=100)
    assert r["fresh"]["trades"] == sum(1 for t in r["trades"] if t["time"] >= 100)


# ---------- trial log and settings per signal ----------
def result(n=2):
    return {"strategies": [{"key": "icc", "combos": [{"params": f"rr={i}", "trades": 5, "expectancy": 0.1} for i in range(n)]}]}


def test_trial_log_counts_distinct_variants_and_backfills_old_runs(tmp_path):
    s = Store(tmp_path / "t.sqlite")
    spec = {"symbols": ["EUR_USD", "XAU_USD"], "tf": "DAY", "days": 100, "strategies": ["icc"]}
    s.save_strategy_run({"created": 1, "status": "done", "spec": spec, "result": result()})
    assert s.trial_count() == 0
    s.backfill_trials()
    assert s.trial_count() == 2
    s.backfill_trials()  # only once
    s.log_trials(9, 2, spec, result(3))  # a repeat adds rows, not variants
    assert s.trial_count() == 3
    assert s.db.execute("SELECT COUNT(*) FROM trial_log").fetchone()[0] == 5


def test_signals_carry_the_settings_that_made_them(tmp_path):
    s = Store(tmp_path / "t.sqlite")
    s.stamp = lambda: {"weights": {"news": 0.4}, "config": "abc"}
    sig = signal(s)
    assert s.get(sig["id"])["stamp"]["config"] == "abc" and sig["stamp"]["weights"]["news"] == 0.4


def learning(tmp_path):
    s = Store(tmp_path / "l.sqlite")
    cfg = {"lookback": 100, "min_samples": 1, "lr": 1.0, "max_step": 1.0, "min_weight": 0.1, "max_weight": 1.5}
    return Learning(s, Bus(), {"weights": {"technical": 1.0, "news": 0.4, "macro": 0.6}}, cfg, "UTC"), s


def test_unfreezing_or_tuning_restarts_the_evaluation(tmp_path):
    le, s = learning(tmp_path)
    s.set_setting("evaluation_since", 1)
    le.freeze(True)
    assert s.get_setting("evaluation_since", 0) == 1  # freezing changes nothing
    le.freeze(False)
    assert s.get_setting("evaluation_since", 0) > 1
    s.set_setting("evaluation_since", 1)
    le.reset()
    assert s.get_setting("evaluation_since", 0) > 1


def test_news_weight_can_fall_but_never_rise(tmp_path, monkeypatch):
    le, s = learning(tmp_path)
    monkeypatch.setattr(performance, "agents", lambda _: {"news": {"graded": 50, "accuracy": 90}, "macro": {"graded": 50, "accuracy": 90}})
    monkeypatch.setattr(le, "_strategies", lambda now: [], raising=True)
    le.update(1)
    assert le.weights["news"] == 0.4 and le.weights["macro"] > 0.6
    monkeypatch.setattr(performance, "agents", lambda _: {"news": {"graded": 50, "accuracy": 25}})
    le.update(2)
    assert le.weights["news"] < 0.4


# ---------- news on trial ----------
def test_headlines_lose_what_a_reader_would_not_see():
    assert clean_title("Gold​ rises <b>sharply</b> &amp; holds‮\x07") == "Gold rises sharply & holds"
    assert clean_title("Ｇold  up") == "Gold up"  # full-width look-alikes
    assert clean_title("<i></i>") == ""
    assert len(clean_title("x" * 1000)) == 300


def test_news_vote_is_logged_but_not_acted_on(tmp_path, monkeypatch):
    from jarvis.station import agents
    import conftest as c
    cfg = {"min_confidence": 60, "conflict_score": 30, "support_factor": 0.3, "weights": {"news": 1.0, "macro": 0.0, "sentiment": 0.0},
           "intraday": {"expiry_hours": 12}, "risk": {}}
    store = Store(tmp_path / "n.sqlite")
    s = Station(Bus(), store, {"XAU_USD": {"pip": 0.1}}, cfg)
    levels = {"entry": 2000.0, "entry_low": 2000.0, "entry_high": 2000.0, "stop": 1990.0, "tp1": 2020.0, "tp2": 2030.0}
    monkeypatch.setattr(agents, "technical", lambda *a: Vote("technical", "BUY", 55, ["t"], levels=levels, invalidation=1990.0))
    monkeypatch.setattr(agents, "macro", lambda *a: Vote("macro", "HOLD", 0, ["m"]))
    monkeypatch.setattr(agents, "sentiment", lambda *a: Vote("sentiment", "HOLD", 0, ["s"]))
    monkeypatch.setattr(agents, "risk_officer", lambda *a: RiskReport(False, []))
    monkeypatch.setattr(agents, "news", lambda *a: Vote("news", "BUY", 100, ["strong"]))
    f = {"MINUTE_15": c.frame(120, 900, 0.1), "HOUR": c.frame(80, 3600, 0.1), "HOUR_4": c.frame(220, 14_400, 0.1)}
    d = s.on_frames("XAU_USD", f, now_s=c.NOW)
    assert d["call"] == "HOLD" and d["confidence"] == 55  # the news vote would have lifted it over the threshold
    assert d["news_trial"]["call"] == "BUY" and d["news_trial"]["vote"] == "BUY"
    assert store.get_setting("news_trial_log", [])[-1]["with_news"] == "BUY"


def test_news_trial_groups_results_by_how_the_vote_went():
    sig = lambda v, r: {"call": "BUY", "result_r": r, "news_trial": {"vote": v}}  # noqa: E731
    out = performance.news_trial([sig("BUY", 1.0), sig("SELL", -1.0), sig("HOLD", 2.0), {"call": "BUY", "result_r": 5.0}])
    assert out["agreed"]["trades"] == out["against"]["trades"] == out["neutral"]["trades"] == 1 and len(out) == 3
