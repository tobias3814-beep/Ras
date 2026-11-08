"""Crypto Scalping Desk: hourly top-volume watchlist (spot + USDT perps), live bid/ask, per-minute scan, Opportunity Board."""
import asyncio
import logging
import math
import re
import time
from typing import Any, Callable

import ccxt.pro as ccxtpro
import pandas as pd

from jarvis.bus import Bus
from jarvis.halt import REASON as HALT_REASON, halt
from jarvis.indicators import atr, ema
from jarvis.scalp import core, risk
from jarvis.scalp.book import Book
from jarvis.scalp.strategies import CRYPTO_STRATEGIES
from jarvis.station.agents import RiskReport
from jarvis.station.commander import decide

log = logging.getLogger("jarvis.crypto.desk")
TF_S = {"1m": 60, "5m": 300, "15m": 900, "1h": 3600}
BTC = {"spot": "BTC/USDT", "perp": "BTC/USDT:USDT"}


def to_df(rows: list[list[float]], tf: str, now_s: float) -> pd.DataFrame:
    df = pd.DataFrame(rows, columns=["time", "open", "high", "low", "close", "volume"])
    df["time"] = (df.time // 1000).astype(int)
    return df[df.time + TF_S[tf] <= now_s].reset_index(drop=True)  # closed candles only


def imbalance(book: dict[str, Any], band: float = 0.001) -> float | None:
    """(bid size - ask size) / total within ±0.1% of mid."""
    if not book.get("bids") or not book.get("asks"):
        return None
    mid = (book["bids"][0][0] + book["asks"][0][0]) / 2
    b = sum(a for p, a, *_ in book["bids"] if p >= mid * (1 - band))
    a = sum(a for p, a, *_ in book["asks"] if p <= mid * (1 + band))
    return (b - a) / (b + a) if b + a else None


def btc_context(alt5: pd.DataFrame, btc5: pd.DataFrame) -> dict[str, float]:
    e = ema(btc5.close, 21)
    trend = 1 if e.iloc[-1] > e.iloc[-4] else -1 if e.iloc[-1] < e.iloc[-4] else 0
    j = pd.merge(alt5[["time", "close"]], btc5[["time", "close"]], on="time", suffixes=("_a", "_b")).tail(51)
    corr = j.close_a.pct_change().corr(j.close_b.pct_change()) if len(j) > 20 else 0.0
    return {"trend": trend, "corr": float(0.0 if math.isnan(corr) else corr)}


class CryptoDesk:
    def __init__(self, bus: Bus, book: Book, cfg: dict[str, Any], scalp_cfg: dict[str, Any], station_cfg: dict[str, Any],
                 open_signals: Callable[[], list[dict[str, Any]]], tz: str) -> None:
        self.bus, self.book, self.cfg, self.sc, self.station_cfg = bus, book, cfg, scalp_cfg, station_cfg
        self.open_signals, self.tz = open_signals, tz
        self.ex = {m: getattr(ccxtpro, x)({"enableRateLimit": True}) for m, x in cfg["markets"].items()}
        self.watch: dict[str, list[str]] = {m: [] for m in self.ex}
        self.watch_at = 0.0
        self.quotes: dict[str, tuple[float, float]] = {}
        self.frames: dict[str, dict[str, pd.DataFrame]] = {}
        self.imb: dict[str, float | None] = {}
        self.oi: dict[str, pd.Series] = {}
        self.oi_at = 0.0
        self.funding: dict[str, float] = {}
        self.listing_ok: dict[str, bool] = {}

    # ---------- watchlist ----------
    async def refresh_watchlist(self) -> None:
        c, now_ms = self.cfg, time.time() * 1000
        coins: set[str] | None = None  # bases with a real crypto perp; keeps tokenised stocks off the spot list
        if "perp" in self.ex:
            await self.ex["perp"].load_markets(reload=True)
            coins = {re.sub(r"^1000+", "", mk["base"]) for mk in self.ex["perp"].markets.values()
                     if mk.get("info", {}).get("underlyingType") == "COIN"}
        for m, ex in self.ex.items():
            if m != "perp":
                await ex.load_markets(reload=True)
            tickers = await ex.fetch_tickers()
            cands = []
            for sym, t in tickers.items():
                mk = ex.markets.get(sym)
                if not mk or not mk.get("active") or mk.get("quote") != "USDT" or mk["base"] in c["exclude_bases"] \
                        or re.search(r"(UP|DOWN|BULL|BEAR)$", mk["base"]) or (t.get("quoteVolume") or 0) < c["min_volume_usd"]:
                    continue
                if m == "perp":
                    info = mk.get("info", {})
                    if not (mk.get("swap") and mk.get("linear") and info.get("underlyingType") == "COIN"
                            and info.get("contractType") == "PERPETUAL"
                            and now_ms - int(info.get("onboardDate", now_ms)) >= c["min_listing_days"] * 86_400_000):
                        continue
                elif not mk.get("spot") or (coins is not None and mk["base"] not in coins):
                    continue
                cands.append((t["quoteVolume"], sym))
            picked = []
            for _, sym in sorted(cands, reverse=True):
                if m == "spot" and sym not in self.listing_ok:  # spot has no listing date: need N daily candles
                    self.listing_ok[sym] = len(await ex.fetch_ohlcv(sym, "1d", limit=c["min_listing_days"])) >= c["min_listing_days"]
                if m == "perp" or self.listing_ok[sym]:
                    picked.append(sym)
                if len(picked) >= c["watchlist_size"]:
                    break
            if BTC[m] in ex.markets and BTC[m] not in picked:
                picked.append(BTC[m])  # BTC context for the correlation filter
            self.watch[m] = picked
        self.watch_at = time.time()
        log.info("crypto watchlist: %s", {m: len(v) for m, v in self.watch.items()})

    # ---------- live quotes ----------
    async def watch_quotes(self, m: str) -> None:
        ex = self.ex[m]
        while True:
            syms = list(self.watch[m])
            if not syms:
                await asyncio.sleep(2)
                continue
            try:
                while syms == self.watch[m]:
                    for s, v in (await ex.watch_bids_asks(syms)).items():
                        if v.get("bid") and v.get("ask"):
                            self.quotes[f"{m}:{s}"] = (v["bid"], v["ask"])
            except Exception as e:
                log.warning("bid/ask stream %s dropped: %r", m, e)
                await asyncio.sleep(3)

    async def tick(self) -> None:
        """Once a second: run open crypto scalps against the latest quotes and push prices to the UI / paper engine."""
        last: dict[str, tuple[float, float]] = {}
        while True:
            await asyncio.sleep(1)
            now_ms = int(time.time() * 1000)
            changed = {k: q for k, q in list(self.quotes.items()) if last.get(k) != q}
            for k, (b, a) in changed.items():
                self.book.on_price(k, b, a, now_ms)
            if changed:
                last.update(changed)
                self.bus.publish({"type": "cprices", "quotes": {k: list(q) for k, q in changed.items()}})

    # ---------- scan ----------
    async def run(self) -> None:
        for m in self.ex:
            asyncio.create_task(self.watch_quotes(m))
        asyncio.create_task(self.tick())
        while True:
            try:
                await self.scan()
            except Exception as e:
                log.warning("crypto scan failed: %r", e)
            await asyncio.sleep(self.cfg["poll_seconds"] - time.time() % self.cfg["poll_seconds"] + 3)

    async def scan(self) -> None:
        if time.time() - self.watch_at > 3600:
            await self.refresh_watchlist()
        now_s = time.time()
        sem = asyncio.Semaphore(8)

        async def load(m: str, sym: str) -> None:
            async with sem:
                ex = self.ex[m]
                self.frames[f"{m}:{sym}"] = {tf: to_df(await ex.fetch_ohlcv(sym, tf, limit=n), tf, now_s)
                                              for tf, n in (("1m", 200), ("5m", 200), ("15m", 100), ("1h", 100))}

        pairs = [(m, s) for m, syms in self.watch.items() for s in syms]
        for (m, s), r in zip(pairs, await asyncio.gather(*(load(m, s) for m, s in pairs), return_exceptions=True)):
            if isinstance(r, Exception):
                log.debug("candles %s:%s failed: %r", m, s, r)

        if "perp" in self.ex and self.watch.get("perp"):
            try:
                fr = await self.ex["perp"].fetch_funding_rates(self.watch["perp"])
                self.funding = {s: v["fundingRate"] for s, v in fr.items() if v.get("fundingRate") is not None}
            except Exception as e:
                log.warning("funding failed: %r", e)

        passing = [(m, s) for m, s in pairs if self._prefilter(m, s) is None]

        async def extras(m: str, s: str) -> None:
            async with sem:
                self.imb[f"{m}:{s}"] = imbalance(await self.ex[m].fetch_order_book(s, 20))
                if m == "perp" and time.time() - self.oi_at > 300:
                    oi = await self.ex[m].fetch_open_interest_history(s, "5m", limit=30)
                    self.oi[f"{m}:{s}"] = pd.Series([o["openInterestAmount"] for o in oi], dtype=float)

        await asyncio.gather(*(extras(m, s) for m, s in passing), return_exceptions=True)
        if any(m == "perp" for m, _ in passing):
            self.oi_at = time.time()

        rows = [r for m, s in pairs if (r := self.evaluate(m, s, time.time()))]
        rows.sort(key=lambda r: (r["call"] == "HOLD", bool(r.get("filtered")), -r["score"]))
        self.bus.publish({"type": "board", "updated": int(time.time() * 1000), "rows": rows}, key="board")

    def _prefilter(self, m: str, sym: str) -> str | None:
        fr, q = self.frames.get(f"{m}:{sym}"), self.quotes.get(f"{m}:{sym}")
        if not fr or len(fr["1m"]) < 60 or len(fr["5m"]) < 120:
            return "not enough data"
        px = float(fr["1m"].close.iloc[-1])
        if float(atr(fr["5m"]).iloc[-1]) / px * 100 < self.cfg["min_atr_pct"]:
            return "ATR too low"
        if q and (q[1] - q[0]) / ((q[1] + q[0]) / 2) * 100 > self.cfg["max_spread_pct"]:
            return "spread too wide"
        return None

    def evaluate(self, m: str, sym: str, now_s: float) -> dict[str, Any] | None:
        key = f"{m}:{sym}"
        fr = self.frames.get(key)
        if not fr or fr["1m"].empty:
            return None
        bid, ask = self.quotes.get(key, (None, None))
        px = float(fr["1m"].close.iloc[-1])
        mk = self.ex[m].markets.get(sym, {})
        tick = (mk.get("precision") or {}).get("price") or 0.01
        row: dict[str, Any] = {
            "key": key, "symbol": sym, "base": mk.get("base", sym.split("/")[0]), "market": m, "price": px,
            "digits": max(0, -math.floor(math.log10(tick))) if tick < 1 else 0,
            "change_1h": round((px / float(fr["1h"].close.iloc[-1]) - 1) * 100, 2) if len(fr["1h"]) else None,
            "funding": self.funding.get(sym) if m == "perp" else None, "call": "HOLD", "score": 0, "strategies": []}
        if len(fr["5m"]) < 120:
            return {**row, "filtered": "not enough data"}
        a5 = float(atr(fr["5m"]).iloc[-1])
        row.update(atr_pct=round(a5 / px * 100, 3),
                   spread_pct=round((ask - bid) / ((ask + bid) / 2) * 100, 4) if bid and ask else None)
        if why := self._prefilter(m, sym):
            return {**row, "filtered": why}

        now_ms = int(now_s * 1000)
        x = core.ScalpInput(key, fr["15m"], fr["5m"], fr["1m"], px, now_s, core.regime(fr["5m"]), {
            "vwap_anchor_s": now_s - now_s % 86400, "imbalance": self.imb.get(key), "oi": self.oi.get(key),
            "funding": row["funding"]})
        direction, score, agree = core.confluence(core.run(CRYPTO_STRATEGIES, x))
        h1 = fr["1h"].close
        h1_trend = 1 if ema(h1, 21).iloc[-1] > ema(h1, 50).iloc[-1] else -1
        if direction:
            score = max(0, min(100, score + (5 if h1_trend == direction else -10)))
        entry = (ask if direction > 0 else bid) or px
        lv = core.levels(direction, entry, agree[0].stop, a5, self.sc) if direction else None
        tech = core.vote(direction, score, agree, x.regime, lv)
        tech.reasons.append(f"1H context {'up' if h1_trend > 0 else 'down'}" + ("" if not direction else
                            " — aligned" if h1_trend == direction else " — against (-10)"))

        btc = None
        if row["base"] != "BTC" and f"{m}:{BTC[m]}" in self.frames:
            btc = btc_context(fr["5m"], self.frames[f"{m}:{BTC[m]}"]["5m"])
        reasons = risk.crypto_checks(lv, bid, ask, self.cfg["fees_pct"][m], self.cfg, btc, direction, self.station_cfg["risk"]["min_rr"])
        others = [s for s in self.open_signals() if s["symbol"] != key]
        today = [s for s in self.book.store.between(risk.day_start_ms(now_ms, self.tz), now_ms + 1) if s["mode"] == "crypto"]
        reasons += risk.limit_checks(today, sum(s["mode"] == "crypto" for s in others), None, self.sc["limits"]["crypto"], now_ms)
        reasons += [HALT_REASON] if halt.active else []
        veto, warn = risk.correlation_guard(key, direction, others, self.sc["max_same_exposure"]) if direction else ([], [])
        d = decide(key, [tech], RiskReport(bool(reasons + veto), reasons + veto, warn),
                   (ask - bid) if bid and ask else None, {**self.station_cfg, "min_confidence": self.sc["min_score"]}, now_ms,
                   mode="crypto", hold_time=f"≤{self.sc['max_hold_minutes']} min", expiry_ms=self.sc["max_hold_minutes"] * 60_000)
        d.update(market=m, display=f"{row['base']} {m}", regime=x.regime, strategies=[s.strategy for s in agree], digits=row["digits"])

        if d["call"] != "HOLD" and key not in self.book.active:
            lvx, dg = d["levels"], row["digits"]
            d.update(entry_until=now_ms + self.sc["expiry_minutes"] * 60_000, time_stop_at=now_ms + self.sc["time_stop_bars"] * 60_000,
                     say=f"Crypto {'buy' if d['call'] == 'BUY' else 'sell'} {row['base']} {m} at {lvx['entry']:.{dg}f}, "
                         f"stop {lvx['stop']:.{dg}f}, target {lvx['tp1']:.{dg}f}.")
            self.book.open(d)
        return {**row, "regime": x.regime, "h1_trend": h1_trend, "call": d["call"], "score": score, "direction": direction,
                "strategies": d["strategies"], "rr": d.get("rr"), "decision": d}

    async def close(self) -> None:
        for ex in self.ex.values():
            await ex.close()
