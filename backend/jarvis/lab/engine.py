"""Strategies tab backtester: fills and exits the setups from lab.rules on Capital.com bid/ask candles, then runs a
walk-forward per strategy. Every parameter combination is simulated over the whole period once; fold k picks the
combination with the best in-sample expectancy (bars before the fold) and is judged on the fold's own bars.

Fills: market setups at the next bar's open (ask to buy, bid to sell) plus slippage; limit setups when the ask (buy)
or bid (sell) reaches the price, at the open if it gapped through. A stop and target in the same bar count as the
stop; a limit order's fill bar can stop out but not take profit. Overnight funding is charged on the notional for
the time held. Results are in R (initial risk)."""
import asyncio
from datetime import datetime, timezone
from typing import Any, Callable

import numpy as np
import pandas as pd

from jarvis import history, performance
from jarvis.lab import stats as lab_stats
from jarvis.lab import account as lab_account
from jarvis.lab import books as lab_books
from jarvis.lab import options as lab_options
from jarvis.lab.rules import RESEARCH_GROUPS, CATALOG, Plan, Setup, combos, label

TF = {"MINUTE_3": 180, "MINUTE_15": 900, "HOUR": 3600, "HOUR_4": 14_400, "DAY": 86_400}
MAX_DAYS = {"MINUTE_3": 365, "MINUTE_15": 365, "HOUR": 1100, "HOUR_4": 2200, "DAY": 9800}
MIN_IS_TRADES = 10
EMBARGO = 0.01
RANDOM_SHUFFLES = 10
SCAN_DAYS = {"MINUTE_15": 365, "HOUR": 730, "HOUR_4": 1500, "DAY": 9000}  # strategy edge scan: history per timeframe


def to_minutes(df: pd.DataFrame, n: int) -> pd.DataFrame:
    """1-minute candles -> n-minute candles (bid, ask and mid OHLC; volume summed), time = the bar's open."""
    g = df.groupby(df.time // (60 * n) * (60 * n))
    agg: dict[str, str] = {"volume": "sum"}
    for pre in ("", "bid_", "ask_"):
        agg.update({f"{pre}open": "first", f"{pre}high": "max", f"{pre}low": "min", f"{pre}close": "last"})
    out = g.agg({k: v for k, v in agg.items() if k in df}).reset_index()
    return out[[c for c in df.columns if c in out]]


def scan_strategies(tf: str) -> list[str]:
    """Rules worth scanning on a timeframe: ICT needs intraday sessions; trend following takes no trades on 15m."""
    return [k for k in CATALOG if CATALOG[k]["group"] not in RESEARCH_GROUPS and not (k == "ict_sweep_fvg" and tf in ("HOUR_4", "DAY"))
            and not (k == "trend_following" and tf == "MINUTE_15")]

Trade = dict[str, Any]
Loader = Callable[[str, str, float], pd.DataFrame]


def simulate(sym: str, key: str, df: pd.DataFrame, plan: Plan, pip: float, slip_pips: float, funding_pct: float,
             bar_s: int, lo: int = 0, funding_short: float | None = None) -> list[Trade]:
    """One position at a time. Setups before bar `lo` are ignored (indicator warm-up). funding_pct is the yearly cost of
    a long, funding_short of a short when it differs (negative earns it)."""
    T = df.time.to_numpy()
    bo, bh, bl, bc = (df[f"bid_{k}"].to_numpy() for k in ("open", "high", "low", "close"))
    ao, ah, al, ac = (df[f"ask_{k}"].to_numpy() for k in ("open", "high", "low", "close"))
    mid_c = df.close.to_numpy()
    n, slip = len(df), slip_pips * pip
    xl = plan.exit_long if plan.exit_long is not None else np.zeros(n, bool)
    xs = plan.exit_short if plan.exit_short is not None else np.zeros(n, bool)
    trades: list[Trade] = []
    free = lo
    for s in sorted(plan.setups, key=lambda s: s.i):
        if s.i < free or s.i + 1 >= n:
            continue
        side, j, fill, stop, target = s.side, None, 0.0, s.stop, s.target
        if s.entry is None:
            j = s.i + 1
            fill = (ao[j] if side > 0 else bo[j]) + side * slip
            # keep the planned stop/target distances from the signal close, moved to the fill (a gap would otherwise
            # leave a stop next to the entry and blow up R)
            shift = fill - mid_c[s.i]
            stop, target = stop + shift, None if target is None else target + shift
        else:
            for k in range(s.i + 1, min(n, s.i + 1 + max(1, s.expiry))):
                if s.stop_entry:  # buy-stop / sell-stop: fills on a break through the level (at the open after a gap)
                    hit = (ah[k] >= s.entry) if side > 0 else (bl[k] <= s.entry)
                    px = max(s.entry, ao[k]) if side > 0 else min(s.entry, bo[k])
                else:
                    hit = (al[k] <= s.entry) if side > 0 else (bh[k] >= s.entry)
                    px = min(s.entry, ao[k]) if side > 0 else max(s.entry, bo[k])
                if hit:
                    j = k
                    fill = px + side * slip
                    break
            if j is None:
                free = min(n, s.i + 1 + max(1, s.expiry))
                continue
        risk = (fill - stop) * side
        if risk <= 0 or (target is not None and (target - fill) * side <= 0):
            free = j
            continue
        reason, exit_px, k = "open", None, j
        for k in range(j, n):
            adv_open, adv = (bo[k], bl[k]) if side > 0 else (ao[k], ah[k])
            if (adv - stop) * side <= 0:
                gapped = k > j and (adv_open - stop) * side <= 0
                exit_px, reason = (adv_open if gapped else stop) - side * slip, "stop"
                break
            fav = bh[k] if side > 0 else al[k]
            if target is not None and (fav - target) * side >= 0 and not (s.entry is not None and k == j):
                exit_px, reason = target, "target"
                break
            close_px = (bc[k] if side > 0 else ac[k]) - side * slip
            if (xl[k] if side > 0 else xs[k]):
                exit_px, reason = close_px, "exit rule"
                break
            if s.max_bars and k - j + 1 >= s.max_bars:
                exit_px, reason = close_px, "time"
                break
        if exit_px is None:  # still open at the end of the data: mark to the last close
            exit_px = bc[-1] if side > 0 else ac[-1]
        days = (T[k] + bar_s - T[j]) / 86_400
        funding = funding_pct if side > 0 or funding_short is None else funding_short
        r = (exit_px - fill) * side / risk - fill * funding / 100 * days / 365 / risk
        trades.append({"symbol": sym, "mode": key, "strategies": [key], "session": "24h", "i": j,
                       "call": "BUY" if side > 0 else "SELL", "time": int(T[j] * 1000), "closed_at": int((T[k] + bar_s) * 1000),
                       "entry": round(float(fill), 6), "exit": round(float(exit_px), 6), "status": reason,
                       "i_exit": k, "risk_px": round(float(risk), 6), "r": round(float(r), 3), "days": round(float(days), 2), "tag": s.tag})
        free = k
    return trades


def with_spread(df: pd.DataFrame) -> pd.DataFrame:
    """Older Capital.com candles (daily ones especially) often have bid == ask. Where the spread is missing, apply the
    instrument's median quoted spread (as a fraction of price) around the mid so those years aren't free to trade."""
    sp = (df.ask_close - df.bid_close) / df.close
    known = sp[sp > 0]
    if known.empty or (sp > 0).all():
        return df
    # the median so far, so a past bar never borrows from later ones; before the first known spread, that first one
    half = sp.where(sp > 0).expanding().median().bfill() / 2
    miss = sp <= 0
    df = df.copy()
    for k in ("open", "high", "low", "close"):
        df.loc[miss, f"bid_{k}"] = df.loc[miss, k] * (1 - half[miss])
        df.loc[miss, f"ask_{k}"] = df.loc[miss, k] * (1 + half[miss])
    return df


def widen(df: pd.DataFrame, x: float) -> pd.DataFrame:
    """The same candles with the bid/ask spread multiplied by x."""
    out = df.copy()
    for k in ("open", "high", "low", "close"):
        half = (df[f"ask_{k}"] - df[f"bid_{k}"]) / 2 * (x - 1)
        out[f"bid_{k}"], out[f"ask_{k}"] = df[f"bid_{k}"] - half, df[f"ask_{k}"] + half
    return out


def random_plan(df: pd.DataFrame, plan: Plan, lo: int, rng: np.random.Generator) -> Plan:
    """The same number of trades with the same stop and target distances and time limits, at random bars and in random
    directions. A rule that can't beat this has no entry skill, whatever its win rate."""
    close, out = df.close.to_numpy(), []
    for s in plan.setups:
        ref = s.entry if s.entry is not None else close[s.i]
        i, side = int(rng.integers(lo, max(lo + 1, len(df) - 1))), int(rng.choice([-1, 1]))
        stop = close[i] - side * abs(ref - s.stop)
        target = None if s.target is None else close[i] + side * abs(s.target - ref)
        out.append(Setup(i, side, float(stop), None if target is None else float(target), max_bars=s.max_bars))
    return Plan(out)


def verdict(st: dict[str, Any], positive_folds: int, folds: int, checks: dict[str, Any] | None = None) -> str:
    """"passes screen" alone is weak: rules with no edge at all reach it often. Only the luck checks can lift it."""
    if st["trades"] < 30:
        return "too few trades"
    e, pf = st["expectancy"] or 0, st["profit_factor"] or 0
    if e >= 0.1 and pf >= 1.15 and positive_folds * 2 >= folds:
        return "survives checks" if checks and checks["passed"] else "passes screen"
    return "marginal" if e > 0 else "doesn't work"


def _period(t_ms: int, by: str) -> str:
    d = datetime.fromtimestamp(t_ms / 1000, timezone.utc)
    return f"{d:%Y-%m}" if by == "month" else f"{d:%Y}"


def _thin(points: list[dict[str, Any]], n: int = 1500) -> list[dict[str, Any]]:
    if len(points) <= n:
        return points
    step = len(points) / n
    return [points[int(k * step)] for k in range(n)] + [points[-1]]


def evaluate(key: str, runs: list[tuple[dict[str, Any], list[Trade]]], spans: dict[str, tuple[int, int]], folds: int,
             by: str, prior_trials: int = 0, fresh_since: int = 0, account: dict[str, Any] | None = None) -> dict[str, Any]:
    """Walk-forward over precomputed full-period trades per parameter combination. spans: symbol -> (first, last+1)
    bar index of the test range; a trade's position in it decides its fold."""
    def frac(t: Trade) -> float:
        a, b = spans[t["symbol"]]
        return (t["i"] - a) / max(1, b - a)

    def frac_exit(t: Trade) -> float:
        a, b = spans[t["symbol"]]
        return (t.get("i_exit", t["i"]) - a) / max(1, b - a)

    edges = np.linspace(0, 1, folds + 2)
    fold_rows, oos = [], []
    for f in range(1, folds + 1):
        # purge: an in-sample trade must be over (plus a 1% gap) before the fold starts, or it leaks the fold's own prices
        by_combo = [(p, [t for t in ts if frac_exit(t) < edges[f] - EMBARGO], [t for t in ts if edges[f] <= frac(t) < edges[f + 1]])
                    for p, ts in runs]
        scored = [(p, performance.stats([t["r"] for t in is_]), test) for p, is_, test in by_combo]
        ok = [x for x in scored if x[1]["trades"] >= MIN_IS_TRADES]
        p, is_st, test = max(ok, key=lambda x: x[1]["expectancy"]) if ok else max(scored, key=lambda x: x[1]["trades"])
        oos += test
        fold_rows.append({"fold": f, "from": min((t["time"] for t in test), default=None),
                          "to": max((t["time"] for t in test), default=None), "params": label(p),
                          "in_sample": is_st, "out_of_sample": performance.stats([t["r"] for t in test])})
    oos.sort(key=lambda t: t["time"])
    st = performance.stats([t["r"] for t in oos])
    positive = sum(1 for f in fold_rows if (f["out_of_sample"]["expectancy"] or 0) > 0)
    full = [{"params": label(p), **performance.stats([t["r"] for t in ts])} for p, ts in runs]
    grid = np.zeros((lab_stats.BUCKETS, len(runs)))
    for c, (_, ts) in enumerate(runs):
        for t in ts:
            grid[min(lab_stats.BUCKETS - 1, max(0, int(frac(t) * lab_stats.BUCKETS))), c] += t["r"]
    checks = lab_stats.checks([t["r"] for t in oos], [[t["r"] for t in ts] for _, ts in runs], grid, prior_trials + len(runs))
    groups: dict[str, list[float]] = {}
    for t in oos:
        groups.setdefault(_period(t["time"], by), []).append(t["r"])
    return {"key": key, "name": CATALOG[key]["name"], "group": CATALOG[key]["group"], "rules": CATALOG[key]["rules"],
            "summary": st, "verdict": verdict(st, positive, folds, checks), "checks": checks, "positive_folds": positive, "folds": fold_rows,
            "drawdown_range": lab_stats.drawdown_range([t["r"] for t in oos]),
            "account": lab_account.simulate(oos, account) if account else None,
            "fresh": performance.stats([t["r"] for t in oos if t["time"] >= fresh_since]) if fresh_since else None,
            "by_period": {k: performance.stats(v) for k, v in sorted(groups.items())}, "period": by,
            "by_symbol": performance.group(oos, "symbol"), "equity": _thin(performance.equity(oos)),
            "combos": full, "combos_positive": sum(1 for c in full if (c["expectancy"] or 0) > 0),
            "avg_days_held": round(float(np.mean([t["days"] for t in oos])), 2) if oos else None,
            "trades": oos[-300:], "_oos": oos}


def _robustness(key: str, runs: list[tuple[dict[str, Any], list[Trade]]], data: dict[str, pd.DataFrame], spans: dict[str, tuple[int, int]],
                inst: dict[str, Any], slip: float, spec: dict[str, Any], bar_s: int, summary: dict[str, Any]) -> dict[str, Any]:
    """On the variant with the best whole-period result: does it survive wider spreads, and (for the pattern-based rules,
    whose claims rest on where they enter) does it beat random entries?"""
    best = max(runs, key=lambda x: performance.stats([t["r"] for t in x[1]])["expectancy"] or -99)[0]
    if not any(ts for _, ts in runs):
        return {"cost_stress": None, "random_entries": None}

    def go(frames: dict[str, pd.DataFrame], plan_of: Callable[[str, pd.DataFrame], Plan]) -> list[float]:
        rs: list[float] = []
        for sym, df in frames.items():
            rate = spec.get("rates", {}).get(sym, {})
            rs += [t["r"] for t in simulate(sym, key, df, plan_of(sym, df), inst[sym]["pip"], slip, rate.get("long", spec["funding_pct"]),
                                            bar_s, lo=spans[sym][0], funding_short=rate.get("short"))]
        return rs

    planned = {s: CATALOG[key]["fn"](df, {**best, "_bar_s": bar_s}) for s, df in data.items()}
    stress = {f"x{x}": performance.stats(go({s: widen(df, x) for s, df in data.items()}, lambda s, _: planned[s]))["expectancy"] for x in (2, 3)}
    rnd = None
    if CATALOG[key]["group"] in ("ICT", "SMC", "ICC"):
        rng = np.random.default_rng(11)
        means = [performance.stats(go(data, lambda s, df: random_plan(df, planned[s], spans[s][0], rng)))["expectancy"] or 0.0 for _ in range(RANDOM_SHUFFLES)]
        top = float(np.mean(means) + 2 * np.std(means))
        rnd = {"expectancy": round(float(np.mean(means)), 3), "top": round(top, 3), "beats": (summary["expectancy"] or -99) > top}
    return {"cost_stress": stress, "random_entries": rnd}


def run(spec: dict[str, Any], cfg: dict[str, Any], progress: Callable[[str, float], None] = lambda m, p: None,
        load: Loader | None = None) -> dict[str, Any]:
    """spec = {strategies, symbols, tf, days, folds, funding_pct}."""
    tf, days, folds = spec["tf"], float(spec["days"]), int(spec["folds"])
    bar_s = TF[tf]
    warm = max(10.0, 300 * bar_s / 86_400 * 1.5)  # ~300 bars for the 200 EMA
    if "trend_following" in spec["strategies"]:
        warm = max(warm, 380.0)  # its 12-month look-back is in calendar time on every timeframe
    load = load or (lambda s, t, d: asyncio.run(history.ensure(s, t, d, cfg)))
    inst, slip = cfg["capital"]["instruments"], cfg["paper"]["fx"]["slippage_pips"]
    data: dict[str, pd.DataFrame] = {}
    spans: dict[str, tuple[int, int]] = {}
    start_s = datetime.now(timezone.utc).timestamp() - days * 86_400
    for n, sym in enumerate(spec["symbols"]):
        progress(f"downloading {sym} {tf}", 0.3 * n / len(spec["symbols"]))
        df = load(sym, "MINUTE" if tf == "MINUTE_3" else tf, days + warm)
        if tf == "MINUTE_3" and df is not None and not df.empty:
            df = to_minutes(df, 3)  # Capital.com has no 3-minute candles: build them from 1-minute ones
        if df is None or df.empty or "bid_close" not in df:
            raise RuntimeError(f"no {tf} bid/ask history for {sym}")
        df = with_spread(df.reset_index(drop=True))
        df.attrs["symbol"] = sym  # a rule's direction can depend on the quote (selling dollars is buying EUR/USD)
        data[sym] = df
        spans[sym] = (int(np.searchsorted(df.time.to_numpy(), start_s)), len(df))
    by = "month" if days <= 1100 else "year"
    out, total = {}, len(spec["strategies"]) * len(data)
    step = 0
    for key in spec["strategies"]:
        runs: list[tuple[dict[str, Any], list[Trade]]] = []
        for p in combos(key):
            runs.append((p, []))
        for sym, df in data.items():
            progress(f"{CATALOG[key]['name']} · {sym}", 0.3 + 0.7 * step / total)
            step += 1
            for p, ts in runs:
                plan = CATALOG[key]["fn"](df, {**p, "_bar_s": bar_s})
                rate = spec.get("rates", {}).get(sym, {})
                ts += simulate(sym, key, df, plan, inst[sym]["pip"], slip, rate.get("long", spec["funding_pct"]), bar_s,
                               lo=spans[sym][0], funding_short=rate.get("short"))
        out[key] = evaluate(key, runs, spans, folds, by, spec.get("prior_trials", 0), spec.get("fresh_since", 0), spec.get("account"))
        oos = out[key].pop("_oos")
        if spec.get("options"):
            progress("pricing the trades as options", 0.95)
            vols = {s: lab_options.daily_vol(load(s, "DAY", days + 60)) for s in data}
            out[key]["options"] = lab_options.evaluate(oos, vols)
        if spec.get("money"):
            out[key]["money"] = lab_books.money(oos, spec.get("account"))
        out[key].update(_robustness(key, runs, data, spans, inst, slip, spec, bar_s, out[key]["summary"]))
    first = min(int(df.time.iloc[spans[s][0]]) for s, df in data.items() if spans[s][0] < len(df))
    last = max(int(df.time.iloc[-1]) for df in data.values())
    progress("done", 1.0)
    ranked = sorted(out.values(), key=lambda r: r["summary"]["expectancy"] if r["summary"]["trades"] else -99, reverse=True)
    return {"spec": spec, "range": [first, last], "bars": {s: spans[s][1] - spans[s][0] for s in data},
            "strategies": ranked, "notes": NOTES}


NOTES = [
    "Walk-forward: each fold's parameters are picked on earlier bars only, then judged on the fold. The headline numbers "
    "are out-of-sample.",
    "Fills on Capital.com bid/ask candles plus Ras's slippage; stop first when stop and target touch in one bar.",
    "Overnight funding is charged on the notional for the time held, at the broker's own long and short rate where Ras "
    "has it, else the flat yearly rate you set. A rate is today's rate applied to the whole history.",
    "Verdicts: 'passes screen' only means 30+ trades, +0.10R, PF 1.15 and half the folds positive, which rules with no "
    "edge often reach. 'Survives checks' also passes the three luck checks, counted against every variant ever tested.",
    "'Fresh' counts only trades after the settings were frozen: data no rule was chosen on.",
    "ICT, SMC and ICC are discretionary methods. These are fixed, mechanical readings of them; a trader applying them by "
    "eye will take different trades.",
    "'Combos positive' shows how many parameter settings were profitable over the whole period (in-sample). If only a few "
    "are, a good result is more likely luck.",
]
