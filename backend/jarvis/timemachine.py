"""Time Machine: before you take a signal, replay the rest of the trade 10,000 times from the symbol's own history.

Each replay starts at the entry and, for every bar until the signal expires, draws a real past bar from the same time
of day (± `window` bars), scaled to the current price. The stop is checked before TP1 in each bar, so a bar that touches
both counts as a loss. Costs (spread, slippage on both fills, fees) come off every replay's R. Partials, time stops and
invalidation are left out: the odds are for TP1 first, the stop first, or still open at expiry.
Same signal, same candles, same seed: the same numbers. It is a simulation from past bars, not a prediction."""
import asyncio
import logging
import math
from typing import Any, Awaitable, Callable

import numpy as np
import pandas as pd

from jarvis import history
from jarvis.bus import Bus
from jarvis.station.store import Signal, Store

log = logging.getLogger("jarvis.timemachine")

TF = {"intraday": ("MINUTE_15", 900), "scalp": ("MINUTE", 60), "crypto": ("1m", 60)}  # mode -> history tf, bar seconds
MAX_GAP_S = 3 * 3600  # a daily break (metals / oil 21:00-22:00 UTC) keeps its reopening move; weekends don't
DEFAULTS: dict[str, Any] = {"replays": 10_000, "seed": 19, "window": 2, "min_samples": 20,
                            "days": {"MINUTE_15": 120, "MINUTE": 30, "1m": 30}}


def pools(bars: pd.DataFrame, bar_s: int, window: int) -> tuple[np.ndarray, list[np.ndarray], np.ndarray]:
    """(ratios, pool per time-of-day slot, open per slot). ratios[i] = bar i's (high, low, close) over the previous
    close, where the previous bar is at most MAX_GAP_S earlier (so weekends are skipped). pool[s] = indices into ratios
    of every past bar whose slot is within `window` of s, wrapping round midnight. open[s] is False for a time of day
    that never traded in the history (a daily break): replays stand still through it."""
    t = bars.time.to_numpy(dtype=np.int64)
    c = bars.close.to_numpy(dtype=float)
    dt = np.diff(t)
    ok = np.r_[False, (dt > 0) & (dt <= MAX_GAP_S)]
    pc = np.r_[np.nan, c[:-1]][ok]
    ratios = np.column_stack([bars.high.to_numpy(dtype=float)[ok] / pc, bars.low.to_numpy(dtype=float)[ok] / pc, c[ok] / pc])
    n = 86_400 // bar_s
    slot = (t[ok] % 86_400) // bar_s
    by_slot = [np.flatnonzero(slot == s) for s in range(n)]
    traded = np.bincount((t % 86_400) // bar_s, minlength=n) > 0
    return ratios, [np.concatenate([by_slot[(s + k) % n] for k in range(-window, window + 1)]) for s in range(n)], traded


def simulate(bars: pd.DataFrame, bar_s: int, side: int, entry: float, stop: float, tp1: float, start_s: float,
             horizon_bars: int, cost_px: float = 0.0, replays: int = 10_000, seed: int = 19, window: int = 2,
             min_samples: int = 20) -> dict[str, Any]:
    """Odds of TP1 first / stop first / neither by expiry, and the average R after costs. {"error": ...} when the
    history can't support it."""
    risk = abs(entry - stop)
    if risk <= 0 or (tp1 - entry) * side <= 0 or (entry - stop) * side <= 0:
        return {"error": "levels don't make sense (stop or TP1 on the wrong side of the entry)"}
    if horizon_bars < 1:
        return {"error": "the signal expires before the next bar"}
    ratios, pool, traded = pools(bars, bar_s, window)
    n = len(pool)
    s0 = int(start_s // bar_s) % n
    slots = [s for s in ((s0 + j) % n for j in range(horizon_bars)) if traded[s]]
    if not slots:
        return {"error": "the market is closed until the signal expires"}
    thin = min(len(pool[s]) for s in slots)
    if thin < min_samples:
        return {"error": f"not enough history: {thin} past bars for some times of day, need {min_samples}"}

    rng = np.random.default_rng(seed)
    p = np.full(replays, float(entry))
    out = np.zeros(replays, dtype=np.int8)  # 0 still open, 1 TP1 first, -1 stop first
    for s in slots:
        hi, lo, cl = (p[:, None] * ratios[pool[s][rng.integers(0, len(pool[s]), replays)]]).T
        live = out == 0
        hit_stop = live & ((lo <= stop) if side > 0 else (hi >= stop))
        hit_tp = live & ~hit_stop & ((hi >= tp1) if side > 0 else (lo <= tp1))
        out[hit_stop], out[hit_tp] = -1, 1
        p = cl

    r_tp1 = abs(tp1 - entry) / risk
    cost_r = cost_px / risk
    r = np.where(out == 1, r_tp1, np.where(out == -1, -1.0, (p - entry) * side / risk)) - cost_r
    odds: dict[str, Any] = {
        "replays": replays, "seed": seed, "window": window, "bars": len(slots), "bar_s": bar_s,
        "p_tp1": round(float(np.mean(out == 1)), 4), "p_stop": round(float(np.mean(out == -1)), 4),
        "p_neither": round(float(np.mean(out == 0)), 4), "avg_r": round(float(r.mean()), 3),
        "r_tp1": round(r_tp1, 3), "cost_r": round(cost_r, 3), "samples": thin,
        "history_days": round((int(bars.time.iloc[-1]) - int(bars.time.iloc[0])) / 86_400, 1)}
    return {**odds, **verdict(odds)}


def breakeven(r_tp1: float, cost_r: float) -> float:
    """Share of the replays that hit a side that must hit TP1 first to break even after costs:
    share·(R − c) = (1 − share)·(1 + c)  →  share = (1 + c) / (1 + R)."""
    return (1 + cost_r) / (1 + r_tp1)


def verdict(o: dict[str, Any]) -> dict[str, Any]:
    """One plain line from the numbers, plus any notes that point the other way. lean: good / bad / flat."""
    hit = o["p_tp1"] + o["p_stop"]
    notes = []
    if hit < 0.05:
        return {"lean": "flat", "breakeven": None, "share": None, "notes": [],
                "verdict": f"Rarely reaches TP1 or the stop before expiry ({hit:.0%} of replays); "
                           f"average R {o['avg_r']:+.2f} after costs"}
    share, be = o["p_tp1"] / hit, breakeven(o["r_tp1"], o["cost_r"])
    d = (share - be) * 100
    toward = "TP1" if d > 0 else "the stop"
    if abs(d) < 5:
        label, lean = ("Close to a coin flip" if abs(o["r_tp1"] - 1) < 0.05 else "Close to break-even"), "flat"
    else:
        label, lean = f"{'Strongly toward' if abs(d) >= 15 else 'Leans toward'} {toward}", "good" if d > 0 else "bad"
    if o["p_neither"] > 0.4:
        notes.append(f"{o['p_neither']:.0%} of replays are still open at expiry; the price then decides those.")
    if (lean == "good" and o["avg_r"] < 0) or (lean == "bad" and o["avg_r"] > 0):
        notes.append(f"Average R {o['avg_r']:+.2f} after costs points the other way, because of the replays still open at expiry.")
    return {"lean": lean, "breakeven": round(be, 4), "share": round(share, 4), "notes": notes,
            "verdict": f"{label}: TP1 first {o['p_tp1']:.1%} vs stop first {o['p_stop']:.1%} "
                       f"(needs {be:.0%} of those after costs, got {share:.0%})"}


def cost_px(sig: Signal, cfg: dict[str, Any]) -> float:
    """Round-trip costs in price, as the backtester charges them: the spread at the signal, slippage on both fills,
    and (crypto) taker fees on both sides."""
    entry, spread = sig["levels"]["entry"], sig.get("spread") or 0.0
    if sig["mode"] == "crypto":
        sc = cfg["crypto"]["scanner"]
        fee = sc["fees_pct"].get(sig.get("market") or sig["symbol"].split(":")[0], 0.0)
        return spread + 2 * (sc["slippage_pct"] + fee) / 100 * entry
    pip = cfg["capital"]["instruments"].get(sig["symbol"], {}).get("pip", 0.0)
    return spread + 2 * cfg["paper"]["fx"]["slippage_pips"] * pip


Loader = Callable[[str, str, float], Awaitable[pd.DataFrame]]


class TimeMachine:
    """Watches the bus for new signals and adds `odds` to each: first {"pending": True}, then the result (or
    {"error": ...}). The live signal dict is updated in place, saved, and republished so the card and the approval
    screen show it."""

    def __init__(self, bus: Bus, store: Store, find: Callable[[int], Signal | None], cfg: dict[str, Any],
                 load: Loader | None = None) -> None:
        self.bus, self.store, self.find, self.cfg = bus, store, find, cfg
        self.tm = {**DEFAULTS, **cfg.get("timemachine", {})}
        self.load = load or self._load
        self.lock = asyncio.Lock()  # one download / simulation at a time
        self.seen: set[int] = set()

    async def _load(self, symbol: str, tf: str, days: float) -> pd.DataFrame:
        try:
            return await history.ensure(symbol, tf, days, self.cfg)
        except Exception as e:  # no credentials / offline: use whatever is already saved
            log.info("time machine: no fresh %s %s history (%s), using saved candles", symbol, tf, e)
            return history.load("capital" if symbol in self.cfg["capital"]["instruments"] else "crypto", symbol, tf)

    async def run(self) -> None:
        q = self.bus.subscribe()
        while True:
            e = await q.get()
            odds = e.get("odds") or {"pending": True}  # none yet, or left pending / failed before a restart: (re)run
            if e.get("type") == "signal" and e.get("status") == "active" and e.get("levels") and e.get("mode") in TF \
                    and (odds.get("pending") or odds.get("error")) and e["id"] not in self.seen:
                self.seen.add(e["id"])
                asyncio.create_task(self.attach(e["id"]))

    def _publish(self, s: Signal) -> None:
        self.bus.publish({**s, "type": "signal"}, key=f"signal:{s['symbol']}:{s['mode']}")

    async def attach(self, sid: int) -> None:
        if (sig := self.find(sid)) is None:
            return
        sig["odds"] = {"pending": True}
        self._publish(sig)
        try:
            odds = await self.compute(sig)
        except Exception as e:
            log.warning("time machine failed for signal %s: %r", sid, e)
            odds = {"error": f"Time Machine failed: {e}"[:200]}
        live = self.find(sid)
        target = live or self.store.get(sid)  # closed while we ran: still save the odds with it
        if target is None:
            return
        target["odds"] = odds
        self.store.update(target)
        if live:
            self._publish(live)
        log.info("time machine %s %s: %s", sid, sig["symbol"], odds.get("verdict") or odds.get("error"))

    async def compute(self, sig: Signal) -> dict[str, Any]:
        tf, bar_s = TF[sig["mode"]]
        created_s = sig["created"] / 1000
        async with self.lock:
            bars = await self.load(sig["symbol"], tf, self.tm["days"][tf])
            if bars is None or bars.empty:
                return {"error": f"no saved {tf} candles for {sig['symbol']} yet"}
            bars = bars[bars.time + bar_s <= created_s].reset_index(drop=True)  # only bars closed before the signal
            lv, side = sig["levels"], 1 if sig["call"] == "BUY" else -1
            return await asyncio.to_thread(
                simulate, bars, bar_s, side, lv["entry"], lv["stop"], lv["tp1"], created_s,
                math.ceil((sig["expires"] - sig["created"]) / 1000 / bar_s), cost_px(sig, self.cfg),
                self.tm["replays"], self.tm["seed"], self.tm["window"], self.tm["min_samples"])
