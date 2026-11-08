"""Signal Station: candles + live quotes in -> agents -> commander -> store -> bus."""
import logging
import time
from typing import Any

import pandas as pd

from jarvis.bus import Bus
from jarvis.halt import REASON as HALT_REASON, halt
from jarvis.station import agents, filters
from jarvis.station.commander import decide
from jarvis.station.context import Context, live_quote
from jarvis.station.store import Signal, Store, blended, check_invalidation, check_outcome

log = logging.getLogger("jarvis.station")


class Station:
    def __init__(self, bus: Bus, store: Store, instruments: dict[str, Any], cfg: dict[str, Any],
                 ctx: Context | None = None, modes: dict[str, dict[str, bool]] | None = None) -> None:
        self.bus, self.store, self.instruments, self.cfg = bus, store, instruments, cfg
        self.ctx = ctx or Context()
        self.modes = modes if modes is not None else {}
        self.exits: dict[str, float] = {"partial": 0.0}
        self.filters: dict[str, dict[str, Any]] = {}  # "SYM:mode" -> filters.OFF-shaped; main.py shares it with the scalp desk
        self.tz = "UTC"  # live dict shared with the scalp book and paper (Settings / Edge scan)
        self.prices: dict[str, dict[str, Any]] = {}
        self.frames: dict[str, dict[str, pd.DataFrame]] = {}
        self.last_m15: dict[str, int] = {}
        self.calls: dict[str, dict[str, Any]] = {}  # latest commander decision per symbol
        self.active: dict[str, Signal] = {s["symbol"]: s for s in store.active() if s["mode"] == "intraday"}
        for s in self.active.values():
            self._publish_signal(s)

    def _publish_signal(self, s: Signal) -> None:
        self.bus.publish({**s, "type": "signal"}, key=f"signal:{s['symbol']}:{s['mode']}")

    def _close(self, sym: str, status: str, r: float, now_ms: int) -> None:
        sig = self.active.pop(sym)
        s = self.store.close(sig, status, blended(sig, r), now_ms)
        log.info("signal %s %s %s R=%s", s["id"], sym, status, r)
        self._publish_signal(s)

    async def run(self) -> None:
        q = self.bus.subscribe()
        while True:
            e = await q.get()
            try:
                if e.get("type") == "price" and e["symbol"] in self.instruments:
                    self.on_price(e, int(time.time() * 1000))
            except Exception as ex:
                self.bus.failed("signal station", ex)

    def on_price(self, p: dict[str, Any], now_ms: int) -> None:
        sym = p["symbol"]
        self.prices[sym] = {**p, "seen": now_ms / 1000}
        self.check(sym, p["bid"], p["ask"], now_ms)

    def check(self, sym: str, bid: float, ask: float, now_ms: int) -> None:
        if sym in self.active and (out := check_outcome(self.active[sym], bid, ask, now_ms, self.exits["partial"])):
            if out[0] == "tp1_partial":  # half banked, stop at entry: still open
                self.store.update(self.active[sym])
                self._publish_signal(self.active[sym])
            else:
                self._close(sym, *out, now_ms)

    def on_frames(self, sym: str, frames: dict[str, pd.DataFrame], now_s: float | None = None) -> dict[str, Any] | None:
        """Called by the candle poller. Re-evaluates only when a new 15M candle has closed."""
        self.frames[sym] = frames
        if not self.modes.get(sym, {}).get("intraday", True):
            return None
        m15, h1 = frames["MINUTE_15"], frames["HOUR"]
        if len(m15) < 100 or len(h1) < 60 or len(frames["HOUR_4"]) < 200:
            return None
        last = int(m15.time.iloc[-1])
        if self.last_m15.get(sym) == last:
            return None
        self.last_m15[sym] = last
        now_s = now_s or time.time()
        now_ms = int(now_s * 1000)

        if sym in self.active and self.active[sym].get("invalidation") is not None:
            if out := check_invalidation(self.active[sym], (int(h1.time.iloc[-1]) + 3600) * 1000, float(h1.close.iloc[-1])):
                self._close(sym, *out, now_ms)

        ins, p = self.instruments[sym], self.prices.get(sym, {})
        bid, ask = (p.get("bid"), p.get("ask")) if live_quote(p, now_s) else (None, None)
        tech = agents.technical(frames["HOUR_4"], h1, m15, ins["pip"], self.cfg)
        news = agents.news(sym, self.ctx, now_ms)
        trial = self.cfg.get("news_vote", "shadow") == "shadow"
        acted = agents.Vote("news", "HOLD", 0, ["News vote on trial: logged, not acted on"]) if trial else news
        votes = [tech, agents.macro(sym, self.ctx, self.prices, ins, self.cfg, now_ms), acted, agents.sentiment(sym, self.ctx)]
        risk = agents.risk_officer(tech, h1, m15, bid, ask, sym, ins, self.ctx.calendar, now_s, self.cfg)
        if halt.active:
            risk.reasons.append(HALT_REASON)
            risk.veto = True
        if tech.direction != "HOLD" and (blocked := filters.reasons(
                self.filters.get(f"{sym}:intraday", filters.OFF), 1 if tech.direction == "BUY" else -1,
                filters.trend(frames["DAY"]) if "DAY" in frames else 0, filters.choppy(h1), filters.hour(now_s, self.tz), "daily")):
            risk.reasons += blocked
            risk.veto = True
        d = decide(sym, votes, risk, (ask - bid) if bid and ask else None, self.cfg, now_ms)
        if trial and tech.direction != "HOLD":
            alt = decide(sym, [news if v is acted else v for v in votes], risk, (ask - bid) if bid and ask else None, self.cfg, now_ms)
            d["news_trial"] = {"vote": news.direction, "confidence": news.confidence, "call": alt["call"], "conf": alt["confidence"]}
            if alt["call"] != d["call"]:  # the vote would have changed whether this trade exists
                self.store.set_setting("news_trial_log", self.store.get_setting("news_trial_log", [])[-499:]
                                       + [{"at": now_ms, "symbol": sym, "acted": d["call"], "with_news": alt["call"], "vote": news.direction}])
        self.calls[sym] = d
        self.bus.publish(d, key=f"call:{sym}:intraday")

        cur = self.active.get(sym)
        if d["call"] != "HOLD" and (cur is None or cur["call"] != d["call"]):
            if cur:  # opposite call supersedes the open one
                px = p.get("bid" if cur["call"] == "BUY" else "ask", cur["levels"]["entry"])
                b = 1 if cur["call"] == "BUY" else -1
                self._close(sym, "invalidated", round((px - cur["levels"]["entry"]) * b / abs(cur["levels"]["entry"] - cur["levels"]["stop"]), 2), now_ms)
            self.active[sym] = self.store.insert({k: v for k, v in d.items() if k != "type"})
            log.info("new signal %s %s conf=%s", sym, d["call"], d["confidence"])
            self._publish_signal(self.active[sym])
        return d
