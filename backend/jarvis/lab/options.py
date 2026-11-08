"""Price a rule's test trades as bought options instead of CFDs: each trade buys an at-the-money call (BUY) or put (SELL)
expiring that Friday and sells it at the trade's exit. Capital.com has no option prices, so this is a model, not
quotes: Black-Scholes with volatility from the market's own last 20 daily moves (x1.15, as options usually cost a bit
more than realised moves), a spread paid on the way in and out, and no skew or volatility changes during the trade."""
import math
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

NY = ZoneInfo("America/New_York")
RATE = 0.04
VOL_MARKUP = 1.15
HALF_SPREAD = 0.015  # of the option's price, each way
YEAR_S = 365 * 86_400


def _n(x: float) -> float:
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def bs(s: float, k: float, t: float, vol: float, call: bool) -> float:
    t = max(t, 60 / YEAR_S)
    d1 = (math.log(s / k) + (RATE + vol * vol / 2) * t) / (vol * math.sqrt(t))
    d2 = d1 - vol * math.sqrt(t)
    return s * _n(d1) - k * math.exp(-RATE * t) * _n(d2) if call else k * math.exp(-RATE * t) * _n(-d2) - s * _n(-d1)


def expiry(ms: int) -> datetime:
    """The Friday 16:00 New York expiry at or after this time (next week's if this week's has passed)."""
    t = datetime.fromtimestamp(ms / 1000, NY)
    fri = (t + timedelta(days=(4 - t.weekday()) % 7)).replace(hour=16, minute=0, second=0, microsecond=0)
    return fri if fri > t else fri + timedelta(days=7)


def daily_vol(day: pd.DataFrame) -> pd.Series:
    """Yesterday's 20-day realised volatility (annualised) for each date: no peeking at the trade's own day."""
    d = day.assign(date=pd.to_datetime(day.time, unit="s", utc=True).dt.date).groupby("date").close.last()
    return (np.log(d).diff().rolling(20).std() * math.sqrt(252) * VOL_MARKUP).shift(1).dropna()


def one(t: dict[str, Any], vol: float, vol_x: float = 1.0) -> float:
    """Return on the premium paid for one trade."""
    exp, call, v = expiry(t["time"]), t["call"] == "BUY", vol * vol_x
    left = lambda ms: (exp - datetime.fromtimestamp(ms / 1000, NY)).total_seconds() / YEAR_S  # noqa: E731
    paid = bs(t["entry"], t["entry"], left(t["time"]), v, call) * (1 + HALF_SPREAD)
    got = bs(t["exit"], t["entry"], left(min(t["closed_at"], int(exp.timestamp() * 1000))), v, call) * (1 - HALF_SPREAD)
    return (got - paid) / paid if paid > 0 else 0.0


def evaluate(trades: list[dict[str, Any]], vols: dict[str, pd.Series], stake: float = 100.0) -> dict[str, Any] | None:
    rows = []
    for t in trades:
        s = vols.get(t["symbol"])
        d = datetime.fromtimestamp(t["time"] / 1000, NY).date()
        if s is None or not len(s):
            continue
        v = s.asof(d) if d >= s.index[0] else np.nan
        if v > 0:
            rows.append((t, float(v)))
    if not rows:
        return None
    base = np.array([one(t, v) for t, v in rows])
    top = np.sort(base)[::-1]

    def avg(x: np.ndarray) -> float:
        return round(float(x.mean()) * 100, 1) if len(x) else 0.0
    by: dict[str, list[float]] = {}
    for (t, _), r in zip(rows, base):
        by.setdefault(t["symbol"], []).append(r)
    return {"trades": len(base), "win_rate": round(float((base > 0).mean()) * 100, 1), "avg_pct": avg(base),
            "median_pct": round(float(np.median(base)) * 100, 1), "without_best_10": avg(top[10:]),
            "stake": stake, "profit": round(float(base.sum()) * stake, 2),
            "pricier": {f"x{x}": avg(np.array([one(t, v, x) for t, v in rows])) for x in (1.2, 1.4)},
            "by_symbol": {k: {"avg_pct": avg(np.array(v)), "trades": len(v)} for k, v in by.items()},
            "model": "Black-Scholes, ATM, Friday expiry, 20-day realised vol x1.15, 1.5% of the premium paid each way"}
