"""Shared market context the feeds write and the agents read."""
from dataclasses import dataclass, field
from typing import Any

import pandas as pd

QUOTE_MAX_AGE_S = 300  # no tick for 5 minutes: market closed or the stream is down


def live_quote(p: dict[str, Any], now_s: float) -> bool:
    """A bid and ask that arrived recently. Friday's last quote stays in memory all weekend, so having one isn't enough."""
    return bool(p.get("bid") and p.get("ask")) and now_s - p.get("seen", now_s) <= QUOTE_MAX_AGE_S


@dataclass
class Context:
    calendar: list[dict[str, Any]] = field(default_factory=list)       # {title, country, time (ms), impact, forecast, previous}
    yields: dict[str, pd.Series] = field(default_factory=dict)          # FRED id -> daily series
    dxy: pd.DataFrame | None = None                                     # 1H DXY candles (close only needed)
    cot: dict[str, pd.DataFrame] = field(default_factory=dict)          # symbol -> weekly {date, net_pct}
    retail: dict[str, float] = field(default_factory=dict)              # symbol -> retail long %
    news: list[dict[str, Any]] = field(default_factory=list)            # headlines, newest first
    news_view: dict[str, Any] = field(default_factory=dict)             # classifier output
