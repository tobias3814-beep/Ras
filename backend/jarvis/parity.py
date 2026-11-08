"""Would the replay have made the signals Ras really made? Each stored live signal is rerun on the candles up to its own time,
through the backtester's code path. A backtest only describes the live desk if they agree, so a mismatch means a bug or a
live-only input (news setups, revised broker candles), never strategy decay."""
from typing import Any, Callable

import numpy as np
import pandas as pd

from jarvis import backtest
from jarvis.trend import score

TF = {"intraday": ("MINUTE_15", 900, 5000), "scalp": ("MINUTE", 60, 6000), "trend": ("DAY", 86_400, 400)}
LOOK_BACK_BARS = 2  # the poller can run a moment after a candle closes, so its signal may belong to the bar before
Loader = Callable[[str, str], pd.DataFrame | None]


def _replay_calls(sig: dict[str, Any], df: pd.DataFrame, i: int, cfg: dict[str, Any]) -> list[str]:
    """What the replay's own logic calls on the last bars up to bar i, ignoring vetoes: live had already passed its own."""
    mode, keep = sig["mode"], TF[sig["mode"]][2]
    window = df.iloc[max(0, i + 1 - keep):i + 1].reset_index(drop=True)
    first = len(window) - LOOK_BACK_BARS
    if mode == "trend":
        s = score(window.close)
        return ["BUY" if s > 0 else "SELL"] if s else []
    if mode == "intraday":
        cands = backtest.intraday_candidates(sig["symbol"], window, cfg, start=first)
    else:
        cands = backtest.scalp_candidates(sig["symbol"], window, cfg, "fx", start=first)
    return ["BUY" if c.direction > 0 else "SELL" for c in cands]


def check(signals: list[dict[str, Any]], load: Loader, cfg: dict[str, Any]) -> dict[str, Any]:
    rows = []
    for s in signals:
        if s["mode"] not in TF:
            continue
        tf, bar_s, keep = TF[s["mode"]]
        row = {"id": s["id"], "symbol": s["symbol"], "mode": s["mode"], "call": s["call"], "created": s["created"]}
        try:
            df = load(s["symbol"], tf)
        except Exception as e:  # one market failing to download doesn't stop the rest
            rows.append({**row, "result": "no data", "why": repr(e)[:120]})
            continue
        if df is None or df.empty:
            rows.append({**row, "result": "no data", "why": "no candles"})
            continue
        i = int(np.searchsorted(df.time.to_numpy() + bar_s, s["created"] / 1000, side="right")) - 1
        needed = min(keep, 300)
        if i + 1 < needed or s["created"] / 1000 - (df.time.iloc[i] + bar_s) > max(3 * bar_s, 600):
            rows.append({**row, "result": "no data", "why": "history doesn't reach this signal"})
            continue
        calls = _replay_calls(s, df, i, cfg)
        rows.append({**row, "result": "match" if s["call"] in calls else "opposite" if calls else "no setup"})
    known = [r for r in rows if r["result"] != "no data"]
    return {"checked": len(known), "matched": sum(r["result"] == "match" for r in known),
            "no_data": len(rows) - len(known), "rows": rows}
