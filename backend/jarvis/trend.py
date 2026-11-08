"""Trend desk: daily time-series momentum on the six Capital.com instruments, the rule that won the Strategies-tab
walk-forward (1/3/6/12-month blend, 3 ATR stop, chosen in every fold of the 6-market daily run).

Hold the direction of the average sign of the past 21/63/126/252-day returns; flip when it turns. There is no profit
target: the trade runs until the trend flips or the stop (3 x daily ATR(20)) is hit. The stop also sizes the trade,
so each position risks the same share of the account (volatility-scaled). A mixed reading (score 0) keeps whatever
is open. Signals go through the normal signal -> paper flow; nothing is sent to a broker."""
import asyncio
import logging
import time
from typing import Any

import numpy as np
import pandas as pd

from jarvis.bus import Bus
from jarvis.halt import halt
from jarvis.indicators import atr
from jarvis.station.agents import digits
from jarvis.station.context import live_quote
from jarvis.station.store import Signal, Store

log = logging.getLogger("jarvis.trend")
LOOKBACKS = (21, 63, 126, 252)
ATR_STOP = 3.0
HOLD_DAYS = 365  # a signal's nominal expiry; in practice the flip or the stop closes it
NAMES = {"GBP_USD": "cable", "EUR_USD": "euro", "USD_JPY": "dollar-yen", "XAU_USD": "gold", "XAG_USD": "silver", "WTI_USD": "oil"}


def score(close: pd.Series) -> float | None:
    """Average sign of the look-back returns at the last close: -1..+1 in steps of 0.5. None without 252 days."""
    if len(close) <= max(LOOKBACKS):
        return None
    c = close.to_numpy(dtype=float)
    return float(np.mean([np.sign(c[-1] / c[-1 - lb] - 1) for lb in LOOKBACKS]))


class TrendDesk:
    def __init__(self, bus: Bus, store: Store, instruments: dict[str, Any], frames: dict[str, dict[str, pd.DataFrame]],
                 prices: dict[str, dict[str, Any]], modes: dict[str, dict[str, bool]]) -> None:
        self.bus, self.store, self.instruments = bus, store, instruments
        self.frames, self.prices, self.modes = frames, prices, modes  # the Station's live dicts
        self.active: dict[str, Signal] = {s["symbol"]: s for s in store.active() if s["mode"] == "trend"}
        self.last_bar: dict[str, int] = {}
        self.calls: dict[str, dict[str, Any]] = {}  # latest daily reading per instrument (BUY / SELL / HOLD)
        for s in self.active.values():
            self._publish(s)

    def enabled(self, sym: str) -> bool:
        return bool(self.modes.get(sym, {}).get("trend", False))

    def _publish(self, s: Signal) -> None:
        self.bus.publish({**s, "type": "signal"}, key=f"signal:{s['symbol']}:trend")

    def _r(self, s: Signal, px: float) -> float:
        lv, b = s["levels"], 1 if s["call"] == "BUY" else -1
        return round((px - lv["entry"]) * b / abs(lv["entry"] - lv["stop"]), 2)

    def _exit_px(self, s: Signal, sym: str) -> float:
        p = self.prices.get(sym, {})
        return p.get("bid" if s["call"] == "BUY" else "ask") or s["levels"]["entry"]

    def close(self, sym: str, status: str, r: float, now_ms: int, why: str = "") -> None:
        sig = self.active.pop(sym)
        if why:
            sig["close_note"] = why
            self.store.update(sig)
        s = self.store.close(sig, status, r, now_ms)
        log.info("trend %s %s %s R=%s %s", s["id"], sym, status, r, why)
        self._publish(s)

    def on_price(self, p: dict[str, Any], now_ms: int) -> None:
        sym = p["symbol"]
        s = self.active.get(sym)
        if s is None:
            return
        b = 1 if s["call"] == "BUY" else -1
        px = p["bid"] if b > 0 else p["ask"]
        if (px - s["levels"]["stop"]) * b <= 0:
            self.close(sym, "hit_sl", -1.0, now_ms)
        elif now_ms >= s["expires"]:
            self.close(sym, "expired", self._r(s, px), now_ms)

    def _stopped_in_this_trend(self, sym: str, call: str) -> bool:
        """The backtested rule enters only when the direction changes, so after a stop it stays out until a flip."""
        last = next((s for s in self.store.history(sym, 200) if s["mode"] == "trend" and s["status"] != "killed"), None)
        return bool(last) and last["status"] == "hit_sl" and last["call"] == call

    def evaluate(self, sym: str, now_s: float) -> dict[str, Any] | None:
        """Re-read the daily trend for one instrument; open, flip or keep its trend position."""
        now_ms = int(now_s * 1000)
        cur = self.active.get(sym)
        if not self.enabled(sym):
            if cur:
                self.close(sym, "invalidated", self._r(cur, self._exit_px(cur, sym)), now_ms, "trend mode switched off")
            return None
        day = self.frames.get(sym, {}).get("DAY")
        p = self.prices.get(sym, {})
        if day is None or (sc := score(day.close)) is None:
            return None
        live = live_quote(p, now_s)  # no quotes at the weekend / overnight breaks
        if not live and "bid_close" in day:
            p = {"bid": float(day.bid_close.iloc[-1]), "ask": float(day.ask_close.iloc[-1])}
        elif not live:
            p = {"bid": float(day.close.iloc[-1]), "ask": float(day.close.iloc[-1])}
        a = float(atr(day, 20).iloc[-1])
        side = int(np.sign(sc))
        call = "BUY" if side > 0 else "SELL" if side < 0 else "HOLD"
        months = {21: "1m", 63: "3m", 126: "6m", 252: "12m"}
        c = day.close.to_numpy(dtype=float)
        parts = [f"{months[lb]} {'up' if c[-1] > c[-1 - lb] else 'down'}" for lb in LOOKBACKS]
        d: dict[str, Any] = {
            "type": "call", "symbol": sym, "mode": "trend", "priority": 3, "call": call, "lean": call,
            "confidence": int(round(abs(sc) * 100)), "votes": [], "risk": {"veto": False, "reasons": [], "warnings": []},
            "reasons": [f"Daily trend: {', '.join(parts)} (score {sc:+.2f})"], "warnings": [], "watch": [],
            "hold_why": [] if side else ["Mixed trend (half the look-backs up, half down): keeping what is open"],
            "created": now_ms, "strategies": ["trend_following"], "hold_time": "weeks to months, until the trend flips",
            "live_price": live}
        if not live:
            d["warnings"] = ["Market closed: levels use the last close; nothing is opened or flipped until prices return."]
        self.calls[sym] = d
        if halt.active and (cur is None or (side and cur["call"] != call)):
            d.update(call="HOLD", hold_why=["Trading is paused (kill switch / halt)"])
            self.bus.publish(d, key=f"call:{sym}:trend")
            return d
        if side and cur is None and self._stopped_in_this_trend(sym, call):
            d.update(call="HOLD", hold_why=["Stopped out of this trend: waiting for it to flip, as the tested rule does"])
            self.bus.publish(d, key=f"call:{sym}:trend")
            return d
        if side:
            entry = p["ask"] if side > 0 else p["bid"]
            stop = entry - side * ATR_STOP * a
            dg = digits(self.instruments[sym]["pip"])
            d["levels"] = {"entry": entry, "entry_low": entry, "entry_high": entry, "stop": stop, "tp1": None, "tp2": None}
            d.update(expires=now_ms + HOLD_DAYS * 86_400_000, rr=None,
                     say=f"Trend {'buy' if side > 0 else 'sell'} {NAMES.get(sym, sym)} at {entry:.{dg}f}, stop {stop:.{dg}f}, "
                         f"held until the daily trend flips.")
        self.bus.publish(d, key=f"call:{sym}:trend")
        if side and live and (cur is None or cur["call"] != call):
            if cur:
                self.close(sym, "invalidated", self._r(cur, self._exit_px(cur, sym)), now_ms, "daily trend flipped")
            s = self.store.insert({k: v for k, v in d.items() if k != "type"})
            self.active[sym] = s
            log.info("trend %s %s %s score=%+.2f", s["id"], sym, call, sc)
            self._publish(s)
        return d

    def tick(self, now_s: float) -> None:
        """Evaluate each instrument once per new daily candle (and once at start-up), or when its mode changes."""
        for sym in self.instruments:
            day = self.frames.get(sym, {}).get("DAY")
            key = (int(day.time.iloc[-1]) if day is not None and len(day) else 0) * 2 + self.enabled(sym)
            if self.last_bar.get(sym) != key and (day is not None or not self.enabled(sym)):
                d = self.evaluate(sym, now_s)
                # without live prices it only showed the call: evaluate again once quotes return (Monday open)
                if (d is not None and d.get("live_price", True)) or not self.enabled(sym):
                    self.last_bar[sym] = key

    async def run(self) -> None:
        q = self.bus.subscribe()

        async def clock() -> None:
            while True:
                try:
                    self.tick(time.time())
                except Exception as e:  # keep the desk alive; the next tick retries
                    log.warning("trend desk tick failed: %r", e)
                await asyncio.sleep(30)

        task = asyncio.create_task(clock())
        try:
            while True:
                e = await q.get()
                try:
                    if e.get("type") == "price" and e["symbol"] in self.active:
                        self.on_price(e, int(time.time() * 1000))
                except Exception as ex:
                    self.bus.failed("trend desk", ex)
        finally:
            task.cancel()
