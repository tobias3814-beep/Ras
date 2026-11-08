"""Stops and targets passed while Ras was closed or the laptop slept. Live checks only see quotes that arrive, so once
candles are back the missed bars are replayed through the same checks, worst price first."""
from collections.abc import Iterator, Sequence
from typing import Any, Callable

import pandas as pd

TFS = ("MINUTE_15", "HOUR", "HOUR_4", "DAY")  # finest first


def bars_since(frames: dict[str, pd.DataFrame], since_ms: int) -> pd.DataFrame | None:
    """Bars that opened during the gap, from the finest timeframe whose history reaches back that far."""
    have = [frames[t] for t in TFS if t in frames and len(frames[t])]
    if not have:
        return None
    df = next((d for d in have if d.time.iloc[0] * 1000 <= since_ms), have[-1])
    return df[df.time * 1000 >= since_ms]


def quotes(df: pd.DataFrame, side: int) -> Iterator[tuple[int, float, float]]:
    """(ms, bid, ask) through each bar: the open first (so a gap fills at the gap), then the side's worst extreme."""
    keys = ("open", "low", "high", "close") if side > 0 else ("open", "high", "low", "close")
    for r in df.to_dict("records"):
        for k in keys:
            bid, ask = r.get(f"bid_{k}"), r.get(f"ask_{k}")
            yield int(r["time"] * 1000), r[k] if pd.isna(bid) else bid, r[k] if pd.isna(ask) else ask


def replay(sym: str, frames: dict[str, pd.DataFrame], since_ms: int, paper: Any,
           desks: Sequence[tuple[dict[str, Any], Callable[[str, float, float, int], Any]]]) -> int:
    """Paper first, so a closed signal can't close its position at today's price instead. Returns positions closed."""
    df = bars_since(frames, since_ms)
    if df is None or df.empty:
        return 0
    n = 0
    for p in [p for p in paper.open.values() if p["symbol"] == sym]:
        for ms, bid, ask in quotes(df[df.time * 1000 >= p["opened"]], p["side"]):
            paper.check(p, bid, ask, ms)
            if p["signal_id"] not in paper.open:
                p["reason"] += " (while Ras was offline)"
                paper.store.save_position(p)
                n += 1
                break
    for active, check in desks:
        if s := active.get(sym):
            for ms, bid, ask in quotes(df[df.time * 1000 >= s["created"]], 1 if s["call"] == "BUY" else -1):
                if active.get(sym) is not s:
                    break
                check(sym, bid, ask, ms)
    return n
