"""Performance analytics over closed trades (live signals, paper positions or backtest trades). R = initial risk."""
from collections import defaultdict
from datetime import datetime
from typing import Any

import numpy as np
from zoneinfo import ZoneInfo

Trade = dict[str, Any]  # needs: r; optional: time (ms), closed_at, symbol, mode, session, strategies, confidence, votes, pnl


def stats(rs: list[float]) -> dict[str, Any]:
    if not rs:
        return {"trades": 0, "wins": 0, "win_rate": None, "profit_factor": None, "avg_r": None, "expectancy": None,
                "total_r": 0.0, "max_dd_r": 0.0, "avg_win": None, "avg_loss": None, "sd": None}
    wins, losses = [r for r in rs if r > 0], [r for r in rs if r <= 0]
    eq, peak, dd = 0.0, 0.0, 0.0
    for r in rs:
        eq += r
        peak = max(peak, eq)
        dd = min(dd, eq - peak)
    gross_loss = -sum(losses)
    return {"trades": len(rs), "wins": len(wins), "win_rate": round(len(wins) / len(rs) * 100, 1),
            "profit_factor": round(sum(wins) / gross_loss, 2) if gross_loss > 0 else None,
            "avg_r": round(sum(rs) / len(rs), 3), "expectancy": round(sum(rs) / len(rs), 3), "total_r": round(sum(rs), 2),
            "max_dd_r": round(dd, 2), "avg_win": round(sum(wins) / len(wins), 2) if wins else None,
            "avg_loss": round(sum(losses) / len(losses), 2) if losses else None,
            "sd": round(float(np.std(rs, ddof=1)), 3) if len(rs) > 1 else None}


def group(trades: list[Trade], key: str) -> dict[str, dict[str, Any]]:
    buckets: dict[str, list[float]] = defaultdict(list)
    for t in trades:
        v = t.get(key)
        for k in (v if isinstance(v, list) else [v]):  # a trade counts once for each strategy it used
            buckets[str(k)].append(t["r"])
    return {k: stats(v) for k, v in sorted(buckets.items())}


def hours(trades: list[Trade], tz: str) -> dict[str, dict[str, Any]]:
    z = ZoneInfo(tz)
    buckets: dict[int, list[float]] = defaultdict(list)
    for t in trades:
        buckets[datetime.fromtimestamp(t["time"] / 1000, z).hour].append(t["r"])
    return {f"{h:02d}": stats(buckets[h]) for h in sorted(buckets)}


def equity(trades: list[Trade]) -> list[dict[str, float]]:
    eq, money, out = 0.0, 0.0, []
    for t in sorted(trades, key=lambda t: t.get("closed_at") or t["time"]):
        eq += t["r"]
        money += t.get("pnl") or 0.0
        out.append({"time": int((t.get("closed_at") or t["time"]) / 1000), "r": round(eq, 3), "pnl": round(money, 2)})
    return out


def calibration(trades: list[Trade], key: str = "confidence") -> list[dict[str, Any]]:
    """Does a 70 actually win ~70%? Buckets of 10 points; 'expected' is the bucket midpoint."""
    buckets: dict[int, list[float]] = defaultdict(list)
    for t in trades:
        if t.get(key) is not None:
            buckets[min(90, int(t[key]) // 10 * 10)].append(t["r"])
    return [{"bucket": f"{b}-{b + 9}", "expected": b + 5, **stats(rs)} for b, rs in sorted(buckets.items())]


def agents(trades: list[Trade]) -> dict[str, dict[str, Any]]:
    """Per-agent accuracy: right = voted with the call and it won, or against it and it lost. HOLD votes aren't graded."""
    out: dict[str, dict[str, int]] = defaultdict(lambda: {"graded": 0, "right": 0, "with_call": 0})
    for t in trades:
        side = 1 if t.get("call") == "BUY" else -1
        for v in t.get("votes") or []:
            if v["direction"] == "HOLD":
                continue
            with_call = (1 if v["direction"] == "BUY" else -1) == side
            a = out[v["agent"]]
            a["graded"] += 1
            a["with_call"] += with_call
            a["right"] += with_call == (t["r"] > 0)
    return {k: {**v, "accuracy": round(v["right"] / v["graded"] * 100, 1)} for k, v in out.items()}


def report(trades: list[Trade], tz: str) -> dict[str, Any]:
    ts = sorted(trades, key=lambda t: t["time"])
    by_hour = hours(ts, tz)
    ranked = sorted(((h, s) for h, s in by_hour.items() if s["trades"] >= 3), key=lambda hs: hs[1]["expectancy"])
    k = min(3, len(ranked) // 2)  # never list the same hour as both best and worst
    return {"summary": stats([t["r"] for t in ts]), "equity": equity(ts),
            "by_symbol": group(ts, "symbol"), "by_mode": group(ts, "mode"), "by_strategy": group(ts, "strategies"),
            "by_session": group(ts, "session"), "by_hour": by_hour,
            "best_hours": [h for h, _ in ranked[::-1][:k]], "worst_hours": [h for h, _ in ranked[:k]],
            "calibration": calibration(ts), "agents": agents(ts)}


def from_signals(signals: list[dict[str, Any]]) -> list[Trade]:
    """Closed signals -> trades (expired with no R are skipped)."""
    return [{**s, "r": s["result_r"], "time": s["created"], "strategies": s.get("strategies") or [s["mode"]],
             "session": s.get("session") or ("24h" if s["mode"] == "crypto" else s["mode"])}
            for s in signals if s["status"] != "active" and s.get("result_r") is not None]


ALARM_MIN_TRADES = 10
SD_ALARM_X = 2.0
SLIP_ALARM_R = 0.1


def count_alarm(n: int, expected: float | None) -> str:
    """Too many signals usually means a bug, too few a desk that has gone quiet. The 3 sigma range treats signals as
    independent, which they aren't quite, so it only speaks up when the gap is large."""
    if expected is None:
        return ""
    if expected >= 10 and n < expected - 3 * expected ** 0.5:
        return f"Only {n} signals where the backtest's pace suggests about {expected:.0f}"
    if n >= 5 and n > expected + 3 * max(expected, 1.0) ** 0.5:
        return f"{n} signals where the backtest's pace suggests about {expected:.0f}"
    return ""


def alarms(live: dict[str, Any], bt: dict[str, Any] | None, slips: list[float], signals: int = 0,
           expected: float | None = None) -> list[str]:
    """Risk behaves far more predictably than returns from backtest to live (Wiecki et al.), so it shows trouble long before
    the average does. Limits are set here, in advance, against the backtest's own range."""
    out = []
    if bt and bt.get("drawdown_bad") and -live["max_dd_r"] > bt["drawdown_bad"]:
        out.append(f"Losing run of {-live['max_dd_r']:.1f}R is past the backtest's bad case of {bt['drawdown_bad']:.1f}R")
    if bt and bt.get("sd") and live["trades"] >= ALARM_MIN_TRADES and live["sd"] and live["sd"] > SD_ALARM_X * bt["sd"]:
        out.append(f"Results swing {live['sd'] / bt['sd']:.1f}x as much as the backtest's")
    if len(slips) >= ALARM_MIN_TRADES and (avg := sum(slips) / len(slips)) > SLIP_ALARM_R:
        out.append(f"Entries are costing {avg:.2f}R of slippage on average")
    if say := count_alarm(signals, expected):
        out.append(say)
    return out


def forward_test(live: list[Trade], backtests: list[dict[str, Any]], target: int = 50, min_trades: int = 20,
                 window_days: float | None = None, signals: dict[tuple[str, str], int] | None = None,
                 enabled: list[tuple[str, str]] | None = None) -> dict[str, Any]:
    """Live paper trades (net R, from real fills) per mode + instrument against the latest backtest's out-of-sample
    expectancy, which is also net. A verdict needs the noise range around the live average (+/- 2 standard errors) to
    clear the comparison: collecting / can't tell yet / on track (clearly above zero) / below backtest / losing.
    window_days: time Ras was online. signals: count per desk, including what the account skipped; enabled: desks that
    get a row even with no trades, because a desk that has gone silent is what the count alarm is for."""
    def noise(rs: list[float]) -> list[float] | None:
        if len(rs) < 2:
            return None
        e, se = float(np.mean(rs)), float(np.std(rs, ddof=1)) / len(rs) ** 0.5
        return [round(e - 2 * se, 3), round(e + 2 * se, 3)]

    def verdict(rs: list[float], bt: float | None) -> str:
        rng = noise(rs)
        if len(rs) < min_trades or rng is None:
            return "collecting"
        if rng[1] < 0:
            return "losing"
        if bt is not None and rng[1] < bt:
            return "below backtest"
        return "on track" if rng[0] > 0 else "can't tell yet"

    groups: dict[tuple[str, str], list[float]] = defaultdict(list)
    for key in enabled or []:
        groups[key]
    for t in live:
        groups[(t["mode"], t["symbol"])].append(t["r"])
    rows = []
    for (mode, sym), rs in sorted(groups.items()):
        st = stats(rs)
        ref = next(((b, b["result"]["out_of_sample"]["by_symbol"][sym]) for b in backtests
                    if b["spec"]["mode"] == mode and (b.get("result") or {}).get("out_of_sample", {}).get("by_symbol", {}).get(sym, {}).get("trades")),
                   None)
        bt = {"id": ref[0]["id"], "created": ref[0]["created"], "expectancy": ref[1]["expectancy"], "trades": ref[1]["trades"],
              "win_rate": ref[1]["win_rate"], "sd": ref[1].get("sd"), "drawdown_bad": ref[1].get("drawdown_bad")} if ref else None
        expected = None
        if ref and window_days is not None and (days := ref[0]["spec"].get("days")):
            folds = ref[0]["spec"].get("folds") or 1
            expected = ref[1]["trades"] / (days * folds / (folds + 1)) * window_days  # out-of-sample covers folds/(folds+1)
        slips = [t["slip_r"] for t in live if t["mode"] == mode and t["symbol"] == sym and t.get("slip_r") is not None]
        rows.append({"mode": mode, "symbol": sym, "live": st, "range": noise(rs), "backtest": bt,
                     "verdict": verdict(rs, bt and bt["expectancy"]), "alarms": alarms(st, bt, slips, (signals or {}).get((mode, sym), len(rs)), expected)})
    all_r = [t["r"] for t in live]
    return {"rows": rows, "total": stats(all_r), "range": noise(all_r), "target": target, "min_trades": min_trades,
            "verdict": verdict(all_r, None)}


def paper_trades(positions: list[dict[str, Any]], since: int) -> list[Trade]:
    """Closed paper positions in net R. Forced closes say nothing about the strategy, so they don't count."""
    out = []
    for p in positions:
        if p.get("closed") and p["opened"] >= since and p.get("reason") not in ("kill switch", "account reset") and p.get("risk"):
            out.append({"symbol": p["symbol"], "mode": p["mode"], "time": p["opened"], "pnl": p["pnl"], "slip_r": p.get("slip_r"),
                        "r": p["r_net"] if p.get("r_net") is not None else p["pnl"] / p["risk"]})
    return out


def feasibility(signals: list[dict[str, Any]], taken: set[int]) -> dict[tuple[str, str], dict[str, Any]]:
    """Per mode + instrument: the strategy's own result at target risk (all its signals, before costs) apart from what
    the small account could actually take. Mixing the two would grade sizing accidents as strategy quality."""
    rows: dict[tuple[str, str], dict[str, Any]] = defaultdict(lambda: {"signals": 0, "taken": 0, "skipped": 0, "rs": []})
    for s in signals:
        if s["status"] == "killed":
            continue
        r = rows[(s["mode"], s["symbol"])]
        r["signals"] += 1
        r["taken" if s["id"] in taken else "skipped"] += 1
        if s["status"] != "active" and s.get("result_r") is not None:
            r["rs"].append(s["result_r"])
    return {k: {"signals": v["signals"], "taken": v["taken"], "skipped": v["skipped"],
                "signal_r": round(float(np.mean(v["rs"])), 3) if v["rs"] else None} for k, v in rows.items()}


def news_trial(signals: list[dict[str, Any]]) -> dict[str, Any]:
    """Would the news vote have helped? Results of intraday signals grouped by how it voted, while it isn't acted on."""
    rs: dict[str, list[float]] = defaultdict(list)
    for s in signals:
        if (n := s.get("news_trial")) and s.get("result_r") is not None:
            rs["neutral" if n["vote"] == "HOLD" else "agreed" if n["vote"] == s["call"] else "against"].append(s["result_r"])
    return {k: stats(v) for k, v in rs.items()}
