import asyncio
import json
import time

import httpx
import pytest

from jarvis.bus import Bus
from jarvis.execution import Executor, Rejected
from jarvis.halt import REASON, Halt
from jarvis.notify import Telegram
from jarvis.paper import Paper
from jarvis.station.store import Store
from jarvis.voice import announce
from jarvis.voice.assistant import Assistant

SYM = "perp:ETH/USDT:USDT"


class FakeEx:
    """Records every call; never talks to a network."""

    def __init__(self, fail_on=None, price=2000.0, free=1000.0):
        self.calls, self.fail_on, self.price, self.free = [], fail_on, price, free
        self.positions = {"ETH/USDT:USDT": 0.0}
        self.markets = {"ETH/USDT:USDT": {"limits": {"amount": {"min": 0.001}, "cost": {"min": 5}}}}
        self.n = 0

    async def load_markets(self):
        return self.markets

    async def fetch_ticker(self, s):
        return {"bid": self.price - 0.1, "ask": self.price + 0.1}

    async def fetch_balance(self):
        return {"USDT": {"free": self.free}}

    def amount_to_precision(self, s, q):
        return f"{q:.3f}"

    async def set_margin_mode(self, *a):
        self.calls.append(("margin", a))

    async def set_leverage(self, *a):
        self.calls.append(("leverage", a))

    async def create_order(self, s, typ, side, qty, price=None, params=None):
        params = params or {}
        kind = "stop" if "stopLossPrice" in params else "tp" if "takeProfitPrice" in params else \
            "trail" if "trailingPercent" in params else "close" if params.get("reduceOnly") else "entry"
        self.calls.append((kind, s, side, qty, params))
        if kind == self.fail_on:
            raise RuntimeError(f"{kind} rejected")
        self.n += 1
        if kind == "entry":
            self.positions[s] = qty
        return {"id": f"o{self.n}", "average": self.price + 0.1}

    async def cancel_order(self, oid, s):
        self.calls.append(("cancel", oid, s))

    async def fetch_positions(self, syms=None):
        return [{"symbol": s, "contracts": q, "side": "long"} for s, q in self.positions.items()]

    async def fetch_open_orders(self):
        return [{"id": "x1", "symbol": "ETH/USDT:USDT"}, {"id": "x2", "symbol": "BTC/USDT:USDT"}]

    async def fetch_my_trades(self, s, since=None):
        return [{"side": "buy", "cost": 100.0, "fee": {"cost": 0.05}}, {"side": "sell", "cost": 103.0, "fee": {"cost": 0.05}}]


def _setup(cfg, tmp_path, ex=None, enabled=True, **over):
    store, bus, h = Store(tmp_path / "s.sqlite"), Bus(), Halt()
    h.bind(store)
    x = Executor(bus, store, {**cfg["execution"], **over}, h, exchange=ex or FakeEx())
    x.enabled = enabled
    now = int(time.time() * 1000)
    sig = store.insert({"symbol": SYM, "mode": "crypto", "call": "BUY", "confidence": 70, "created": now,
                        "entry_until": now + 600_000,
                        "levels": {"entry": 2000.0, "entry_low": 1999, "entry_high": 2001, "stop": 1980.0, "tp1": 2036.0, "tp2": 2050.0}})
    active = {SYM: {**sig, "actionable": True}}
    return x, store, bus, h, sig, active


def test_enable_requires_exact_phrase(cfg, tmp_path):
    x, store, *_ = _setup(cfg, tmp_path, enabled=False)
    with pytest.raises(Rejected, match="type exactly"):
        asyncio.run(x.enable("enable demo execution"))
    st = asyncio.run(x.enable("ENABLE DEMO EXECUTION"))
    assert st["enabled"] and st["balance"] == 1000.0 and store.get_setting("execution", {})["enabled"]
    assert not x.disable()["enabled"]


def test_live_is_not_selectable(cfg, tmp_path, monkeypatch):
    monkeypatch.setattr("jarvis.execution.has_keys", lambda: True)
    monkeypatch.setattr("jarvis.execution.secret", lambda k: "k")
    x = Executor(Bus(), Store(tmp_path / "s.sqlite"), {**cfg["execution"], "environment": "live"}, Halt())
    with pytest.raises(Rejected, match="Demo"):
        x._exchange()


def test_approve_places_bracket_sized_and_capped(cfg, tmp_path):
    ex = FakeEx()
    x, store, bus, _, sig, active = _setup(cfg, tmp_path, ex)
    out = asyncio.run(x.approve(sig["id"], {}, active))
    kinds = [c[0] for c in ex.calls]
    assert kinds == ["margin", "leverage", "entry", "stop", "tp"]
    risk_qty = 1000 * 0.005 / (2000.1 - 1980.0)                  # 0.5% of free USDT / stop distance
    assert out["qty"] == float(f"{min(risk_qty, 500 / 2000.1):.3f}") and out["status"] == "open"
    stop, tp = ex.calls[3], ex.calls[4]
    assert stop[2] == "sell" and stop[4] == {"stopLossPrice": 1980.0, "reduceOnly": True}
    assert tp[4] == {"takeProfitPrice": 2036.0, "reduceOnly": True}
    assert bus.snapshot[f"execution:{sig['id']}"]["status"] == "open"
    with pytest.raises(Rejected, match="already has a decision"):
        asyncio.run(x.approve(sig["id"], {}, active))


def test_approve_gates(cfg, tmp_path):
    x, store, _, h, sig, active = _setup(cfg, tmp_path, enabled=False)
    with pytest.raises(Rejected, match="execution is off"):
        asyncio.run(x.approve(sig["id"], {}, active))
    x.enabled = True
    h.activate("test")
    with pytest.raises(Rejected, match="kill switch"):
        asyncio.run(x.approve(sig["id"], {}, active))
    h.resume()
    with pytest.raises(Rejected, match="entry window"):
        asyncio.run(x.approve(sig["id"], {}, {SYM: {**sig, "actionable": False}}))
    with pytest.raises(Rejected, match="no longer active"):
        asyncio.run(x.approve(sig["id"], {}, {}))
    with pytest.raises(Rejected, match="losing side"):
        asyncio.run(x.approve(sig["id"], {"stop": 2010.0}, active))
    with pytest.raises(Rejected, match="risk must be"):
        asyncio.run(x.approve(sig["id"], {"risk_pct": 5}, active))
    spot = store.insert({**sig, "symbol": "spot:ETH/USDT"})
    spot.pop("id")
    with pytest.raises(Rejected, match="stays paper"):
        asyncio.run(x.approve(store.insert(spot)["id"], {}, active))


def test_price_moved_rejects_without_sending(cfg, tmp_path):
    ex = FakeEx(price=2010.0)                                   # 0.5R past the entry
    x, *_, sig, active = _setup(cfg, tmp_path, ex)
    with pytest.raises(Rejected, match="moved"):
        asyncio.run(x.approve(sig["id"], {}, active))
    assert not [c for c in ex.calls if c[0] == "entry"]


def test_modify_overrides_levels(cfg, tmp_path):
    ex = FakeEx()
    x, *_, sig, active = _setup(cfg, tmp_path, ex)
    out = asyncio.run(x.approve(sig["id"], {"stop": 1990.0, "tp": 2030.0, "risk_pct": 0.25}, active))
    assert out["modified"] and out["stop"] == 1990.0 and ex.calls[3][4]["stopLossPrice"] == 1990.0


def test_stop_failure_closes_position_immediately(cfg, tmp_path):
    ex = FakeEx(fail_on="stop")
    x, *_, sig, active = _setup(cfg, tmp_path, ex)
    out = asyncio.run(x.approve(sig["id"], {}, active))
    assert out["status"] == "failed" and "position closed at market" in out["reason"]
    assert [c[0] for c in ex.calls][-1] == "close" and ex.calls[-1][4] == {"reduceOnly": True}


def test_monitor_closes_and_cancels_other_leg(cfg, tmp_path):
    ex = FakeEx()
    x, store, *_ , sig, active = _setup(cfg, tmp_path, ex)
    asyncio.run(x.approve(sig["id"], {}, active))
    ex.positions["ETH/USDT:USDT"] = 0.0                          # TP filled on the exchange
    asyncio.run(x.check_open())
    done = store.execution_for(sig["id"])
    assert done["status"] == "closed" and done["pnl"] == pytest.approx(2.9)
    assert {c[1] for c in ex.calls if c[0] == "cancel"} == {done["orders"]["stop"], done["orders"]["tp"]}


def test_kill_flattens_account_and_paper(cfg, tmp_path):
    ex = FakeEx()
    x, store, bus, h, sig, active = _setup(cfg, tmp_path, ex)
    asyncio.run(x.approve(sig["id"], {}, active))
    out = asyncio.run(x.flatten_all())
    assert out["cancelled"] == 2 and out["closed"] == ["ETH/USDT:USDT"] and not out["errors"]
    assert store.execution_for(sig["id"])["reason"] == "kill switch"
    p = Paper(bus, store, cfg["paper"], cfg["capital"]["instruments"], {"perp": 0.05}, 0.02)
    p.on_quote(SYM, 2000, 2000.1, 0)
    p.on_signal({**sig, "status": "active"}, 0)
    assert p.flatten("kill switch", 1) == 1 and not p.open and store.positions()[0]["reason"] == "kill switch"


def test_halt_vetoes_desks(cfg, tmp_path, monkeypatch):
    from conftest import NOW, frames
    from jarvis import halt as halt_mod
    from jarvis.station.engine import Station
    st = Station(Bus(), Store(tmp_path / "s.sqlite"), {"X": {"pip": 0.01, "max_spread": 4.0}}, cfg["station"])
    f = frames(0.05)
    px = float(f["MINUTE_15"].close.iloc[-1])
    st.on_price({"symbol": "X", "bid": px, "ask": px + 0.01}, NOW * 1000)
    h = Halt()
    h.active = True
    monkeypatch.setattr("jarvis.station.engine.halt", h)
    d = st.on_frames("X", f, NOW)
    assert d["call"] == "HOLD" and any(REASON in r for r in d["hold_why"]) and not st.active
    assert halt_mod.halt is not h  # the process-wide instance wasn't touched


def test_voice_kill_needs_confirmation(cfg, tmp_path):
    from test_voice import _tools
    fired = []

    async def on_kill():
        fired.append(1)
        return {"paper_closed": 1, "exchange": {"cancelled": 2, "closed": ["ETH/USDT:USDT"], "errors": []}}

    a = Assistant(_tools(tmp_path, cfg), [], 6, on_kill=on_kill)
    assert asyncio.run(a.ask("confirm"))[1] == "none" and not fired          # nothing armed: not a kill
    assert "armed" in asyncio.run(a.ask("Jarvis, kill switch"))[0] and not fired
    reply = asyncio.run(a.ask("confirm"))[0]
    assert fired == [1] and "2 orders cancelled, 1 positions closed" in reply
    asyncio.run(a.ask("kill switch"))
    a.kill_armed_at -= 60                                                     # too slow
    assert "timed out" in asyncio.run(a.ask("confirm"))[0] and fired == [1]


def test_telegram_filters_and_sends(cfg):
    sent = []

    def handler(req):
        sent.append(json.loads(req.content)["text"])
        return httpx.Response(200, json={"ok": True})

    tg = Telegram(Bus(), cfg["telegram"], http=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    tg.token, tg.chat = "t", "c"
    assert asyncio.run(tg.send("hello")) and sent == ["Ras · hello"]
    assert tg.wants({"type": "execution"}, 1) and tg.wants({"type": "signal"}, 3) and not tg.wants({"type": "signal"}, 2)
    x = {"type": "execution", "status": "open", "symbol": SYM, "side": "long", "qty": 0.25, "stop": 1980, "tp": 2036,
         "opened": int(time.time() * 1000)}
    assert announce.classify(x, set(), 0)[0].startswith("Order filled: long ETH")
    assert announce.classify({**x, "opened": 1}, set(), 10) is None          # replayed snapshot is ignored
