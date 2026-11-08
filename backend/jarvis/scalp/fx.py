"""Signal Station scalp mode: 15M bias / 5M structure / 1M entry for the six instruments, session-gated."""
import logging
import time
from datetime import datetime, timezone
from typing import Any, Callable

import pandas as pd

from jarvis.bus import Bus
from jarvis.halt import REASON as HALT_REASON, halt
from jarvis.station.context import live_quote
from jarvis.indicators import atr
from jarvis.scalp import core, risk
from jarvis.scalp.book import Book, r_of
from jarvis.scalp.sessions import scalp_session, window
from jarvis.scalp.strategies import FX_STRATEGIES
from jarvis.station import filters
from jarvis.station.agents import RiskReport, digits
from jarvis.station.commander import decide
from jarvis.station.context import Context

log = logging.getLogger("jarvis.scalp.fx")
NAMES = {"GBP_USD": "cable", "EUR_USD": "euro", "USD_JPY": "dollar-yen", "XAU_USD": "gold", "XAG_USD": "silver", "WTI_USD": "oil"}
DXY_LEGS = ("EUR_USD", "GBP_USD", "USD_JPY")


def fx_extra(sym: str, ins: dict[str, Any], sessions_cfg: dict[str, Any], calendar: list[dict[str, Any]],
             now_s: float) -> tuple[str | None, list[dict[str, Any]], dict[str, Any]]:
    """(scalp session, this instrument's high-impact events, strategy extras) — shared by the live desk and the backtester."""
    now_ms = int(now_s * 1000)
    session = scalp_session(sym, sessions_cfg, now_ms)
    now_dt = datetime.fromtimestamp(now_s, timezone.utc)
    windows = {n: window(spec, now_dt) for n, spec in sessions_cfg.items()}
    anchor = {"overlap": "ny_open", "london_open": "london_open", "ny_open": "ny_open", "tokyo": "tokyo"}.get(session or "")
    events = [e for e in calendar if e["impact"] == "High" and e["country"] in ins.get("ccy", [])]
    return session, events, {"session": session, "windows": windows, "events": events, "all_events": calendar,
                             "round": ins.get("round"), "vwap_anchor_s": windows[anchor][0] / 1000 if anchor else now_s - 3600}


class FxScalpDesk:
    def __init__(self, bus: Bus, book: Book, instruments: dict[str, Any], cfg: dict[str, Any], station_cfg: dict[str, Any],
                 ctx: Context, prices: dict[str, dict[str, Any]], modes: dict[str, dict[str, bool]],
                 open_signals: Callable[[], list[dict[str, Any]]], tz: str,
                 adjust: dict[str, int] | None = None) -> None:
        self.bus, self.book, self.instruments, self.cfg, self.station_cfg = bus, book, instruments, cfg, station_cfg
        self.ctx, self.prices, self.modes, self.open_signals, self.tz = ctx, prices, modes, open_signals, tz
        self.frames: dict[str, dict[str, pd.DataFrame]] = {}
        self.last_m1: dict[str, int] = {}
        self.adjust = adjust if adjust is not None else {}  # learned per-strategy score points (Learning.adjust)
        self.filters: dict[str, dict[str, Any]] = {}  # "SYM:scalp" -> filters.OFF-shaped (main.py shares the Station's dict)

    def enabled(self, sym: str) -> bool:
        return self.modes.get(sym, self.cfg["default_modes"])["scalp"]

    def wants(self, sym: str) -> bool:
        # 1M of the USD legs feed the DXY filter whenever any scalp is on
        return self.enabled(sym) or (sym in DXY_LEGS and any(self.enabled(s) for s in self.instruments))

    def on_frames(self, sym: str, frames: dict[str, pd.DataFrame], now_s: float | None = None) -> dict[str, Any] | None:
        self.frames[sym] = frames
        m1 = frames["MINUTE"]
        if not self.enabled(sym) or len(m1) < 60 or len(frames["MINUTE_5"]) < 60 or len(frames["MINUTE_15"]) < 60:
            return None
        if self.last_m1.get(sym) == int(m1.time.iloc[-1]):
            return None
        self.last_m1[sym] = int(m1.time.iloc[-1])
        return self.evaluate(sym, now_s or time.time())

    def evaluate(self, sym: str, now_s: float) -> dict[str, Any]:
        f, ins, sc = self.frames[sym], self.instruments[sym], self.cfg
        now_ms = int(now_s * 1000)
        session, events, extra = fx_extra(sym, ins, sc["sessions"], self.ctx.calendar, now_s)
        x = core.ScalpInput(sym, f["MINUTE_15"], f["MINUTE_5"], f["MINUTE"], float(f["MINUTE"].close.iloc[-1]), now_s,
                            core.regime(f["MINUTE_5"]), extra)
        setups = core.run(FX_STRATEGIES, x)
        for st in setups:
            if adj := self.adjust.get(st.strategy, 0):
                st.score, st.reason = max(0, min(100, st.score + adj)), f"{st.reason}, learned {adj:+d}"
        direction, score, agree = core.confluence(setups)

        p = self.prices.get(sym, {})
        bid = p.get("bid", float(f["MINUTE"].bid_close.iloc[-1]))
        ask = p.get("ask", float(f["MINUTE"].ask_close.iloc[-1]))
        lv = core.levels(direction, ask if direction > 0 else bid, agree[0].stop, float(atr(f["MINUTE_5"]).iloc[-1]), sc) if direction else None
        tech = core.vote(direction, score, agree, x.regime, lv)

        usd = risk.usd_move({k: self.frames[k]["MINUTE"] for k in DXY_LEGS if k in self.frames})
        reasons = risk.fx_checks(sym, ins, direction, lv, bid, ask, f["MINUTE"], events, session, usd, now_ms, sc,
                                 self.station_cfg["risk"]["min_rr"])
        others = [s for s in self.open_signals() if s["symbol"] != sym or s["mode"] != "scalp"]
        today = [s for s in self.book.store.between(risk.day_start_ms(now_ms, self.tz), now_ms + 1) if s["mode"] == "scalp"]
        reasons += risk.limit_checks(today, sum(s["mode"] == "scalp" for s in others), session, sc["limits"]["fx"], now_ms)
        reasons += [HALT_REASON] if halt.active else []
        reasons += [] if live_quote(p, now_s) else ["No live quote — clears when the price stream is live"]
        if direction and (h1 := f.get("HOUR")) is not None:
            reasons += filters.reasons(self.filters.get(f"{sym}:scalp", filters.OFF), direction, filters.trend(h1),
                                       filters.choppy(h1), filters.hour(now_s, self.tz), "1H")
        veto, warn = risk.correlation_guard(sym, direction, others, sc["max_same_exposure"]) if direction else ([], [])
        d = decide(sym, [tech], RiskReport(bool(reasons + veto), reasons + veto, warn), ask - bid,
                   {**self.station_cfg, "min_confidence": sc["min_score"]}, now_ms, mode="scalp",
                   hold_time=f"≤{sc['max_hold_minutes']} min", expiry_ms=sc["max_hold_minutes"] * 60_000)
        d.update(session=session, regime=x.regime, strategies=[s.strategy for s in agree])
        self.bus.publish(d, key=f"call:{sym}:scalp")

        cur = self.book.active.get(sym)
        if d["call"] != "HOLD" and (cur is None or cur["call"] != d["call"]):
            if cur:  # opposite call supersedes the open scalp
                self.book.close(sym, "invalidated", r_of(cur, bid if cur["call"] == "BUY" else ask), now_ms)
            dg = digits(ins["pip"])
            lvx = d["levels"]
            d.update(entry_until=now_ms + sc["expiry_minutes"] * 60_000, time_stop_at=now_ms + sc["time_stop_bars"] * 60_000,
                     say=f"Scalp {'buy' if d['call'] == 'BUY' else 'sell'} {NAMES[sym]} at {lvx['entry']:.{dg}f}, "
                         f"stop {lvx['stop']:.{dg}f}, target {lvx['tp1']:.{dg}f}.")
            self.book.open(d)
        return d
