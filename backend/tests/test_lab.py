import numpy as np
import pandas as pd
import pytest

from jarvis.config import load_config
from jarvis.lab import engine, rules
from jarvis.lab.rules import CATALOG, Plan, Setup

T0 = 1_800_000_000 - 1_800_000_000 % 86_400  # a midnight UTC


def quoted(rows: list[tuple[float, float, float, float]], bar_s: int = 3600, spread: float = 0.0) -> pd.DataFrame:
    """Bars from (open, high, low, close) mids, with a constant spread around them."""
    o, h, lo, c = (np.array(x, float) for x in zip(*rows))
    df = pd.DataFrame({"time": T0 + bar_s * np.arange(len(rows)), "open": o, "high": h, "low": lo, "close": c, "volume": 1})
    for side, sgn in (("bid", -1), ("ask", 1)):
        for k in ("open", "high", "low", "close"):
            df[f"{side}_{k}"] = df[k] + sgn * spread / 2
    return df


def walk(n: int, bar_s: int = 3600, seed: int = 5, drift: float = 0.0, vol: float = 0.003) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    c = 100 * np.exp(np.cumsum(drift + vol * rng.standard_normal(n)))
    o = np.r_[100, c[:-1]]
    w = np.abs(vol / 2 * rng.standard_normal(n)) * c
    return quoted(list(zip(o, np.maximum(o, c) + w, np.minimum(o, c) - w, c)), bar_s, spread=0.01)


def sim(df, setups, exit_long=None, exit_short=None, funding=0.0, slip=0.0, bar_s=3600):
    return engine.simulate("X", "k", df, Plan(setups, exit_long, exit_short), pip=0.01, slip_pips=slip,
                           funding_pct=funding, bar_s=bar_s)


FLAT = (100, 100.2, 99.8, 100)


def test_stop_counts_first_when_a_bar_touches_both():
    df = quoted([FLAT, (100, 100.1, 99.9, 100), (100, 103, 97, 100)])
    [t] = sim(df, [Setup(0, 1, 98.0, 102.0)])
    assert t["status"] == "stop" and t["r"] == pytest.approx(-1.0)


def test_market_fill_pays_the_spread_and_target_pays_out():
    df = quoted([FLAT, (100, 100.1, 99.9, 100), (100, 102.5, 99.5, 102)], spread=0.2)
    [t] = sim(df, [Setup(0, 1, 98.0, 102.0)])
    assert t["entry"] == pytest.approx(100.1)  # bought at the ask
    # stop and target keep their distances from the signal close, moved to the fill: 98.1 / 102.1, so exactly +1R
    assert t["status"] == "target" and t["exit"] == pytest.approx(102.1) and t["r"] == pytest.approx(1.0)


def test_gap_keeps_the_planned_stop_distance():
    df = quoted([FLAT, (110, 110.2, 109.9, 110), (110, 110.1, 107.9, 108)])
    [t] = sim(df, [Setup(0, 1, 98.0, None, max_bars=5)])  # 2.0 below the signal close
    assert t["entry"] == 110 and t["status"] == "stop" and t["exit"] == pytest.approx(108.0)
    assert t["r"] == pytest.approx(-1.0)


def test_limit_fills_only_when_touched_and_its_fill_bar_cannot_take_profit():
    df = quoted([FLAT, (100, 100.5, 99.9, 100.4), (100.4, 101.5, 99.4, 101), (101, 103, 100.9, 102.5)])
    [t] = sim(df, [Setup(0, 1, 98.0, 102.0, entry=99.5, expiry=3)])
    assert t["i"] == 2 and t["entry"] == pytest.approx(99.5)
    assert t["status"] == "target" and t["time"] == int(df.time[2] * 1000) and t["closed_at"] == int((df.time[3] + 3600) * 1000)
    assert sim(df, [Setup(0, 1, 98.0, 102.0, entry=99.0, expiry=3)]) == []  # never reached: cancelled


def test_exit_rule_and_funding():
    df = quoted([FLAT] + [(100, 100.2, 99.8, 100)] * 9 + [(100, 101.2, 99.9, 101)], bar_s=86_400)
    xl = np.zeros(len(df), bool)
    xl[10] = True
    [t] = sim(df, [Setup(0, 1, 99.0, None)], exit_long=xl, bar_s=86_400)
    assert t["status"] == "exit rule" and t["r"] == pytest.approx(1.0) and t["days"] == pytest.approx(10)
    [f] = sim(df, [Setup(0, 1, 99.0, None)], exit_long=xl, funding=3.65, bar_s=86_400)
    assert f["r"] == pytest.approx(1.0 - 100 * 0.0365 * 10 / 365 / 1.0, abs=1e-3)  # 0.01 a day on 100 for 10 days = 0.1R


def test_one_position_at_a_time():
    df = walk(200)
    setups = [Setup(i, 1, float(df.close[i]) - 5, None, max_bars=20) for i in range(0, 150, 5)]
    ts = sim(df, setups)
    for a, b in zip(ts, ts[1:]):
        assert b["time"] >= a["closed_at"] - 3600 * 1000


def test_swings_are_known_only_n_bars_later():
    h = [1, 2, 5, 2, 1, 1, 1]
    df = quoted([(x, x, x - 0.5, x) for x in h])
    sh, _, shi, _ = rules.swings(df, 2)
    assert np.isnan(sh[3]) and sh[4] == 5 and shi[4] == 2


def test_missing_spreads_get_the_typical_one():
    df = quoted([FLAT] * 4, spread=0.0)
    df.loc[3, "ask_close"], df.loc[3, "bid_close"] = 100.1, 99.9
    out = engine.with_spread(df)
    assert out.ask_close[0] - out.bid_close[0] == pytest.approx(0.2, rel=1e-3)
    assert out.ask_close[3] == 100.1


@pytest.mark.parametrize("key", list(CATALOG))
def test_every_rule_runs_and_puts_the_stop_on_the_right_side(key):
    bar_s = 86_400 if CATALOG[key]["tf"] == "DAY" else 900 if CATALOG[key]["tf"] == "MINUTE_15" else 3600
    df = walk(4000 if bar_s == 86_400 else 6000, bar_s=bar_s, seed=11, vol=0.01 if bar_s == 86_400 else 0.003)
    total = 0
    for p in rules.combos(key):
        plan = CATALOG[key]["fn"](df, {**p, "_bar_s": bar_s})
        for s in plan.setups:
            ref = s.entry if s.entry is not None else float(df.close[s.i])
            assert (ref - s.stop) * s.side > 0
            assert s.target is None or (s.target - ref) * s.side > 0
        total += len(plan.setups)
    assert total > 0, f"{key} never fired on 6000 random bars"


def test_icc_needs_indication_correction_and_continuation():
    # swing low 95 (bar 3), swing high 105 (bar 6); break of 105 = indication; pullback holds above 95 (low 101,
    # bar 10); a close above the indication's extreme (108) = continuation
    path = [100, 97, 96, 95, 98, 102, 105, 103, 101, 106, 108, 104, 102, 101, 103, 105, 107, 109, 110]
    rows = [(c, c + 0.2, c - 0.2, c) for c in path]
    df = quoted(rows)
    plan = rules.icc(df, {"swing": 2, "max_bars": 50, "rr": 2.0, "trend_filter": False})
    longs = [s for s in plan.setups if s.side > 0]
    assert longs, "no continuation entry"
    s = longs[0]
    assert df.close[s.i] > 108 and s.stop < 101 and s.stop > 95


def test_ict_only_trades_intraday_and_inside_kill_zones():
    df = walk(96 * 120, bar_s=900, seed=2, vol=0.004)
    assert rules.ict_sweep_fvg(walk(400, bar_s=14_400), {"zone": "both", "entry": "edge", "target": 2.0, "_bar_s": 14_400}).setups == []
    plan = rules.ict_sweep_fvg(df, {"zone": "london", "entry": "ce", "target": "liquidity", "_bar_s": 900})
    assert plan.setups
    ny = pd.to_datetime(df.time, unit="s", utc=True).dt.tz_convert("America/New_York")
    for s in plan.setups:
        assert 2 <= ny[s.i].hour < 5 and s.entry is not None


def test_walk_forward_picks_parameters_in_sample_only():
    spans = {"X": (0, 100)}
    t = lambda i, r: {"symbol": "X", "i": i, "r": r, "time": T0 * 1000 + i * 1000, "closed_at": T0 * 1000 + i * 1000, "days": 1}  # noqa: E731
    good_then_bad = [t(i, 1.0 if i < 50 else -1.0) for i in range(0, 100, 2)]
    flat_then_good = [t(i, -0.1 if i < 50 else 2.0) for i in range(0, 100, 2)]
    res = engine.evaluate("icc", [({"rr": 1.5}, good_then_bad), ({"rr": 3.0}, flat_then_good)], spans, folds=2, by="month")
    # fold 1 tests 33-67%: chosen on 0-33% where the first combo looked best -> it gets the losses after bar 50
    assert res["folds"][0]["params"] == "rr=1.5"
    assert res["summary"]["trades"] > 0 and res["combos_positive"] == 1  # +25R -25R nets to zero


def test_run_end_to_end_with_a_fake_loader():
    cfg = load_config()
    df = walk(3000, bar_s=86_400, seed=4, drift=0.0004, vol=0.01)
    df["time"] = df.time - df.time.iloc[-1] + int(pd.Timestamp.now(tz="UTC").timestamp()) - 86_400
    res = engine.run({"strategies": ["trend_following", "donchian_breakout", "icc"], "symbols": ["XAU_USD", "EUR_USD"],
                      "tf": "DAY", "days": 2500, "folds": 4, "funding_pct": 2.0}, cfg, load=lambda s, tf, d: df.copy())
    assert [r["key"] for r in res["strategies"]] and len(res["strategies"]) == 3
    for r in res["strategies"]:
        assert len(r["folds"]) == 4 and r["period"] == "year" and r["verdict"]
        assert set(r["by_symbol"]) <= {"XAU_USD", "EUR_USD"}


def test_capital_download_retries_a_timeout(monkeypatch):
    import asyncio

    import httpx

    from jarvis import history

    class Flaky:
        calls = 0

        async def get(self, path, params):
            Flaky.calls += 1
            if Flaky.calls < 3:
                raise httpx.ReadTimeout("slow")
            return {"prices": []}

    real_sleep = asyncio.sleep
    monkeypatch.setattr(asyncio, "sleep", lambda s: real_sleep(0))
    assert asyncio.run(history._get_retrying(Flaky(), "/x", {})) == {"prices": []} and Flaky.calls == 3


def test_scan_skips_rules_that_cannot_trade_on_a_timeframe():
    assert "ict_sweep_fvg" not in engine.scan_strategies("DAY") and "ict_sweep_fvg" in engine.scan_strategies("MINUTE_15")
    assert "trend_following" not in engine.scan_strategies("MINUTE_15") and "trend_following" in engine.scan_strategies("DAY")
    assert set(engine.scan_strategies("HOUR")) == {k for k, v in CATALOG.items() if v["group"] not in ("Ras 1.1", "Legends", "Books")}


def test_restart_marks_interrupted_runs_failed(tmp_path):
    from jarvis.station.store import Store
    st = Store(tmp_path / "r.sqlite")
    run = {"created": 1, "status": "running", "spec": {"strategies": ["icc"], "symbols": ["XAU_USD"], "tf": "HOUR", "days": 30}}
    st.save_strategy_run(run)
    st.set_setting("strategy_scan", {"status": "running", "runs": [run["id"]], "current": "XAU_USD HOUR"})
    st.set_setting("edge_scan", {"status": "done", "runs": []})
    assert st.fail_interrupted() == 2
    assert st.strategy_run(run["id"])["status"] == "failed" and "restarted" in st.strategy_run(run["id"])["result"]["error"]
    scan = st.get_setting("strategy_scan", None)
    assert scan["status"] == "failed" and "current" not in scan
    assert st.get_setting("edge_scan", None)["status"] == "done"  # finished scans are left alone


def _hourly(n: int = 2000) -> "pd.DataFrame":
    import numpy as np
    import pandas as pd
    c = 1.1 + np.cumsum(np.random.default_rng(1).normal(0, 0.0004, n))
    t = 1_767_225_600 + 3600 * np.arange(n)  # 2026-01-01 00:00 UTC
    return pd.DataFrame({"time": t, "open": c, "high": c + 0.001, "low": c - 0.001, "close": c})


def test_ras11_rules_read_the_clock_they_claim():
    from jarvis.lab import rules
    df = _hourly()
    plan = rules.gold_asia_drift(df, {"start": 0, "end": 6, "atr_stop": 2.0, "_bar_s": 3600})
    assert plan.setups and all((int(df.time.iat[s.i]) + 3600) % 86_400 == 0 and s.side == 1 and s.max_bars == 6 for s in plan.setups)
    plan = rules.gold_london_fade(df, {"hold": 4, "atr_stop": 2.0, "_bar_s": 3600})
    assert plan.setups and all(s.side == -1 for s in plan.setups)  # January: London open is 08:00 UTC
    assert all((int(df.time.iat[s.i]) + 3600) % 86_400 == 8 * 3600 for s in plan.setups if int(df.time.iat[s.i]) < 1_772_000_000)


def test_ras11_fix_reversal_sells_dollars_whichever_way_the_pair_is_quoted():
    from jarvis.lab import rules
    p = {"fix": "london", "hold": 2, "atr_stop": 2.0, "_bar_s": 3600}
    df = _hourly()
    df.attrs["symbol"] = "EUR_USD"
    assert {s.side for s in rules.fix_reversal(df, p).setups} == {1}
    df.attrs["symbol"] = "USD_JPY"
    assert {s.side for s in rules.fix_reversal(df, p).setups} == {-1}


def test_ras11_stays_apart_from_the_rest_of_ras():
    from jarvis.lab import engine, ras11
    from jarvis.lab.rules import CATALOG
    assert not [k for k in engine.scan_strategies("HOUR") if CATALOG[k]["group"] == "Ras 1.1"]
    assert all(k in CATALOG and s["sources"] and s["markets"] for k, s in ras11.STRATEGIES.items())
    # untested rules show as untested, never as a result
    assert all(r["test"] is None for r in ras11.page([])["strategies"])


def test_widen_keeps_the_symbol_for_cost_stress():
    import pandas as pd
    from jarvis.lab import engine
    df = _hourly(50)
    for s in ("bid", "ask"):
        for k in ("open", "high", "low", "close"):
            df[f"{s}_{k}"] = df[k]
    df = engine.with_spread(df)
    df.attrs["symbol"] = "USD_JPY"
    assert engine.widen(df, 2.0).attrs.get("symbol") == "USD_JPY"
    assert isinstance(df, pd.DataFrame)


def _daily(n=1500, seed=3):
    import numpy as np
    import pandas as pd
    c = 100 * np.exp(np.cumsum(np.random.default_rng(seed).normal(0, 0.01, n)))
    return pd.DataFrame({"time": 1_600_000_000 + 86_400 * np.arange(n), "open": c, "high": c * 1.005, "low": c * 0.995, "close": c,
                         "volume": 1000 + np.arange(n) % 7 * 100})


def test_hidden_research_rules_still_run():
    from jarvis.lab import engine, rules
    df = _daily()
    for k in ("simons_reversal", "ptj_200day", "livermore_highs", "druckenmiller_pullback", "soros_squeeze"):
        for p in rules.combos(k):
            plan = rules.CATALOG[k]["fn"](df, {**p, "_bar_s": 86_400})
            assert all(s.side in (1, -1) and (s.stop - df.close.iat[s.i]) * s.side < 0 for s in plan.setups), k
    assert not [k for k in engine.scan_strategies("DAY") if rules.CATALOG[k]["group"] in ("Legends", "Books")]


def test_book_rules_run_with_stops_on_the_right_side_and_stay_on_their_page():
    from jarvis.lab import books, rules
    df = _daily()
    for k in books.STRATEGIES:
        n = 0
        for p in rules.combos(k):
            plan = rules.CATALOG[k]["fn"](df, {**p, "_bar_s": 86_400})
            n += len(plan.setups)
            for s in plan.setups:
                ref = s.entry if s.entry is not None else df.close.iat[s.i]
                assert (s.stop - ref) * s.side < 0, k
                if s.stop_entry:  # a buy-stop sits above the bar, a sell-stop below it
                    assert (s.entry - df.close.iat[s.i]) * s.side > 0, k
                if s.target is not None:
                    assert (s.target - ref) * s.side > 0, k
        assert n > 0, k
    page = books.page([])
    assert {r["key"] for r in page["strategies"]} == set(books.STRATEGIES) and all(r["test"] is None for r in page["strategies"])
    assert all(r["claim"] and r["sources"] for r in page["strategies"])


def test_weekly_tide_does_not_peek_at_the_open_week():
    from jarvis.lab import rules
    df = _daily(400)
    a = rules._weekly_tide(df)
    df2 = df.copy()
    df2.loc[len(df2) - 3:, "close"] *= 1.3  # rewrite the last few days
    b = rules._weekly_tide(df2)
    import pandas as pd
    wk = pd.to_datetime(df.time, unit="s").dt.to_period("W-FRI")  # UTC, naive
    same_week = (wk == wk.iloc[-1]).to_numpy()
    assert (a[same_week] == b[same_week]).all()  # this week's tide comes from finished weeks only


def test_a_buy_stop_fills_only_on_a_break_above_it():
    import numpy as np
    import pandas as pd
    from jarvis.lab import engine
    from jarvis.lab.rules import Plan, Setup
    c = np.array([100.0, 100, 100, 101, 103, 104, 104, 104])
    df = pd.DataFrame({"time": 86_400 * np.arange(len(c)), "open": c, "high": c + 0.5, "low": c - 0.5, "close": c})
    for s in ("bid", "ask"):
        for k in ("open", "high", "low", "close"):
            df[f"{s}_{k}"] = df[k]
    no = engine.simulate("EUR_USD", "k", df, Plan([Setup(1, 1, 99.0, None, entry=102.0, expiry=1, stop_entry=True)]), 0.0001, 0, 0, 86_400)
    assert no == []  # bar 2 never traded up to 102
    gap = engine.simulate("EUR_USD", "k", df, Plan([Setup(3, 1, 99.0, None, entry=102.0, expiry=1, stop_entry=True, max_bars=1)]), 0.0001, 0, 0, 86_400)
    assert gap and gap[0]["entry"] == 103.0  # gapped over the stop: filled at the open, not at 102


def test_money_rules_cut_size_in_drawdowns_and_stop_for_the_month():
    from jarvis.lab import account
    t0 = 1_780_000_000_000
    losses = [{"time": t0 + i * 3_600_000, "closed_at": t0 + i * 3_600_000 + 60_000, "symbol": "EUR_USD", "call": "BUY", "mode": "trend",
               "entry": 1.1, "risk_px": 0.001, "r": -1.0} for i in range(12)]
    base = {"balance": 100.0, "risk_pct": 2.0, "max_risk_pct": 2.0}
    plain = account.simulate(losses, base)
    cut = account.simulate(losses, {**base, "dd_cut": [[10, 0.5], [20, 0.25]]})
    stop = account.simulate(losses, {**base, "month_stop_pct": 6})
    assert plain["final"] < cut["final"] < 100 and stop["paused"] > 0 and stop["final"] > plain["final"]


def test_every_page_keeps_its_endpoints():
    from jarvis import main
    paths = {r.path for r in main.app.routes}
    for p in ("/api/brain", "/api/brain/rebuild", "/api/books", "/api/books/test/{key}", "/api/ras11", "/api/ras11/test/{key}", "/api/test101"):
        assert p in paths, p


def test_vwap_ema9_trades_the_new_york_session_on_3_minute_bars():
    import numpy as np
    import pandas as pd
    from zoneinfo import ZoneInfo
    from jarvis.lab import engine, rules
    n = 20 * 24 * 60  # 20 days of 1-minute candles
    c = 100 * np.exp(np.cumsum(np.random.default_rng(5).normal(0, 0.0005, n)))
    m1 = pd.DataFrame({"time": 1_780_000_020 // 60 * 60 + 60 * np.arange(n), "open": c, "high": c * 1.0004, "low": c * 0.9996, "close": c,
                       "volume": 10})
    for s in ("bid", "ask"):
        for k in ("open", "high", "low", "close"):
            m1[f"{s}_{k}"] = m1[k]
    df = engine.to_minutes(m1, 3)
    assert len(df) == n // 3 and (df.volume == 30).all() and list(df.columns) == list(m1.columns)
    plan = rules.vwap_ema9(df, {"session": "ny", "rr": 1.5, "atr_stop": 1.5, "_bar_s": 180})
    assert plan.setups
    ny = ZoneInfo("America/New_York")
    for s in plan.setups:
        t = pd.Timestamp(int(df.time.iat[s.i]), unit="s", tz="UTC").tz_convert(ny)
        mins = t.hour * 60 + t.minute
        assert 570 <= mins < 930 and mins + 3 * (s.max_bars + 1) <= 960  # entries 09:30-15:30, out by 16:00
    assert len(rules.vwap_ema9(df, {"session": "fx", "rr": None, "atr_stop": 1.5, "_bar_s": 180}).setups) > len(plan.setups)


def test_options_price_a_winning_call_up_and_a_losing_put_down():
    from jarvis.lab import options
    t0 = 1_793_700_000_000  # a Tuesday
    win = {"symbol": "XAU_USD", "call": "BUY", "time": t0, "closed_at": t0 + 3_600_000, "entry": 4000.0, "exit": 4040.0}
    lose = {**win, "call": "SELL"}
    assert options.one(win, 0.2) > 0 > options.one(lose, 0.2)
    flat = {**win, "exit": 4000.0}
    assert options.one(flat, 0.2) < 0  # time and the spread cost money when nothing moves
    assert options.expiry(t0).weekday() == 4


def test_ras11_vwap_test_asks_for_option_pricing():
    from jarvis import main
    from jarvis.lab import ras11
    s = ras11.STRATEGIES["vwap_ema9"]
    spec = main.StrategySpec(strategies=["vwap_ema9"], symbols=s["markets"], tf=s["tf"], days=s["days"], options=s["options"])
    assert spec.model_dump()["options"] is True
