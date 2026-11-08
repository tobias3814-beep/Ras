"""Live short-term analysis for a market room: run every Strategies-tab rule on the latest closed candles, report each
one's current setup, and make one decision that only listens to rules with a positive tested record.

Each rule uses the settings its latest Strategies-tab run picked for this timeframe (last walk-forward fold), or its
first setting if it has never been run. A setup is shown while it is still live: a limit order not yet filled or
expired, or a position whose stop / target / time limit hasn't been reached on the bars since. Analysis only: nothing
here opens a signal or a paper trade."""
from typing import Any

import numpy as np
import pandas as pd

from jarvis.lab import rules
from jarvis.indicators import ema
from jarvis.lab.rules import CATALOG, Setup

TF_S = {"MINUTE_3": 180, "MINUTE_15": 900, "HOUR": 3600, "HOUR_4": 14_400, "DAY": 86_400}
FRESH_BARS = 12            # look this many bars back for setups that may still be live
TRUSTED = {"promising", "passes screen", "survives checks"}    # verdicts whose setups count toward the decision
MIN_TRUST_R = 0.05         # ... or a "marginal" rule with at least this out-of-sample average R
LOCAL_TRADES, LOCAL_R = 100, 0.10  # ... or this many out-of-sample trades on this market at this average R or better


def _parse(label: str) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for part in filter(None, (x.strip() for x in label.split(","))):
        k, v = part.split("=", 1)
        out[k] = None if v == "exit rule" else True if v == "True" else False if v == "False" else \
            float(v) if v.replace(".", "", 1).lstrip("-").isdigit() and "." in v else int(v) if v.lstrip("-").isdigit() else v
    return out


def evidence(runs: list[dict[str, Any]], key: str, symbol: str, tf: str) -> dict[str, Any] | None:
    """The finished Strategies run on this timeframe with the most out-of-sample evidence for this rule: runs that
    included this market first (most trades on it), else the run with the most trades overall. Returns its verdict,
    out-of-sample stats overall and for this market, and the settings its last fold picked."""
    found = []
    for r in runs:
        res = r.get("result") or {}
        if r.get("status") != "done" or r["spec"]["tf"] != tf or key not in r["spec"]["strategies"]:
            continue
        row = next((s for s in res.get("strategies", []) if s["key"] == key), None)
        if row is None:
            continue
        mine = row["by_symbol"].get(symbol)
        found.append(({"run": r["id"], "verdict": row["verdict"], "summary": row["summary"], "this_market": mine,
                       "markets": r["spec"]["symbols"], "days": r["spec"]["days"],
                       "params": row["folds"][-1]["params"] if row["folds"] else ""},
                      (mine or {}).get("trades", 0), row["summary"]["trades"], r["id"]))
    if not found:
        return None
    return max(found, key=lambda x: (x[1], x[2], x[3]))[0]


def basis(ev: dict[str, Any] | None) -> str | None:
    """Why a rule's setups count toward the decision, or None when they don't:
    "tested" - its run's verdict passed (promising, or marginal at >= MIN_TRUST_R) and it didn't lose on this market;
    "this market only" - it failed across the run's markets but made >= LOCAL_R over >= LOCAL_TRADES trades on this one
    (weaker evidence: it may only be riding this market's own trend)."""
    if not ev:
        return None
    e = ev["summary"].get("expectancy") or 0
    mine = ev.get("this_market") or {}
    if (ev["verdict"] in TRUSTED or (ev["verdict"] == "marginal" and e >= MIN_TRUST_R)) and (mine.get("expectancy") is None or mine["expectancy"] > 0):
        return "tested"
    if mine.get("trades", 0) >= LOCAL_TRADES and (mine.get("expectancy") or 0) >= LOCAL_R:
        return "this market only"
    return None


def trusted(ev: dict[str, Any] | None) -> bool:
    return basis(ev) is not None


def _status(s: Setup, df: pd.DataFrame, exit_long: np.ndarray | None, exit_short: np.ndarray | None) -> tuple[str, int | None]:
    """Where a setup stands on the bars after it: (status, fill bar)."""
    h, lo, n = df.high.to_numpy(), df.low.to_numpy(), len(df)
    j = None
    if s.entry is None:
        j = s.i + 1 if s.i + 1 < n else None
        if j is None:
            return "fresh: enter at the next open", None
    else:
        for k in range(s.i + 1, min(n, s.i + 1 + max(1, s.expiry))):
            if ((h[k] >= s.entry) if s.side > 0 else (lo[k] <= s.entry)) if s.stop_entry else ((lo[k] <= s.entry) if s.side > 0 else (h[k] >= s.entry)):
                j = k
                break
        if j is None:
            return ("limit expired" if n - 1 - s.i >= max(1, s.expiry) else "limit waiting"), None
    xs = exit_long if s.side > 0 else exit_short
    for k in range(j, n):
        if (lo[k] <= s.stop) if s.side > 0 else (h[k] >= s.stop):
            return "stopped out", j
        if s.target is not None and ((h[k] >= s.target) if s.side > 0 else (lo[k] <= s.target)):
            return "target hit", j
        if xs is not None and xs[k]:
            return "closed by its exit rule", j
        if s.max_bars and k - j + 1 >= s.max_bars:
            return "timed out", j
    return "in trade", j


def analyse(symbol: str, tf: str, df: pd.DataFrame, runs: list[dict[str, Any]], trend_call: str | None,
            price: float | None = None) -> dict[str, Any]:
    """Every rule's current setup on `df` (closed bars, newest last) plus one decision. trend_call: the trend desk's
    daily direction (BUY / SELL / HOLD / None); short-term trades against it are not taken."""
    bar_s = TF_S[tf]
    rows = []
    for key, meta in CATALOG.items():
        if key == "trend_following" or meta["group"] in rules.RESEARCH_GROUPS:
            continue  # a daily rule the trend desk runs live, or a research rule shown on its own page
        ev = evidence(runs, key, symbol, tf)
        p = {**rules.combos(key)[0], **(_parse(ev["params"]) if ev and ev["params"] else {})}
        plan = meta["fn"](df, {**p, "_bar_s": bar_s})
        recent = [s for s in plan.setups if s.i >= len(df) - 1 - FRESH_BARS]
        row: dict[str, Any] = {"key": key, "name": meta["name"], "group": meta["group"], "params": rules.label(p),
                               "evidence": ev, "trusted": trusted(ev), "basis": basis(ev), "setup": None}
        if recent:
            s = recent[-1]
            status, j = _status(s, df, plan.exit_long, plan.exit_short)
            live = status in ("fresh: enter at the next open", "limit waiting", "in trade")
            entry = s.entry if s.entry is not None else (float(df.close.iat[s.i]))
            row["setup"] = {"call": "BUY" if s.side > 0 else "SELL", "entry": entry, "stop": s.stop, "target": s.target,
                            "bars_ago": len(df) - 1 - s.i, "time": int(df.time.iat[s.i]), "status": status, "live": live,
                            "kind": ("stop" if s.stop_entry else "limit") if s.entry is not None else "market", "tag": s.tag}
        rows.append(row)
    return {"symbol": symbol, "tf": tf, "bar_time": int(df.time.iat[-1]), "price": price, "trend": trend_call,
            "rules": rows, "decision": decide(rows, trend_call)}


def decide(rows: list[dict[str, Any]], trend_call: str | None) -> dict[str, Any]:
    """BUY / SELL only when a trusted rule has a live setup, no trusted rule has a live setup the other way, and it
    goes with the daily trend. Otherwise NO TRADE, with the reason."""
    live = [r for r in rows if r["setup"] and r["setup"]["live"]]
    good = [r for r in live if r["trusted"]]
    sides = {r["setup"]["call"] for r in good}
    untrusted = [r["name"] for r in live if not r["trusted"]]
    note = f" Ignored (no positive tested record here): {', '.join(untrusted)}." if untrusted else ""
    if not any(r["trusted"] for r in rows):
        return {"call": "NO TRADE", "why": "None of the short-term rules has a positive tested record on this timeframe "
                                          "and market. Run them in the Strategies tab; only rules that pass count." + note,
                "rules": []}
    if not good:
        return {"call": "NO TRADE", "why": "No trusted rule has a live setup right now." + note, "rules": []}
    if len(sides) > 1:
        return {"call": "NO TRADE", "why": "Trusted rules disagree: " + ", ".join(f"{r['name']} {r['setup']['call']}" for r in good) + ".",
                "rules": [r["key"] for r in good]}
    side = sides.pop()
    if trend_call in ("BUY", "SELL") and trend_call != side:
        return {"call": "NO TRADE", "why": f"{', '.join(r['name'] for r in good)} say{'s' if len(good) == 1 else ''} {side}, "
                                          f"but the daily trend is {trend_call}: short-term trades against it are skipped.",
                "rules": [r["key"] for r in good]}
    best = max(good, key=lambda r: r["evidence"]["summary"].get("expectancy") or 0)
    s = best["setup"]
    return {"call": side, "rules": [r["key"] for r in good], "entry": s["entry"], "stop": s["stop"], "target": s["target"],
            "kind": s["kind"], "status": s["status"],
            "why": f"{', '.join(r['name'] for r in good)}: live {side} setup, with the daily trend "
                   f"({trend_call or 'unknown'}). Levels from {best['name']} "
                   f"(tested {best['evidence']['summary'].get('expectancy'):+.2f}R over {best['evidence']['summary']['trades']} trades)."}


# ---------- one strategy on its own (Strategies tab live desks) ----------

def overlays(key: str, df: pd.DataFrame, p: dict[str, Any]) -> dict[str, pd.Series]:
    """The lines a strategy reads, to draw on its chart."""
    c = df.close
    if key == "ema_cross":
        return {f"EMA {p['fast']}": ema(c, p["fast"]), f"EMA {p['slow']}": ema(c, p["slow"])}
    if key in ("macd_trend", "rsi_pullback", "icc"):
        return {"EMA 200": ema(c, 200)} if key != "icc" or p.get("trend_filter") else {}
    if key == "bollinger_reversion":
        mid, sd = c.rolling(20).mean(), c.rolling(20).std()
        return {"Band mid": mid, "Band upper": mid + p["k"] * sd, "Band lower": mid - p["k"] * sd}
    if key == "donchian_breakout":
        return {f"{p['entry']}-bar high": df.high.rolling(p["entry"]).max().shift(),
                f"{p['entry']}-bar low": df.low.rolling(p["entry"]).min().shift()}
    return {}


def single(key: str, symbol: str, tf: str, df: pd.DataFrame, runs: list[dict[str, Any]], bars: int = 200) -> dict[str, Any]:
    """One strategy deciding on its own: BUY / SELL while it has a live setup (fresh, limit working, or in a trade),
    HOLD otherwise. Returns the candles, its lines, every setup it made on them, and its tested record for context."""
    meta = CATALOG[key]
    ev = evidence(runs, key, symbol, tf)
    p = {**rules.combos(key)[0], **(_parse(ev["params"]) if ev and ev["params"] else {})}
    plan = meta["fn"](df, {**p, "_bar_s": TF_S[tf]})
    first = max(0, len(df) - bars)
    view = df.iloc[first:]
    marks = []
    for s in plan.setups:
        if s.i < first:
            continue
        status, j = _status(s, df, plan.exit_long, plan.exit_short)
        marks.append({"time": int(df.time.iat[s.i]), "call": "BUY" if s.side > 0 else "SELL", "status": status,
                      "entry": s.entry if s.entry is not None else float(df.close.iat[s.i]), "stop": s.stop,
                      "target": s.target, "kind": "limit" if s.entry is not None else "market", "tag": s.tag,
                      "live": status in ("fresh: enter at the next open", "limit waiting", "in trade")})
    cur = next((m for m in reversed(marks) if m["live"]), None)
    if cur:
        verb = {"fresh: enter at the next open": "enter now", "limit waiting": "limit order working", "in trade": "in the trade"}[cur["status"]]
        decision = {"call": cur["call"], "why": f"{meta['name']} has a live {cur['call']} setup: {verb}.", **{k: cur[k] for k in ("entry", "stop", "target", "kind", "status")}}
    elif key == "trend_following" and tf == "DAY" and (sc := _trend_score(df)):
        # its trades last months, so the entry is often older than the candles here: read the trend itself
        side = "BUY" if sc > 0 else "SELL"
        decision = {"call": side, "why": f"Daily trend score {sc:+.2f}: holding {side} until it flips (same rule as the trend desk).",
                    "status": "in trade", "entry": None, "stop": None, "target": None, "kind": "market"}
    else:
        last = marks[-1] if marks else None
        decision = {"call": "HOLD", "why": "No live setup" + (f" — its last {last['call']} {last['status']}." if last else " on these candles.")}
    lines = {name: [[int(t), round(float(v), 6)] for t, v in zip(view.time, ser.iloc[first:]) if pd.notna(v)]
             for name, ser in overlays(key, df, p).items()}
    return {"key": key, "name": meta["name"], "group": meta["group"], "rules": meta["rules"], "symbol": symbol, "tf": tf,
            "params": rules.label(p), "evidence": ev, "basis": basis(ev), "decision": decision,
            "candles": [{"time": int(r.time), "open": float(r.open), "high": float(r.high), "low": float(r.low), "close": float(r.close)}
                        for r in view.itertuples()],
            "lines": lines, "marks": marks, "bar_time": int(df.time.iat[-1])}


def _trend_score(df: pd.DataFrame) -> float:
    from jarvis.trend import score  # the live trend desk's own reading
    return score(df.close) or 0.0
