"""Scalp strategy plugins. FX ones use session context; crypto ones use order book / funding / OI.
Times in frames are candle OPEN times in UTC seconds; extra windows are UTC ms."""
import pandas as pd

from jarvis.indicators import atr, ema
from jarvis.scalp.core import ScalpInput, Setup, Strategy


def _atr(df: pd.DataFrame) -> float:
    return float(atr(df).iloc[-1])


def _between(df: pd.DataFrame, start_ms: float, end_ms: float) -> pd.DataFrame:
    return df[(df.time * 1000 >= start_ms) & (df.time * 1000 < end_ms)]


def _vol_spike(df: pd.DataFrame, x: float) -> bool:
    return bool(df.volume.iloc[-1] > x * df.volume.iloc[-21:-1].mean())


class LondonORB(Strategy):
    """Pre-London range (00:00 London -> London open), break then retest on 1M."""
    name = "london_orb"

    def evaluate(self, x: ScalpInput) -> Setup | None:
        if x.extra.get("session") != "london_open":
            return None
        start = x.extra["windows"]["london_open"][0]
        rng = _between(x.mid, start - 7 * 3_600_000, start)
        if len(rng) < 12:
            return None
        hi, lo, a1, f = rng.high.max(), rng.low.min(), _atr(x.fast), x.fast.iloc[-15:]
        last = f.iloc[-1]
        if (f.close > hi).any() and last.low <= hi + 0.2 * a1 and last.close > hi:
            return Setup(self.name, 1, min(f.low.iloc[-5:].min(), hi - 0.5 * a1), 68, f"broke pre-London high {hi:g}, retest held")
        if (f.close < lo).any() and last.high >= lo - 0.2 * a1 and last.close < lo:
            return Setup(self.name, -1, max(f.high.iloc[-5:].max(), lo + 0.5 * a1), 68, f"broke pre-London low {lo:g}, retest held")
        return None


class AsianSweep(Strategy):
    """Wick beyond the Asian (Tokyo) range at London/NY open that closes back inside -> reverse."""
    name = "asian_sweep"

    def evaluate(self, x: ScalpInput) -> Setup | None:
        if x.extra.get("session") not in ("london_open", "ny_open", "overlap"):
            return None
        rng = _between(x.mid, *x.extra["windows"]["tokyo"])
        if len(rng) < 12 or x.extra["windows"]["tokyo"][1] > x.now_s * 1000:
            return None
        hi, lo, c, a5 = rng.high.max(), rng.low.min(), x.mid.iloc[-1], _atr(x.mid)
        if c.high > hi and c.close < hi:
            return Setup(self.name, -1, c.high + 0.2 * a5, 66, f"swept Asian high {hi:g} and closed back inside")
        if c.low < lo and c.close > lo:
            return Setup(self.name, 1, c.low - 0.2 * a5, 66, f"swept Asian low {lo:g} and closed back inside")
        return None


class VWAPReclaim(Strategy):
    """1M cross of the anchored VWAP with a volume spike (tick volume for FX)."""
    name = "vwap"

    def __init__(self, market: str) -> None:
        self.market = market

    def evaluate(self, x: ScalpInput) -> Setup | None:
        df = x.fast[x.fast.time >= x.extra["vwap_anchor_s"]]
        if len(df) < 10 or df.volume.sum() <= 0:
            return None
        tp = (df.high + df.low + df.close) / 3
        vw = (tp * df.volume).cumsum() / df.volume.cumsum().replace(0, float("nan"))
        p, c = df.iloc[-2], df.iloc[-1]
        if not _vol_spike(x.fast, 1.5):
            return None
        if p.close < vw.iloc[-2] and c.close > vw.iloc[-1]:
            return Setup(self.name, 1, df.low.iloc[-3:].min(), 62, f"reclaimed VWAP {vw.iloc[-1]:g} on volume")
        if p.close > vw.iloc[-2] and c.close < vw.iloc[-1]:
            return Setup(self.name, -1, df.high.iloc[-3:].max(), 62, f"rejected/lost VWAP {vw.iloc[-1]:g} on volume")
        return None


class EMAPullback(Strategy):
    """15M trend; 1M pullback into EMA21 (EMA9/21, or 9/21/50 ribbon) that resumes above EMA9."""
    regimes = frozenset({"trend"})

    def __init__(self, name: str = "ema_pullback", market: str = "fx", ribbon: int | None = None) -> None:
        self.name, self.market, self.ribbon = name, market, ribbon

    def evaluate(self, x: ScalpInput) -> Setup | None:
        b = x.bias.close
        trend = 1 if ema(b, 21).iloc[-1] > ema(b, 50).iloc[-1] and b.iloc[-1] > ema(b, 21).iloc[-1] else \
            -1 if ema(b, 21).iloc[-1] < ema(b, 50).iloc[-1] and b.iloc[-1] < ema(b, 21).iloc[-1] else 0
        if not trend:
            return None
        f = x.fast
        e9, e21 = ema(f.close, 9), ema(f.close, 21)
        stack = [e9.iloc[-1], e21.iloc[-1]] + ([ema(f.close, self.ribbon).iloc[-1]] if self.ribbon else [])
        aligned = all(trend * (a - b) > 0 for a, b in zip(stack, stack[1:]))
        a1, recent = _atr(f), f.iloc[-5:]
        touched = (recent.low <= e21.iloc[-5:] + 0.1 * a1).any() if trend > 0 else (recent.high >= e21.iloc[-5:] - 0.1 * a1).any()
        resumed = trend * (f.close.iloc[-1] - e9.iloc[-1]) > 0
        if not (aligned and touched and resumed):
            return None
        stop = recent.low.min() - 0.1 * a1 if trend > 0 else recent.high.max() + 0.1 * a1
        label = "ribbon 9/21/50" if self.ribbon else "EMA 9/21"
        return Setup(self.name, trend, stop, 64, f"15M trend {'up' if trend > 0 else 'down'}, 1M {label} pullback resumed")


class RoundNumber(Strategy):
    """Rejection wick at a round number (majors 00, gold 00/50, ...)."""
    name = "round_number"

    def evaluate(self, x: ScalpInput) -> Setup | None:
        step = x.extra.get("round")
        if not step:
            return None
        c, a1 = x.fast.iloc[-1], _atr(x.fast)
        level = round(c.close / step) * step
        rng = c.high - c.low
        if rng <= 0:
            return None
        slope = ema(x.mid.close, 21).diff().iloc[-1]
        if c.low <= level + 0.1 * a1 and c.close > level and (min(c.open, c.close) - c.low) >= 0.5 * rng:
            return Setup(self.name, 1, c.low - 0.2 * a1, 55 + (10 if slope > 0 else 0), f"rejected round number {level:g} from above")
        if c.high >= level - 0.1 * a1 and c.close < level and (c.high - max(c.open, c.close)) >= 0.5 * rng:
            return Setup(self.name, -1, c.high + 0.2 * a1, 55 + (10 if slope < 0 else 0), f"rejected round number {level:g} from below")
        return None


class PostNews(Strategy):
    """After a high-impact release: first clean 5M close outside the release range, once the blackout is over."""
    name = "post_news"
    regimes = frozenset({"trend", "range", "chop"})

    def __init__(self, name: str = "post_news", title: str = "", range_from: float = -30, range_to: float = 20,
                 active_for: float = 30, wednesday: bool = False) -> None:
        self.name, self.title, self.range_from, self.range_to = name, title, range_from, range_to
        self.active_for, self.wednesday = active_for, wednesday

    def _events(self, x: ScalpInput) -> list[dict]:
        if self.title:
            return [e for e in x.extra.get("all_events", []) if self.title in e["title"].lower()]
        return x.extra.get("events", [])

    def evaluate(self, x: ScalpInput) -> Setup | None:
        now_ms = x.now_s * 1000
        for e in self._events(x):
            start, end = e["time"] + self.range_from * 60_000, e["time"] + self.range_to * 60_000
            if self.wednesday and pd.Timestamp(e["time"], unit="ms", tz="America/New_York").weekday() != 2:
                continue
            if not end <= now_ms <= end + self.active_for * 60_000:
                continue
            rng, after = _between(x.mid, start, end), x.mid[x.mid.time * 1000 >= end]
            if len(rng) < 3 or after.empty:
                return None
            hi, lo = rng.high.max(), rng.low.min()
            inside_before = ((after.close.iloc[:-1] <= hi) & (after.close.iloc[:-1] >= lo)).all()
            c = after.close.iloc[-1]
            if inside_before and c > hi:
                return Setup(self.name, 1, (hi + lo) / 2, 66, f"first clean 5M break above the {e['title']} range")
            if inside_before and c < lo:
                return Setup(self.name, -1, (hi + lo) / 2, 66, f"first clean 5M break below the {e['title']} range")
        return None


class OilEIA(PostNews):
    """WTI only, Wednesdays: trade the break of the first 20 min after the EIA release, once the dust settles."""

    def __init__(self) -> None:
        super().__init__("oil_eia", title="crude oil inventories", range_from=0, range_to=20, active_for=40, wednesday=True)

    def evaluate(self, x: ScalpInput) -> Setup | None:
        return super().evaluate(x) if x.symbol == "WTI_USD" else None


# ---------- crypto ----------

class LiquiditySweep(Strategy):
    """5M wick past the recent swing high/low that closes back inside -> reverse."""
    name, market, regimes = "liquidity_sweep", "crypto", frozenset({"range", "trend"})

    def evaluate(self, x: ScalpInput) -> Setup | None:
        m = x.mid
        hi, lo, c, a5 = m.high.iloc[-22:-2].max(), m.low.iloc[-22:-2].min(), m.iloc[-1], _atr(m)
        if c.high > hi and c.close < hi:
            return Setup(self.name, -1, c.high + 0.1 * a5, 63, f"swept swing high {hi:g}, closed back inside")
        if c.low < lo and c.close > lo:
            return Setup(self.name, 1, c.low - 0.1 * a5, 63, f"swept swing low {lo:g}, closed back inside")
        return None


class SqueezeBreakout(Strategy):
    """5M Bollinger squeeze (width in the lowest 20% of 100 bars) then a close outside the band on 2x volume."""
    name, market, regimes = "squeeze_breakout", "crypto", frozenset({"range", "chop"})

    def evaluate(self, x: ScalpInput) -> Setup | None:
        c = x.mid.close
        mid, sd = c.rolling(20).mean(), c.rolling(20).std()
        width = (4 * sd / mid).dropna()
        if len(width) < 100 or width.iloc[-2] > width.iloc[-100:].quantile(0.2) or not _vol_spike(x.mid, 2.0):
            return None
        up, dn = mid.iloc[-1] + 2 * sd.iloc[-1], mid.iloc[-1] - 2 * sd.iloc[-1]
        if c.iloc[-1] > up:
            return Setup(self.name, 1, mid.iloc[-1], 66, "Bollinger squeeze broke up on 2x volume")
        if c.iloc[-1] < dn:
            return Setup(self.name, -1, mid.iloc[-1], 66, "Bollinger squeeze broke down on 2x volume")
        return None


class OrderBookImbalance(Strategy):
    """Resting size within 0.1% of mid leaning one way, confirmed by 1M close vs EMA9."""
    name, market = "orderbook", "crypto"

    def evaluate(self, x: ScalpInput) -> Setup | None:
        imb = x.extra.get("imbalance")
        if imb is None or abs(imb) < 0.35:
            return None
        f, d = x.fast, 1 if imb > 0 else -1
        if d * (f.close.iloc[-1] - ema(f.close, 9).iloc[-1]) <= 0:
            return None
        stop = f.low.iloc[-3:].min() if d > 0 else f.high.iloc[-3:].max()
        return Setup(self.name, d, stop, int(45 + 25 * min(1.0, abs(imb))), f"order book {imb:+.0%} imbalance near price")


class FundingOI(Strategy):
    """Perps: price moved 1h but open interest fell >1.5% (move not backed by new positions) -> fade; extreme funding adds."""
    name, market = "funding_oi", "crypto"

    def evaluate(self, x: ScalpInput) -> Setup | None:
        oi, fr = x.extra.get("oi"), x.extra.get("funding")
        if oi is None or len(oi) < 13 or len(x.mid) < 13:
            return None
        px_chg = x.mid.close.iloc[-1] / x.mid.close.iloc[-13] - 1
        oi_chg = oi.iloc[-1] / oi.iloc[-13] - 1
        if oi_chg > -0.015 or abs(px_chg) < 0.003:
            return None
        d = -1 if px_chg > 0 else 1
        score = 55 + (15 if fr is not None and -d * fr > 0.0003 else 0)
        stop = x.mid.high.iloc[-12:].max() if d < 0 else x.mid.low.iloc[-12:].min()
        why = f"price {px_chg:+.1%} 1h while OI {oi_chg:+.1%}" + (f", funding {fr:+.3%}" if fr is not None else "")
        return Setup(self.name, d, stop, score, why)


FX_STRATEGIES: list[Strategy] = [LondonORB(), AsianSweep(), VWAPReclaim("fx"), EMAPullback(), RoundNumber(), PostNews(), OilEIA()]
CRYPTO_STRATEGIES: list[Strategy] = [VWAPReclaim("crypto"), EMAPullback("ema_ribbon", "crypto", ribbon=50), LiquiditySweep(),
                                     SqueezeBreakout(), OrderBookImbalance(), FundingOI()]
