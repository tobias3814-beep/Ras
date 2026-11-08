import numpy as np
import pandas as pd

from jarvis import brain
from jarvis.brain import Brain
from jarvis.bus import Bus
from jarvis.paper import Paper
from jarvis.station.store import Store
from jarvis.voice import announce

DAY = 86_400_000
T0 = 1_780_000_000_000


def _mem(n, cond, r, t0=T0, mode="scalp"):
    return [{"source": "backtest", "symbol": "EUR_USD", "mode": mode, "side": 1, "time": t0 + i * 3_600_000, "r": r(i),
             "strategy": "vwap", "conditions": {"symbol": "EUR_USD", "side": "BUY", "strategy": "vwap", **cond(i)}} for i in range(n)]


def test_a_lesson_acts_only_when_it_holds_on_newer_trades():
    old = _mem(300, lambda i: {"trend": "against" if i % 3 == 0 else "with", "session": "asia" if i % 4 == 0 else "london"},
               lambda i: -1.0 if i % 3 == 0 or i % 4 == 0 else (0.6 if i % 2 else -0.2))
    # newer trades: against-trend still loses, Asian hours no longer do
    new = _mem(120, lambda i: {"trend": "against" if i % 3 == 0 else "with", "session": "asia" if i % 4 == 0 else "london"},
               lambda i: -1.0 if i % 3 == 0 else (0.6 if i % 4 == 0 or i % 2 else -0.2), t0=T0 + 400 * 3_600_000)
    lessons = {x["id"]: x for x in brain.find_lessons(old + new, T0 + 350 * 3_600_000)}
    assert lessons["mode=scalp|trend=against"]["status"] == "active"
    assert lessons["mode=scalp|session=asia"]["status"] == "rejected"
    assert "against the hourly trend" in lessons["mode=scalp|trend=against"]["text"]


def test_conditions_use_only_candles_closed_before_the_trade():
    n = 400
    t = (T0 // 1000 // 3600) * 3600 - 3600 * np.arange(n)[::-1]
    c = np.r_[np.linspace(100, 110, n - 1), [50.0]]  # a crash in the trade's own hour must not count
    h = pd.DataFrame({"time": t, "open": c, "high": c + 0.2, "low": c - 0.2, "close": c})
    f = brain.conditions(h, int(t[-1]) * 1000 + 60_000, 1, "EUR_USD", "vwap", 70)
    assert f["trend"] == "with" and f["confidence"] == "mid"
    stale = brain.conditions(h, int(t[-1]) * 1000 + 10 * DAY, 1, "EUR_USD", "vwap", 70)
    assert "trend" not in stale  # a gap in the history says nothing about the trade


def _brain(tmp_path):
    store = Store(tmp_path / "s.sqlite")
    b = Brain(store, Bus(), lambda s: None)
    b.lessons = [{"id": "mode=scalp|side=BUY", "when": {"mode": "scalp", "side": "BUY"}, "text": "Skip scalp buys.", "status": "active",
                  "find": {"trades": 50, "avg_r": -0.4}, "check": {"trades": 20, "avg_r": -0.3}}]
    return store, b


def test_the_brain_gates_paper_and_can_be_switched_off(tmp_path):
    store, b = _brain(tmp_path)
    p = Paper(Bus(), store, {"auto_follow": True, "fx": {"balance": 100, "risk_pct": 1.0, "currency": "USD", "slippage_pips": 0, "max_risk_pct": 5},
                             "crypto": {"balance": 1000, "risk_pct": 1.0, "currency": "USDT", "max_leverage": 3}},
              {"EUR_USD": {"pip": 0.0001}}, {}, 0.0)
    p.set_rules({"EUR_USD": {}})
    p.on_quote("EUR_USD", 1.1, 1.1, 0)
    p.gate = b.check
    sig = lambda call: store.insert({"symbol": "EUR_USD", "mode": "scalp", "call": call, "created": T0, "expires": 10**15,  # noqa: E731
                                     "reasons": ["vwap: reclaimed"], "levels": {"entry": 1.1, "stop": 1.099 if call == "BUY" else 1.101, "tp1": None, "tp2": None}})
    s = sig("BUY")
    p.on_signal(s, 1)
    assert s["id"] not in p.open and "the brain skipped it" in store.journal([s["id"]])[s["id"]]["trade_log"]
    assert b.skips and b.skips[0]["symbol"] == "EUR_USD"
    s = sig("SELL")
    p.on_signal(s, 1)
    assert s["id"] in p.open  # the lesson is about buys only
    b.set(lesson="mode=scalp|side=BUY", on=False)
    assert b.check({**sig("BUY")}) is None
    b.set(lesson="mode=scalp|side=BUY", on=True, enabled=False)
    assert b.check({**sig("BUY")}) is None


def test_a_broken_brain_never_blocks_a_trade(tmp_path):
    _, b = _brain(tmp_path)
    assert b.check({"symbol": "EUR_USD", "mode": "scalp", "call": "BUY"}) is None  # no 'created': the check fails, the trade goes


def test_the_model_needs_to_beat_chance_on_newer_trades():
    rng = np.random.default_rng(1)
    for seed in range(20):  # luck must not earn the model a say
        r = np.random.default_rng(seed)
        noise = _mem(600, lambda i: {"session": ["asia", "london", "overlap"][i % 3], "weekday": ["Monday", "Friday"][i % 2]},
                     lambda i: 1.0 if r.random() < 0.35 else -1.0)
        assert brain.judge_model(noise, T0 + 420 * 3_600_000)["earned"] is False, seed
    real = _mem(600, lambda i: {"session": ["asia", "london", "overlap"][i % 3]},
                lambda i: (1.0 if rng.random() < 0.6 else -1.0) if i % 3 == 1 else (1.0 if rng.random() < 0.15 else -1.0))
    assert brain.judge_model(real, T0 + 420 * 3_600_000)["earned"] is True


def test_ras_says_a_new_lesson_out_loud():
    said = announce.classify({"type": "brain_lesson", "text": "Skip scalp buys."}, set(), 0)
    assert said and "learnt" in said[0]


def test_the_learning_log_records_each_change_once_and_survives_a_restart(tmp_path, monkeypatch):
    store = Store(tmp_path / "s.sqlite")
    bus = Bus()
    b = Brain(store, bus, lambda s: None)
    lesson = {"id": "mode=scalp|side=BUY", "when": {"mode": "scalp", "side": "BUY"}, "text": "Skip scalp buys.", "status": "watching",
              "find": {"trades": 50, "avg_r": -0.4, "win_rate": 20, "base_r": -0.1}, "check": {"trades": 5, "avg_r": -0.3, "rest_r": 0.0}}
    loss = {"source": "live", "id": 7, "symbol": "EUR_USD", "mode": "scalp", "side": 1, "time": T0, "r": -1.0, "strategy": "vwap",
            "conditions": {"side": "BUY", "strategy": "vwap"}, "note": "Lost -1.00R on a scalp buy of EUR/USD."}
    b.memory, b.lessons = [loss], [lesson]
    b._record(T0)
    assert [e["kind"] for e in b.log] == ["noticed", "awake"]
    b._record(T0 + 1)  # nothing changed: nothing new written
    assert len(b.log) == 2
    b2 = Brain(store, bus, lambda s: None)  # restart
    b2.memory, b2.lessons = [loss, {**loss, "id": 8, "time": T0 + 5}], [{**lesson, "status": "active"}]
    b2._record(T0 + 2)
    kinds = [e["kind"] for e in b2.log]
    assert kinds[:2].count("learnt") == 1 and "mistake" in kinds[:2] and kinds.count("awake") == 1
    g = b2.graph()
    assert len(g["mistakes"]) == 2 and g["mistakes"][0]["lessons"] == [0] and g["log"][0]["time"] == T0 + 2
