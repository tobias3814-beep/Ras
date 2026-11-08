"""TEST 101: the day-trade skill chain on synthetic 5-minute bars with known answers."""
from datetime import date

import numpy as np

from jarvis.test101 import rules
from jarvis.test101.rules import BAR, Day

D = date(2026, 10, 19)  # a Monday, the skills' reel day
CFG = {**rules.DEFAULTS, "replays": 500}


def day(closes, d=D, wick=0.2, vol=1000):
    o, _ = rules.session(d)
    c = np.array(closes, float)
    op = np.r_[c[0], c[:-1]]
    return Day(d, o + BAR * np.arange(len(c)), op, np.maximum(op, c) + wick, np.minimum(op, c) - wick, c, np.full(len(c), vol, float))


def flat_pool(n=25):
    return rules.pool([day([100.0] * 78, d=date(2026, 9, 1)) for _ in range(n)])


def test_calendar_matches_the_skills():
    assert not rules.trading_day(date(2026, 11, 26)) and rules.trading_day(date(2026, 11, 27))
    k = rules.clock(date(2026, 11, 27), CFG)
    assert k["bars"] == 42 and k["last"] == rules.at(date(2026, 11, 27), "12:30") and k["flat"] == rules.at(date(2026, 11, 27), "12:55")


def test_entry_is_the_first_close_above_the_range_high_not_a_wick():
    bars = [100, 101, 100.5] + [101.1] * 2 + [101.3, 102] + [101] * 71   # range high 101.2 (101 + wick)
    dy = day(bars)
    e = rules.entry(dy, CFG)
    assert e["status"] == "triggered" and e["i"] == 5 and e["price"] == 101.3
    assert e["time"] == int(dy.t[5]) + BAR
    # before that bar has closed it is still waiting
    assert rules.entry(dy, CFG, now=int(dy.t[5]) + BAR - 1)["status"] == "waiting"


def test_exit_plan_rounds_like_the_reel():
    p = rules.exit_plan({"price": 364.18, "or_high": 362.18, "or_low": 354.47}, {**CFG, "target_r": 1.0})
    assert p["stop"] == 358.32 and p["target"] == 370.04 and round(p["risk"], 2) == 5.86


def test_budget_sizes_like_the_reel():
    s = rules.size(364.18, 358.32, 370.04, 5000, CFG)
    assert s["shares"] == 8 and s["decided_by"] == "max loss" and s["max_loss"] == 46.88
    assert rules.size(366.48, 358.32, 370.04, 5000, CFG)["shares"] == 0   # reward:risk 0.44 after the run-up


def test_time_machine_is_repeatable_and_flat_markets_hit_neither():
    a = rules.time_machine(flat_pool(), 10, 76, 100.0, 99.0, 102.0, 2.0, 500, 19)
    b = rules.time_machine(flat_pool(), 10, 76, 100.0, 99.0, 102.0, 2.0, 500, 19)
    assert a == b and a["neither"] > 0.99


def test_whole_day_trades_once_and_respects_limits():
    up = [100, 101, 100.5, 101.1, 101.1, 101.5] + list(np.linspace(101.6, 104, 72))
    days = {s: day(up) for s in ("AAA", "BBB", "CCC", "DDD")}
    res = rules.run_day(days, {s: flat_pool() for s in days}, {**CFG, "tm_gate": False}, 5000.0)
    taken = [t for t in res["trades"] if t["status"] in ("open", "closed")]
    assert len(taken) <= CFG["max_trades_per_day"]
    assert all(t["max_loss"] <= 50 for t in (x["sizing"] for x in taken))
    assert sum(x["sizing"]["max_loss"] for x in taken) <= CFG["max_daily_loss_usd"]
    assert all(t["status"] == "closed" for t in taken)   # flat by the close, never overnight
    blocked = [t for t in res["trades"] if t["status"] == "blocked"]
    assert all(t["why"] for t in blocked)


def test_halt_and_kill_switch_stop_new_entries():
    up = [100, 101, 100.5, 101.1, 101.1, 101.5] + list(np.linspace(101.6, 104, 72))
    days = {"AAA": day(up)}
    trig = rules.entry(days["AAA"], CFG)["time"]
    res = rules.run_day(days, {"AAA": flat_pool()}, {**CFG, "tm_gate": False}, 5000.0, halts=[[trig - 60, None]])
    assert res["trades"][0]["status"] == "blocked" and "halted" in res["trades"][0]["why"]
    res = rules.run_day(days, {"AAA": flat_pool()}, {**CFG, "tm_gate": False}, 5000.0, kill_at=trig + 3 * BAR)
    assert res["trades"][0]["reason"] == "kill switch"


def test_live_replay_matches_the_finished_day():
    """What the desk did by 11:00 is what the whole-day replay says happened by 11:00 (parity)."""
    up = [100, 101, 100.5, 101.1, 101.1, 101.5] + list(np.linspace(101.6, 104, 72))
    full = day(up)
    cut = rules.at(D, "11:00")
    m = full.t + BAR <= cut
    part = Day(D, full.t[m], full.o[m], full.h[m], full.l[m], full.c[m], full.v[m], float(full.o[~m][0]))
    a = rules.run_day({"AAA": part}, {"AAA": flat_pool()}, {**CFG, "tm_gate": False}, 5000.0, now=cut)["trades"][0]
    b = rules.run_day({"AAA": full}, {"AAA": flat_pool()}, {**CFG, "tm_gate": False}, 5000.0)["trades"][0]
    assert (a["fill"], a["shares"], a["plan"]) == (b["fill"], b["shares"], b["plan"])


def test_scan_tags():
    assert "breakout_20d" in rules.scan_tags(np.r_[np.full(20, 10.0), 11.0])
    assert "oversold" in rules.scan_tags(np.linspace(20, 10, 30))


def test_test101_never_imports_a_broker():
    import inspect
    from jarvis.test101 import engine
    src = inspect.getsource(engine) + inspect.getsource(rules)
    assert "alpaca" not in src.lower().replace("no alpaca", "") and "execution" not in src and "place_stock_order" not in src


def test_a_late_start_replays_the_morning_quietly_and_keeps_it_out_of_the_live_record():
    from jarvis.test101.engine import Test101

    class St:
        def __init__(self): self.d = {}
        def get_setting(self, k, d): return self.d.get(k, d)
        def set_setting(self, k, v): self.d[k] = v

    class Bus:
        def __init__(self): self.events = []
        def publish(self, e, key=None): self.events.append(e)

    bus = Bus()
    t = Test101(bus, St(), type("H", (), {"active": False})())
    trade = {"symbol": "AAA", "trigger_time": 100, "status": "closed", "shares": 1, "fill": 1.0, "plan": {"stop": 0.9, "target": 1.2},
             "reason": "stop", "pnl": -0.1, "exit_time": 200, "r": -1.0}
    t._alerts(None, {"trades": [trade]}, live_from=1000)
    assert not bus.events
    t.state["days"] = {"2026-10-19": {"trades": [trade], "pnl": -0.1, "closed": True, "live_from": 1000}}
    t.state["since"] = "2026-10-19"
    assert t.snapshot()["forward"]["stats"]["trades"] == 0
