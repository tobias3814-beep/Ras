"""Optional trade filters, shared by the live desks and the backtester so both judge setups the same way:
- trend: only trade with the higher timeframe (intraday: daily; scalp: 1H) — EMA20/EMA50 and price agree;
- chop: skip when the 1H market is choppy (low ADX, low efficiency);
- hours: skip local hours that lost money in-sample (chosen by the backtest, never on the data it's judged on)."""
from collections import defaultdict
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

import pandas as pd

from jarvis.indicators import ema
from jarvis.scalp import core

OFF: dict[str, Any] = {"trend": False, "chop": False, "hours": []}


def trend(df: pd.DataFrame) -> int:
    """+1 up / -1 down / 0 unclear, from the last closed bar of df (needs ~50 bars)."""
    if len(df) < 50:
        return 0
    c, e20, e50 = df.close.iloc[-1], ema(df.close, 20).iloc[-1], ema(df.close, 50).iloc[-1]
    return 1 if c > e50 and e20 > e50 else -1 if c < e50 and e20 < e50 else 0


def choppy(h1: pd.DataFrame) -> bool:
    return len(h1) >= 40 and core.regime(h1) == "chop"


def hour(ts_s: float, tz: str) -> int:
    return datetime.fromtimestamp(ts_s, ZoneInfo(tz)).hour


def reasons(f: dict[str, Any], direction: int, trend_dir: int, chop: bool, hr: int, tf: str) -> list[str]:
    """Why the filters block this setup (empty = allowed)."""
    out = []
    if f.get("trend") and trend_dir != direction:
        out.append(f"filter: {tf} trend {'unclear' if trend_dir == 0 else 'against the trade'}")
    if f.get("chop") and chop:
        out.append("filter: 1H market choppy")
    if hr in (f.get("hours") or []):
        out.append(f"filter: {hr:02d}:00 is a losing hour for this setup")
    return out


def bad_hours(trades: list[dict[str, Any]], tz: str, min_n: int = 4, max_e: float = -0.25) -> list[int]:
    """Hours (local) with at least min_n trades averaging max_e R or worse."""
    by: dict[int, list[float]] = defaultdict(list)
    for t in trades:
        by[hour(t["time"] / 1000, tz)].append(t["r"])
    return sorted(h for h, rs in by.items() if len(rs) >= min_n and sum(rs) / len(rs) <= max_e)
