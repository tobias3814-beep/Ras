"""Backtester: replays history bar by bar through the LIVE strategy / agent / risk code, with bid/ask spreads,
fees and slippage. Walk-forward: the score threshold is chosen in-sample and judged out-of-sample.

Phase A computes every candidate setup once (the expensive part); phase B simulates trades for any threshold cheaply."""
import asyncio
import logging
from dataclasses import dataclass, field, replace
from typing import Any, Callable

import numpy as np
import pandas as pd

from jarvis import history, performance
from jarvis.indicators import atr, ema
from jarvis.scalp import core, risk
from jarvis.scalp.fx import fx_extra
from jarvis.scalp.sessions import near_rollover
from jarvis.scalp.strategies import CRYPTO_STRATEGIES, FX_STRATEGIES
from jarvis.station import agents, filters

log = logging.getLogger("jarvis.backtest")
LIVE_BARS = 300  # the live poller's candle history (feeds/capital.py): replay must see the same, or indicators differ


@dataclass
class Cand:
    i: int                 # index of the bar whose close produced the setup; fill is at bar i+1's open
    direction: int
    score: int
    stop: float            # scalp: structural stop
    atr5: float = 0.0
    strategies: list[str] = field(default_factory=list)
    session: str | None = None
    regime: str = ""
    veto: list[str] = field(default_factory=list)
    levels: dict[str, float] = field(default_factory=dict)   # intraday: the technical agent's levels
    invalidation: float | None = None
    htf: int = 0           # higher-timeframe trend at the setup (intraday: daily, scalp: 1H) — for the trend filter
    chop: bool = False     # 1H market choppy — for the chop filter
    hour: int = -1         # local hour of the fill — for the hours filter


def resample(df: pd.DataFrame, seconds: int) -> pd.DataFrame:
    cols = {c: a for c, a in (("open", "first"), ("high", "max"), ("low", "min"), ("close", "last"), ("volume", "sum"))}
    for side in ("bid", "ask"):
        if f"{side}_close" in df:
            cols.update({f"{side}_open": "first", f"{side}_high": "max", f"{side}_low": "min", f"{side}_close": "last"})
    out = df.groupby(df.time // seconds * seconds).agg(cols)
    return out.rename_axis("time").reset_index()


def with_quotes(df: pd.DataFrame, spread_pct: float) -> pd.DataFrame:
    """Crypto OHLCV has no bid/ask: model a constant spread around mid."""
    if "bid_close" in df:
        return df
    h = spread_pct / 200
    return df.assign(**{f"{s}_{k}": df[k] * (1 - h if s == "bid" else 1 + h) for s in ("bid", "ask") for k in ("open", "high", "low", "close")})


def _closed(frame: pd.DataFrame, seconds: int, t: float, n: int) -> pd.DataFrame:
    j = int(np.searchsorted(frame.time.values + seconds, t, side="right"))
    return frame.iloc[max(0, j - n):j]


# ---------- phase A: candidates ----------

def scalp_candidates(sym: str, m1: pd.DataFrame, cfg: dict[str, Any], kind: str,
                     progress: Callable[[float], None] = lambda p: None, start: int = LIVE_BARS) -> list[Cand]:
    sc, ins = cfg["scalp"], cfg["capital"]["instruments"].get(sym, {})
    m5, m15, h1 = resample(m1, 300), resample(m1, 900), resample(m1, 3600)
    strategies = FX_STRATEGIES if kind == "fx" else CRYPTO_STRATEGIES
    spread = (m1.ask_close - m1.bid_close).values
    out = []
    for i in range(max(LIVE_BARS, start), len(m1)):
        if i % 2000 == 0:
            progress(i / len(m1))
        t = float(m1.time.iloc[i] + 60)
        if kind == "fx":
            session, _, extra = fx_extra(sym, ins, sc["sessions"], [], t)
            if session is None:
                continue
        else:
            session, extra = None, {"vwap_anchor_s": t - t % 86400}
        mid, bias = _closed(m5, 300, t, LIVE_BARS), _closed(m15, 900, t, LIVE_BARS)
        if len(mid) < 120 or len(bias) < 60:
            continue
        x = core.ScalpInput(sym, bias, mid, m1.iloc[i - LIVE_BARS + 1:i + 1], float(m1.close.iloc[i]), t, core.regime(mid), extra)
        d, score, agree = core.confluence(core.run(strategies, x))
        if not d:
            continue
        if kind == "crypto":  # same 1H context adjustment as the live desk
            hc = _closed(h1, 3600, t, 60).close
            if len(hc) >= 50:
                score = max(0, min(100, score + (5 if (1 if ema(hc, 21).iloc[-1] > ema(hc, 50).iloc[-1] else -1) == d else -10)))
        veto = []
        if kind == "fx":
            if near_rollover(int(t * 1000), sc["rollover_minutes"]):
                veto.append("rollover")
            if spread[i] / ins["pip"] > ins["max_spread"]:
                veto.append("max spread")
            med = float(np.median(spread[max(0, i - 60):i + 1]))
            if med > 0 and spread[i] > sc["spread_widen_x"] * med:
                veto.append("spread widened")
        h1w = _closed(h1, 3600, t, 80)
        out.append(Cand(i, d, score, agree[0].stop, float(atr(mid).iloc[-1]), [s.strategy for s in agree], session, x.regime, veto,
                        htf=filters.trend(h1w), chop=filters.choppy(h1w), hour=filters.hour(t, cfg["briefings"]["tz"])))
    return out


def intraday_candidates(sym: str, m15: pd.DataFrame, cfg: dict[str, Any],
                        progress: Callable[[float], None] = lambda p: None, start: int = 300) -> list[Cand]:
    st, ins = cfg["station"], cfg["capital"]["instruments"][sym]
    h1, h4, d1 = resample(m15, 3600), resample(m15, 14400), resample(m15, 86400)
    out = []
    for i in range(max(300, start), len(m15)):
        if i % 500 == 0:
            progress(i / len(m15))
        t = float(m15.time.iloc[i] + 900)
        h4w, h1w, m15w = _closed(h4, 14400, t, 300), _closed(h1, 3600, t, 300), m15.iloc[i - 299:i + 1]
        if len(h4w) < 200 or len(h1w) < 60:
            continue
        v = agents.technical(h4w, h1w, m15w, ins["pip"], st)
        if v.direction == "HOLD":
            continue
        r = agents.risk_officer(v, h1w, m15w, float(m15.bid_close.iloc[i]), float(m15.ask_close.iloc[i]), sym, ins, [], t, st)
        out.append(Cand(i, 1 if v.direction == "BUY" else -1, v.confidence, v.levels["stop"], strategies=["technical"],
                        veto=r.reasons, levels=v.levels, invalidation=v.invalidation,
                        htf=filters.trend(_closed(d1, 86400, t, 80)), chop=filters.choppy(h1w),
                        hour=filters.hour(t, cfg["briefings"]["tz"])))
    return out


# ---------- phase B: simulation ----------

def simulate(sym: str, bars: pd.DataFrame, cands: list[Cand], threshold: float, cfg: dict[str, Any], mode: str,
             lo: int = 0, hi: int | None = None, partial: float = 0.0) -> list[dict[str, Any]]:
    """Trades for candidates in bar range [lo, hi) scoring >= threshold. One position at a time per symbol.
    partial > 0: bank that fraction at TP1, stop to entry, the rest runs to TP2 (as live)."""
    sc, st, tz = cfg["scalp"], cfg["station"], cfg["briefings"]["tz"]
    hi = len(bars) if hi is None else hi
    bar_s = 60 if mode != "intraday" else 900
    fee_pct = cfg["crypto"]["scanner"]["fees_pct"].get(sym.split(":")[0], 0.0) if mode == "crypto" else 0.0
    pip = cfg["capital"]["instruments"].get(sym, {}).get("pip")
    lim = sc["limits"]["fx" if mode == "scalp" else "crypto"]
    T = bars.time.values
    bo, bh, bl, bc = (bars[f"bid_{k}"].values for k in ("open", "high", "low", "close"))
    ao, ah, al, ac = (bars[f"ask_{k}"].values for k in ("open", "high", "low", "close"))
    mid_c = bars.close.values
    trades: list[dict[str, Any]] = []
    free_from = lo
    for c in cands:
        if c.i < free_from or c.i >= hi or c.score < threshold or c.veto or c.i + 1 >= len(bars):
            continue
        j, side = c.i + 1, c.direction
        slip = (cfg["paper"]["fx"]["slippage_pips"] * pip) if pip else (ao[j] * cfg["crypto"]["scanner"]["slippage_pct"] / 100)
        fill = (ao[j] if side > 0 else bo[j]) + side * slip
        if mode == "intraday":
            lv = {k: v + fill - c.levels["entry"] for k, v in c.levels.items()}  # keep the agent's R, move to the fill
        else:
            lv = core.levels(side, fill, c.stop, c.atr5, sc)
        risk_px = abs(fill - lv["stop"])
        fee_px = 2 * fee_pct / 100 * fill
        spread = ao[j] - bo[j]
        if risk_px <= 0 or risk.net_rr(lv, spread + fee_px) < st["risk"]["min_rr"]:
            continue
        if mode == "scalp" and spread > sc["max_spread_tp_pct"] / 100 * abs(lv["tp1"] - fill):
            continue
        created = int(T[j] * 1000)
        if mode != "intraday":
            day0 = risk.day_start_ms(created, tz)
            today = [t for t in trades if t["time"] >= day0]
            if risk.limit_checks(today, 0, c.session, lim, created):
                continue

        mfe, reason, exit_px, k = 0.0, "expired", None, j
        banked, rem, stop, taken = 0.0, 1.0, lv["stop"], False
        max_bars = (sc["max_hold_minutes"] * 60 if mode != "intraday" else st["intraday"]["expiry_hours"] * 3600) // bar_s
        for k in range(j, len(bars)):
            adv_open, adv = (bo[k], bl[k]) if side > 0 else (ao[k], ah[k])
            fav = bh[k] if side > 0 else al[k]
            if (adv - stop) * side <= 0:   # stop first if both touch in one bar (conservative)
                exit_px = (stop if (adv_open - stop) * side > 0 else adv_open) - side * slip
                reason = "breakeven" if taken else "hit_sl"
                break
            target = lv["tp2"] if taken else lv["tp1"]
            if (fav - target) * side >= 0:
                if not taken and partial and "tp2" in lv:  # TP2 can't also fill in this bar (conservative)
                    banked, rem, stop, taken = partial * (lv["tp1"] - fill) * side / risk_px, 1 - partial, fill, True
                    continue
                exit_px, reason = target, "hit_tp2" if taken else "hit_tp"
                break
            mfe = max(mfe, (fav - fill) * side / risk_px)
            close_adv = bc[k] if side > 0 else ac[k]
            n = k - j + 1
            if mode != "intraday" and n >= sc["time_stop_bars"] and mfe < 0.5:
                exit_px, reason = close_adv - side * slip, "time_stop"
                break
            if mode == "intraday" and c.invalidation is not None and (T[k] + 900) % 3600 == 0 \
                    and (mid_c[k] - c.invalidation) * side < 0:
                exit_px, reason = close_adv - side * slip, "invalidated"
                break
            if n >= max_bars:
                exit_px, reason = close_adv - side * slip, "expired"
                break
        if exit_px is None:  # ran out of data: mark to the last close
            exit_px = (bc[-1] if side > 0 else ac[-1])
        r = banked + rem * (exit_px - fill) * side / risk_px - fee_px / risk_px
        trades.append({"symbol": sym, "mode": mode, "call": "BUY" if side > 0 else "SELL", "time": created,
                       "closed_at": int((T[k] + bar_s) * 1000), "entry": fill, "exit": exit_px, "status": reason,
                       "result_r": round(r, 3), "r": round(r, 3), "confidence": c.score, "strategies": c.strategies,
                       "session": c.session or "24h", "regime": c.regime})
        free_from = k + 1
    return trades


# ---------- random entries ----------

RANDOM_SHUFFLES = 10


def random_cands(cands: list[Cand], bars: pd.DataFrame, rng: np.random.Generator) -> list[Cand]:
    """The same setups at random bars in the same hour of the day (so the same session and typical spread), in random
    directions, with the same stop distance and score. What's left to compare is the skill in choosing the entry."""
    if not cands:
        return []
    close, hours = bars.close.to_numpy(), (bars.time.to_numpy() // 3600) % 24
    start, last = cands[0].i, len(bars) - 2
    out = []
    for c in cands:
        if c.veto:
            continue
        pool = np.flatnonzero((hours == hours[c.i]) & (np.arange(len(bars)) >= start) & (np.arange(len(bars)) <= last))
        i, side = int(rng.choice(pool)), int(rng.choice([-1, 1]))
        out.append(replace(c, i=i, direction=side, stop=float(close[i] - side * abs(close[c.i] - c.stop))))
    return sorted(out, key=lambda c: c.i)


def random_entries(data: dict[str, tuple[pd.DataFrame, list[Cand]]], rule_r: float | None, threshold: float, cfg: dict[str, Any],
                   mode: str, partial: float, seed: int = 11) -> dict[str, Any] | None:
    """Does the rule beat its own setups placed at random? 'top' is the shuffles' average plus 2 standard deviations."""
    if rule_r is None:
        return None
    rng, means = np.random.default_rng(seed), []
    for _ in range(RANDOM_SHUFFLES):
        rs = [t["r"] for sym, (bars, cands) in data.items()
              for t in simulate(sym, bars, random_cands(cands, bars, rng), threshold, cfg, mode, partial=partial)]
        means.append(performance.stats(rs)["expectancy"] or 0.0)
    top = float(np.mean(means) + 2 * np.std(means))
    return {"rule": rule_r, "expectancy": round(float(np.mean(means)), 3), "top": round(top, 3), "beats": rule_r > top}


# ---------- runner ----------

VARIANTS: dict[str, dict[str, Any]] = {
    "trend": {"trend": True, "chop": False, "hours": False}, "chop": {"trend": False, "chop": True, "hours": False},
    "hours": {"trend": False, "chop": False, "hours": True}, "all": {"trend": True, "chop": True, "hours": True}}

def _pick(trades_by_th: dict[float, list[dict[str, Any]]], default: float, min_trades: int) -> float:
    ok = {th: performance.stats([t["r"] for t in ts]) for th, ts in trades_by_th.items()}
    ok = {th: s for th, s in ok.items() if s["trades"] >= min_trades}
    return max(ok, key=lambda th: (ok[th]["expectancy"], -th)) if ok else default


def run(spec: dict[str, Any], cfg: dict[str, Any], progress: Callable[[str, float], None] = lambda m, p: None) -> dict[str, Any]:
    """spec = {symbols: [...], mode: scalp | intraday | crypto, days, folds?}."""
    mode, days, folds = spec["mode"], float(spec["days"]), int(spec.get("folds") or cfg["backtest"]["folds"])
    bt, tz = cfg["backtest"], cfg["briefings"]["tz"]
    default_th = cfg["scalp"]["min_score"] if mode != "intraday" else cfg["station"]["min_confidence"]
    data: dict[str, tuple[pd.DataFrame, list[Cand]]] = {}
    for n, sym in enumerate(spec["symbols"]):
        progress(f"downloading {sym}", n / len(spec["symbols"]))
        tf = "MINUTE_15" if mode == "intraday" else ("MINUTE" if mode == "scalp" else "1m")
        bars = asyncio.run(history.ensure(sym, tf, days + (45 if mode == "intraday" else 1), cfg))
        if bars.empty:
            raise RuntimeError(f"no history for {sym}")
        bars = with_quotes(bars, bt["crypto_spread_pct"])
        cb = lambda p, s=sym, k=n: progress(f"replaying {s}", (k + p) / len(spec["symbols"]))  # noqa: E731
        cands = intraday_candidates(sym, bars, cfg, cb) if mode == "intraday" else \
            scalp_candidates(sym, bars, cfg, "fx" if mode == "scalp" else "crypto", cb)
        data[sym] = (bars, cands)

    used = cfg.get("exits", {}).get("partial", 0.0)  # the live exit style

    def sim(th: float, a: float, b: float, partial: float = used,
            keep: Callable[[Cand], bool] = lambda c: True) -> list[dict[str, Any]]:
        """All symbols, the time slice [a, b) as fractions of each symbol's tradable range, candidates passing keep."""
        out = []
        for sym, (bars, cands) in data.items():
            start = cands[0].i if cands else 0
            span = len(bars) - start
            out += simulate(sym, bars, [c for c in cands if keep(c)], th, cfg, mode, start + int(a * span), start + int(b * span), partial)
        return out

    def walk_forward(partial: float, filt: dict[str, Any] = filters.OFF) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[int]]:
        """Anchored folds: fold k trains on [0, edge k), tests on [edge k, edge k+1). The threshold and (with the hours
        filter) the hours to skip are chosen on the training slice only."""
        fixed = {**filt, "hours": []}
        base = lambda c: not filters.reasons(fixed, c.direction, c.htf, c.chop, c.hour, "")  # noqa: E731
        edges = np.linspace(0, 1, folds + 2)
        fold_rows, oos, hrs = [], [], []
        for k in range(1, folds + 1):
            by_th = {th: sim(th, 0, edges[k], partial, base) for th in bt["threshold_grid"]}
            th = _pick(by_th, default_th, bt["min_trades_is"])
            hrs = filters.bad_hours(by_th[th], tz) if filt.get("hours") else []
            test = sim(th, edges[k], edges[k + 1], partial, lambda c, h=hrs: base(c) and c.hour not in h)
            oos += test
            fold_rows.append({"fold": k, "train": [0, round(edges[k], 2)], "test": [round(edges[k], 2), round(edges[k + 1], 2)],
                              "threshold": th, "skip_hours": hrs, "in_sample": performance.stats([t["r"] for t in by_th[th]]),
                              "out_of_sample": performance.stats([t["r"] for t in test])})
        return fold_rows, oos, hrs

    progress("walk-forward", 0.95)
    fold_rows, oos, _ = walk_forward(used)
    # same data, the other exit style: full exit at TP1 vs bank part at TP1 and run the rest to TP2
    alt = used or 0.5
    other = walk_forward(0.0 if used else alt)[1]
    exits = {"fraction": alt, "used": used,
             "full": performance.stats([t["r"] for t in (other if used else oos)]),
             "partial": performance.stats([t["r"] for t in (oos if used else other)])}
    # same data, the optional filters (live exit style); "hours" = skip list from the last fold, i.e. the most data
    variants = {"none": {"stats": performance.stats([t["r"] for t in oos]), "skip_hours": []}}
    for name, filt in VARIANTS.items():
        _, vo, vh = walk_forward(used, filt)
        variants[name] = {"stats": performance.stats([t["r"] for t in vo]), "skip_hours": vh}
    baseline = sim(default_th, 0, 1)
    rnd = None if mode == "intraday" else random_entries(data, performance.stats([t["r"] for t in baseline])["expectancy"],
                                                         default_th, cfg, mode, used)
    notes = _notes(mode)
    if rnd:
        notes.append(f"Random entries at the same hours and stop distances averaged {rnd['expectancy']:+.2f}R against this rule's "
                     f"{rnd['rule']:+.2f}R at the default threshold: it {'beats' if rnd['beats'] else 'does not clearly beat'} them.")
    first = min(int(b.time.iloc[0]) for b, _ in data.values())
    last = max(int(b.time.iloc[-1]) for b, _ in data.values())
    progress("done", 1.0)
    return {"spec": {**spec, "folds": folds}, "range": [first, last], "default_threshold": default_th,
            "candidates": {s: len(c) for s, (_, c) in data.items()}, "folds": fold_rows,
            "out_of_sample": performance.report(oos, tz), "baseline": performance.report(baseline, tz),
            "trades": sorted(oos, key=lambda t: t["time"])[-500:], "exits": exits, "variants": variants,
            "random_entries": rnd, "notes": notes}


def _notes(mode: str) -> list[str]:
    n = ["Fills at the next bar's open on the correct side (ask to buy, bid to sell) plus slippage; stop wins if stop and "
         "target touch in the same bar.", "Signals close in full at TP1, as live."]
    if mode == "crypto":
        n += ["OHLCV has no bid/ask: spread is modelled as a constant.", "Order-book and funding/OI strategies need live "
              "data and are not replayed.", "BTC correlation filter not replayed."]
    else:
        n += ["Uses Capital.com bid/ask history, so real spread widening is included. There is no free historical "
              "calendar, so news blackouts and post-news/EIA setups are not replayed at all: live trades around news differ."]
    if mode == "intraday":
        n.append("Technical agent + Risk Officer only — macro/news/sentiment history is not replayed.")
    return n


# ---------- edge scan verdicts ----------

def edge_verdict(run: dict[str, Any], min_trades: int = 30, keep_r: float = 0.1, keep_pf: float = 1.15) -> dict[str, Any]:
    """One instrument + mode: pick the better exit style out-of-sample, then keep on / switch off / marginal / too few."""
    res, spec = run["result"], run["spec"]
    ex = res.get("exits") or {"full": res["out_of_sample"]["summary"], "partial": None, "fraction": 0}
    full, part = ex["full"], ex["partial"]
    e = lambda s: s["expectancy"] if s and s["expectancy"] is not None else -99.0  # noqa: E731
    style = "partial" if part and e(part) > e(full) + 0.05 else "full"
    best = part if style == "partial" else full
    # a filter wins only if it beats no filter by 0.05R+ and still leaves 20+ trades (several were tried: demand a margin)
    vs = res.get("variants") or {}
    base_e = e(vs["none"]["stats"]) if "none" in vs else e(best)
    ok = {k: v for k, v in vs.items() if k != "none" and v["stats"]["trades"] >= 20 and e(v["stats"]) >= base_e + 0.05}
    filt = max(ok, key=lambda k: e(ok[k]["stats"])) if ok else "none"
    if filt != "none" and e(ok[filt]["stats"]) > e(best):
        best, style = ok[filt]["stats"], "full" if not ex.get("used") else "partial"
    if best["trades"] < min_trades:
        verdict = "too few trades"
    elif e(best) >= keep_r and (best["profit_factor"] or 0) >= keep_pf:
        verdict = "keep on"
    elif e(best) < 0:
        verdict = "switch off"
    else:
        verdict = "marginal"
    return {"symbol": spec["symbols"][0], "mode": spec["mode"], "days": spec["days"], "full": full, "partial": part,
            "fraction": ex.get("fraction"), "best_exit": style, "verdict": verdict, "best_filter": filt,
            "best": best, "variants": vs,
            "filter": {**VARIANTS[filt], "hours": vs[filt]["skip_hours"]} if filt != "none" else dict(filters.OFF)}


def recommend_partial(rows: list[dict[str, Any]]) -> bool:
    """Use partial exits if, summed over what's worth keeping (or everything, if nothing is), they made more R."""
    pool = [r for r in rows if r["verdict"] == "keep on"] or rows
    tot = lambda k: sum((r[k] or {}).get("total_r", 0.0) for r in pool)  # noqa: E731
    return bool(pool) and tot("partial") > tot("full")
