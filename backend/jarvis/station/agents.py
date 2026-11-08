"""Specialist agents. Levels, direction and size come from indicators and rules, never from an LLM. The exception to
"deterministic" is news(): it passes on an LLM's reading of headlines, which could change whether an intraday signal fires.
It has never been backtested, so the station logs that vote without acting on it (station.news_vote: shadow)."""
from dataclasses import dataclass, field
from typing import Any, Literal

import numpy as np
import pandas as pd

from jarvis.indicators import adx, atr, ema, rsi
from jarvis.station.context import Context

Direction = Literal["BUY", "SELL", "HOLD"]


@dataclass
class Vote:
    agent: str
    direction: Direction
    confidence: int  # 0-100
    reasons: list[str]
    invalidation: float | None = None
    watch: str = ""  # what would change this vote
    levels: dict[str, float] = field(default_factory=dict)  # entry/stop/tp, from the technical agent


@dataclass
class RiskReport:
    veto: bool
    reasons: list[str]  # each says why AND what clears it
    warnings: list[str] = field(default_factory=list)  # shown on the card, don't block


def digits(pip: float) -> int:
    return max(0, len(f"{pip:f}".rstrip("0").split(".")[1]) + 1)


def technical(h4: pd.DataFrame, h1: pd.DataFrame, m15: pd.DataFrame, pip: float, cfg: dict[str, Any]) -> Vote:
    """Intraday: 4H bias, 1H structure, 15M entry trigger."""
    d = digits(pip)
    f = lambda x: f"{x:.{d}f}"  # noqa: E731

    c4, e50_4, e200_4 = h4.close.iloc[-1], ema(h4.close, 50).iloc[-1], ema(h4.close, 200).iloc[-1]
    bias = 1 if c4 > e50_4 > e200_4 else -1 if c4 < e50_4 < e200_4 else 0

    e21_1, e50_1 = ema(h1.close, 21), ema(h1.close, 50)
    slope = e21_1.iloc[-1] - e21_1.iloc[-6]
    structure = 1 if e21_1.iloc[-1] > e50_1.iloc[-1] and slope > 0 else -1 if e21_1.iloc[-1] < e50_1.iloc[-1] and slope < 0 else 0
    adx1, atr1 = float(adx(h1).iloc[-1]), float(atr(h1).iloc[-1])

    e21_15, atr15 = ema(m15.close, 21), float(atr(m15).iloc[-1])
    r15, c15 = float(rsi(m15.close).iloc[-1]), float(m15.close.iloc[-1])
    near = 0.3 * atr15
    pulled_up = (m15.low.iloc[-6:] <= e21_15.iloc[-6:] + near).any() and c15 > e21_15.iloc[-1] and r15 > 50
    pulled_dn = (m15.high.iloc[-6:] >= e21_15.iloc[-6:] - near).any() and c15 < e21_15.iloc[-1] and r15 < 50
    trigger = 1 if pulled_up else -1 if pulled_dn else 0

    word = {1: "bullish", -1: "bearish", 0: "neutral"}
    reasons = [
        f"4H bias {word[bias]}: close {f(c4)} vs EMA50 {f(e50_4)} / EMA200 {f(e200_4)}",
        f"1H structure {word[structure]}: EMA21 {'>' if e21_1.iloc[-1] > e50_1.iloc[-1] else '<'} EMA50, ADX {adx1:.0f}",
    ]
    if bias == 0 or bias != structure:
        return Vote("technical", "HOLD", 0, reasons,
                    watch="4H bias and 1H structure must agree (EMA alignment on both)")

    side = "BUY" if bias > 0 else "SELL"
    conf = 40 + (25 if trigger == bias else 0) + min(20, max(0, adx1 - 20))
    if (bias > 0 and r15 > 70) or (bias < 0 and r15 < 30):
        conf -= 15
        reasons.append(f"15M RSI {r15:.0f} stretched — late entry risk")
    reasons.append(f"15M trigger: {'pullback to EMA21 held' if trigger == bias else 'no pullback yet'}, RSI {r15:.0f}")

    ic = cfg["intraday"]
    risk = ic["stop_atr"] * atr1
    stop = c15 - bias * risk
    inval = float(e50_1.iloc[-1]) if (c15 - e50_1.iloc[-1]) * bias > 0 and (e50_1.iloc[-1] - stop) * bias > 0 else stop
    levels = {"entry": c15, "entry_low": c15 - 0.15 * atr15, "entry_high": c15 + 0.15 * atr15, "stop": stop,
              "tp1": c15 + bias * ic["tp_r"][0] * risk, "tp2": c15 + bias * ic["tp_r"][1] * risk}
    return Vote("technical", side, int(max(0, min(100, conf))), reasons, invalidation=inval, levels=levels,
                watch="" if trigger == bias else f"15M pullback toward EMA21 ({f(e21_15.iloc[-1])}) that holds")


def blackout_event(sym: str, ccy: list[str], events: list[dict[str, Any]], now_ms: int,
                   before_min: float, after_min: float) -> dict[str, Any] | None:
    """The high-impact event (for this instrument's currencies, or oil inventories for WTI) whose window we're in."""
    for e in events:
        hits = (e["impact"] == "High" and e["country"] in ccy) or (sym == "WTI_USD" and "crude oil" in e["title"].lower())
        if hits and e["time"] - before_min * 60_000 <= now_ms <= e["time"] + after_min * 60_000:
            return e
    return None


def risk_officer(vote: Vote, h1: pd.DataFrame, m15: pd.DataFrame, bid: float | None, ask: float | None,
                 sym: str, ins: dict[str, Any], events: list[dict[str, Any]], now_s: float, cfg: dict[str, Any]) -> RiskReport:
    rc, out = cfg["risk"], []
    pip, max_spread = ins["pip"], ins["max_spread"]
    if e := blackout_event(sym, ins.get("ccy", []), events, int(now_s * 1000), rc["blackout_before"], rc["blackout_after"]):
        mins = round((e["time"] / 1000 - now_s) / 60)
        when = f"in {mins} min" if mins > 0 else f"{-mins} min ago"
        out.append(f"News blackout: {e['country']} {e['title']} {when} — clears {rc['blackout_after']} min after release")
    last_close_s = float(m15.time.iloc[-1]) + 900
    if now_s - last_close_s > rc["stale_minutes"] * 60:
        out.append("Market closed or data stale — clears when fresh 15M candles arrive")
    if bid is None or ask is None:
        out.append("No live quote — clears when the price stream is live")
    else:
        spread = ask - bid
        if spread / pip > max_spread:
            out.append(f"Spread {spread / pip:.1f} pips > max {max_spread} — clears when spread normalises")
        lv = vote.levels
        if lv:
            rr = (abs(lv["tp1"] - lv["entry"]) - spread) / (abs(lv["entry"] - lv["stop"]) + spread)
            if rr < rc["min_rr"]:
                out.append(f"Net R:R {rr:.2f} < {rc['min_rr']} after spread — clears with tighter spread")
    a = float(adx(h1).iloc[-1])
    if a < rc["adx_chop"]:
        out.append(f"Choppy: 1H ADX {a:.0f} < {rc['adx_chop']} — clears when ADX rises")
    a15 = atr(m15)
    med = float(a15.iloc[-96:].median())
    if a15.iloc[-1] > rc["vol_spike"] * med:
        out.append(f"Volatility spike: 15M ATR {a15.iloc[-1] / med:.1f}x normal — clears when it settles")
    return RiskReport(bool(out), out)


# ---------- fundamental agents ----------

def _clip(x: float) -> float:
    return float(max(-1.0, min(1.0, x)))


def _vote(agent: str, score: float, reasons: list[str], watch: str, cap: int = 80, floor: float = 0.25) -> Vote:
    if abs(score) < floor:
        return Vote(agent, "HOLD", 0, reasons, watch=watch)
    return Vote(agent, "BUY" if score > 0 else "SELL", int(min(cap, 40 + 40 * abs(score))), reasons)


def _bp_change(s: pd.Series | None, days: int = 5) -> float | None:
    return None if s is None or len(s) <= days else float(s.iloc[-1] - s.iloc[-1 - days]) * 100


def _next_event(ccy: list[str], events: list[dict[str, Any]], now_ms: int) -> str | None:
    for e in events:
        if e["impact"] == "High" and e["country"] in ccy and now_ms < e["time"] < now_ms + 86_400_000:
            h, m = divmod(int((e["time"] - now_ms) / 60_000), 60)
            return f"Next high-impact: {e['country']} {e['title']} in {h}h {m:02d}m"
    return None


def macro(sym: str, ctx: Context, prices: dict[str, dict[str, Any]], ins: dict[str, Any],
          cfg: dict[str, Any], now_ms: int) -> Vote:
    """USD strength (DXY trend + US 2Y) and yields, mapped onto each instrument's drivers."""
    reasons: list[str] = []
    usd_parts: list[tuple[float, float]] = []  # (score, weight)
    if ctx.dxy is not None and len(ctx.dxy) > 25:
        d = ctx.dxy.close
        chg = (d.iloc[-1] / d.iloc[-25] - 1) * 100
        usd_parts.append((_clip(chg / 0.5), 0.6))
        reasons.append(f"DXY {d.iloc[-1]:.2f}, {chg:+.2f}% over 24h")
    two, ten, real = (_bp_change(ctx.yields.get(k)) for k in ("DGS2", "DGS10", "DFII10"))
    if two is not None:
        usd_parts.append((_clip(two / 15), 0.4))
        reasons.append(f"US 2Y {ctx.yields['DGS2'].iloc[-1]:.2f}% ({two:+.0f}bp 5d)")
    if not usd_parts:
        return Vote("macro", "HOLD", 0, ["Macro data not loaded yet (DXY / yields)"], watch="macro feeds to load")
    usd = sum(s * w for s, w in usd_parts) / sum(w for _, w in usd_parts)

    if sym in ("EUR_USD", "GBP_USD"):
        score = -usd
    elif sym == "USD_JPY":
        score = 0.5 * usd + 0.5 * _clip((ten or 0) / 15)
        if ten is not None:
            reasons.append(f"US 10Y {ten:+.0f}bp 5d (yield differential driver)")
    elif sym in ("XAU_USD", "XAG_USD"):
        score = -0.5 * usd - 0.5 * _clip((real or 0) / 10)
        if real is not None:
            reasons.append(f"US 10Y real yield {ctx.yields['DFII10'].iloc[-1]:.2f}% ({real:+.0f}bp 5d)")
        if sym == "XAG_USD" and "XAU_USD" in prices and "XAG_USD" in prices:
            reasons.append(f"Gold/silver ratio {prices['XAU_USD']['bid'] / prices['XAG_USD']['bid']:.1f}; silver tracks gold with higher beta")
    else:  # WTI: macro is a weak driver; supply news dominates
        score = -0.3 * usd
        reasons.append("Oil: USD is a minor driver — OPEC+/EIA/supply news dominate")

    v = _vote("macro", score, reasons, "a clearer USD / yield trend")
    if sym == "USD_JPY" and sym in prices and prices[sym]["bid"] > cfg["usdjpy_intervention"]:
        v.reasons.append(f"Above {cfg['usdjpy_intervention']}: Japan MoF intervention risk")
        if v.direction == "BUY":
            v.confidence = max(0, v.confidence - 15)
    if nxt := _next_event(ins.get("ccy", []), ctx.calendar, now_ms):
        v.reasons.append(nxt)
    return v


def news(sym: str, ctx: Context, now_ms: int) -> Vote:
    view = ctx.news_view
    if not view or now_ms - view.get("updated", 0) > 3_600_000:
        return Vote("news", "HOLD", 0, ["No fresh news analysis"], watch="news analysis to refresh")
    iv = next((x for x in view["instruments"] if x["symbol"] == sym), None)
    if iv is None:
        return Vote("news", "HOLD", 0, ["Instrument missing from news analysis"])
    reasons = [f"News ({view['source']}, mood {view['risk_sentiment'].replace('_', '-')}): {iv['reason']}"]
    reasons += [f"“{h}”" for h in iv["headlines"][:2]]
    if view.get("geopolitical_alert"):
        reasons.append(f"Geopolitical alert: {view['geopolitical_alert']}")
    if iv["bias"] == "NEUTRAL":
        return Vote("news", "HOLD", 0, reasons, watch="a clear news catalyst")
    return Vote("news", iv["bias"], iv["confidence"], reasons)


def sentiment(sym: str, ctx: Context) -> Vote:
    """Contrarian at positioning extremes: retail (Capital.com clients) and CFTC speculators (COT)."""
    parts, reasons = [], []
    if (r := ctx.retail.get(sym)) is not None:
        reasons.append(f"Retail {r:.0f}% long")
        if r >= 70 or r <= 30:
            parts.append(-(r - 50) / 50)
            reasons[-1] += " — crowded, contrarian " + ("bearish" if r >= 70 else "bullish")
    cot = ctx.cot.get(sym)
    if cot is not None and len(cot) >= 52:
        net = -cot.net_pct if sym == "USD_JPY" else cot.net_pct  # CFTC yen longs = short USD/JPY
        pct = float((net < net.iloc[-1]).mean() * 100)
        reasons.append(f"COT speculators net {net.iloc[-1]:+.0f}% of OI, 3y percentile {pct:.0f} ({cot.date.iloc[-1].date()})")
        if pct >= 90 or pct <= 10:
            parts.append(-0.6 if pct >= 90 else 0.6)
            reasons[-1] += " — extreme"
    if not reasons:
        return Vote("sentiment", "HOLD", 0, ["Positioning data not loaded yet"], watch="sentiment feeds to load")
    return _vote("sentiment", _clip(float(np.sum(parts))) if parts else 0.0, reasons, "positioning at an extreme", cap=70, floor=0.3)
