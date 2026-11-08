"""Lifecycle of scalp / crypto signals: entry window countdown, runaway, time stop, TP/SL, max hold."""
import logging
import time
from typing import Any

from jarvis.bus import Bus
from jarvis.station.store import Signal, Store, blended, check_outcome

log = logging.getLogger("jarvis.scalp")


def r_of(sig: Signal, px: float) -> float:
    lv, b = sig["levels"], 1 if sig["call"] == "BUY" else -1
    return (px - lv["entry"]) * b / abs(lv["entry"] - lv["stop"])


class Book:
    def __init__(self, bus: Bus, store: Store, modes: tuple[str, ...], cfg: dict[str, Any]) -> None:
        self.bus, self.store, self.cfg = bus, store, cfg
        self.active: dict[str, Signal] = {}
        self.exits: dict[str, float] = {"partial": 0.0}  # main.py shares the Station's dict
        for s in store.active():
            if s["mode"] in modes:
                self.active[s["symbol"]] = {**s, "actionable": False}  # entry windows don't survive a restart
                self._publish(self.active[s["symbol"]])

    def _publish(self, s: Signal) -> None:
        self.bus.publish({**s, "type": "signal"}, key=f"signal:{s['symbol']}:{s['mode']}")

    def open(self, d: dict[str, Any]) -> Signal:
        s = self.store.insert({k: v for k, v in d.items() if k != "type"})
        s.update(actionable=True, mfe=0.0)
        self.active[s["symbol"]] = s
        log.info("scalp %s %s %s score=%s", s["mode"], s["symbol"], s["call"], s["confidence"])
        self._publish(s)
        return s

    def close(self, sym: str, status: str, r: float, now_ms: int) -> None:
        sig = self.active.pop(sym)
        s = self.store.close(sig, status, blended(sig, r), now_ms)
        self._publish(s)

    async def run(self) -> None:
        """FX scalp quotes arrive on the bus; crypto quotes are pushed in by the crypto desk."""
        q = self.bus.subscribe()
        while True:
            e = await q.get()
            try:
                if e.get("type") == "price" and e["symbol"] in self.active:
                    self.on_price(e["symbol"], e["bid"], e["ask"], int(time.time() * 1000))
            except Exception as ex:
                self.bus.failed("scalp book", ex)

    def on_price(self, sym: str, bid: float, ask: float, now_ms: int) -> None:
        s = self.active.get(sym)
        if s is None:
            return
        px = bid if s["call"] == "BUY" else ask
        if out := check_outcome(s, bid, ask, now_ms, self.exits["partial"]):
            if out[0] != "tp1_partial":
                return self.close(sym, *out, now_ms)
            self.store.update(s)
            self._publish(s)
        r = r_of(s, px)
        s["mfe"] = max(s.get("mfe", 0.0), r)
        if now_ms >= s["time_stop_at"] and s["mfe"] < 0.5:
            return self.close(sym, "time_stop", r, now_ms)
        if s.get("actionable") and (now_ms >= s["entry_until"] or r >= self.cfg["runaway_r"]):
            s["actionable"] = False
            s["entry_note"] = "price ran away from the entry zone" if r >= self.cfg["runaway_r"] else "entry window closed"
            self._publish(s)
