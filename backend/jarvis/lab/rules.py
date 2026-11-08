"""Strategy rules for the Strategies tab, written so a computer can test them: indicator systems, trend following,
and mechanical versions of ICT, SMC and ICC. Each rule reads closed bars only and returns setups; lab.engine fills
and exits them. A setup at bar i is known at i's close: a market setup fills at bar i+1's open, a limit setup works
from bar i+1 for `expiry` bars.

ICT, SMC and ICC are discretionary methods taught by traders, not published research. The definitions here are one
reasonable, fixed reading of each (stated in DESCRIPTIONS); a trader who applies them by eye will differ."""
from dataclasses import dataclass
from itertools import product
from typing import Any, Callable
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from jarvis.indicators import adx, atr, ema, rsi

RESEARCH_GROUPS = {"Ras 1.1", "Legends", "Books"}  # shown on their own pages, kept out of scans and room analysis

NY = ZoneInfo("America/New_York")


@dataclass
class Setup:
    i: int                      # bar whose close produced the setup
    side: int                   # +1 buy, -1 sell
    stop: float
    target: float | None        # None: exit by the strategy's exit rule or max_bars
    entry: float | None = None  # limit price; None = market at the next open
    expiry: int = 0             # bars a limit order keeps working
    max_bars: int = 0           # 0 = no time limit
    tag: str = ""
    stop_entry: bool = False    # entry is a buy-stop / sell-stop (fills on a break through it), not a limit


@dataclass
class Plan:
    setups: list[Setup]
    exit_long: np.ndarray | None = None   # True at a close where an open long should exit
    exit_short: np.ndarray | None = None


Rule = Callable[[pd.DataFrame, dict[str, Any]], Plan]


# ---------- shared pieces ----------

def swings(df: pd.DataFrame, n: int) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Fractal swings: bar k is a swing high if its high is the highest of k-n..k+n. It is only known n bars later,
    so the arrays hold, at each bar t, the latest swing high / low *confirmed by t* and the bar it formed on."""
    h, lo = df.high.to_numpy(), df.low.to_numpy()
    m = len(df)
    sh, sl = np.full(m, np.nan), np.full(m, np.nan)
    shi, sli = np.full(m, -1), np.full(m, -1)
    cur_h, cur_l, ih, il = np.nan, np.nan, -1, -1
    for t in range(m):
        k = t - n
        if k >= n:
            win_h, win_l = h[k - n:t + 1], lo[k - n:t + 1]
            if h[k] == win_h.max() and np.argmax(win_h) == n:
                cur_h, ih = h[k], k
            if lo[k] == win_l.min() and np.argmin(win_l) == n:
                cur_l, il = lo[k], k
        sh[t], sl[t], shi[t], sli[t] = cur_h, cur_l, ih, il
    return sh, sl, shi, sli


def cross_up(a: pd.Series, b: pd.Series | float) -> np.ndarray:
    return ((a > b) & (a.shift() <= (b.shift() if isinstance(b, pd.Series) else b))).to_numpy()


def cross_down(a: pd.Series, b: pd.Series | float) -> np.ndarray:
    return ((a < b) & (a.shift() >= (b.shift() if isinstance(b, pd.Series) else b))).to_numpy()


def _target(side: int, entry: float, stop: float, rr: float | None) -> float | None:
    return None if rr is None else entry + side * rr * abs(entry - stop)


def _market(df: pd.DataFrame, i: int, side: int, stop_dist: float, rr: float | None, **kw: Any) -> Setup:
    c = float(df.close.iat[i])
    stop = c - side * stop_dist
    return Setup(i, side, stop, _target(side, c, stop, rr), **kw)


# ---------- indicator systems ----------

def ema_cross(df: pd.DataFrame, p: dict[str, Any]) -> Plan:
    f, s = ema(df.close, p["fast"]), ema(df.close, p["slow"])
    a = atr(df, 14).to_numpy()
    up, dn = cross_up(f, s), cross_down(f, s)
    out = [_market(df, i, 1 if up[i] else -1, 2 * a[i], p["rr"]) for i in range(p["slow"], len(df))
           if (up[i] or dn[i]) and a[i] > 0]
    return Plan(out, dn, up)  # exit on the opposite cross


def macd_trend(df: pd.DataFrame, p: dict[str, Any]) -> Plan:
    macd = ema(df.close, 12) - ema(df.close, 26)
    hist = macd - ema(macd, 9)
    trend = ema(df.close, 200).to_numpy()
    c, a = df.close.to_numpy(), atr(df, 14).to_numpy()
    up, dn = cross_up(hist, 0.0), cross_down(hist, 0.0)
    out = []
    for i in range(200, len(df)):
        side = 1 if up[i] and c[i] > trend[i] else -1 if dn[i] and c[i] < trend[i] else 0
        if side and a[i] > 0:
            out.append(_market(df, i, side, 2 * a[i], p["rr"]))
    return Plan(out)


def rsi_pullback(df: pd.DataFrame, p: dict[str, Any]) -> Plan:
    """Buy oversold dips in an uptrend (above the 200 EMA), sell overbought rallies in a downtrend; exit when RSI is
    back at 50."""
    n, lo_lvl = p["period"], p["level"]
    r = rsi(df.close, n)
    trend, c, a = ema(df.close, 200).to_numpy(), df.close.to_numpy(), atr(df, 14).to_numpy()
    up, dn = cross_up(r, lo_lvl), cross_down(r, 100 - lo_lvl)
    out = []
    for i in range(200, len(df)):
        side = 1 if up[i] and c[i] > trend[i] else -1 if dn[i] and c[i] < trend[i] else 0
        if side and a[i] > 0:
            out.append(_market(df, i, side, p["atr_stop"] * a[i], None, max_bars=p["max_bars"]))
    rv = r.to_numpy()
    return Plan(out, rv >= 50, rv <= 50)


def bollinger_reversion(df: pd.DataFrame, p: dict[str, Any]) -> Plan:
    """Close back inside the band after closing outside it; target the middle band; optional range filter (ADX < 25)."""
    mid = df.close.rolling(20).mean()
    sd = df.close.rolling(20).std()
    upper, lower = mid + p["k"] * sd, mid - p["k"] * sd
    c, a, m = df.close.to_numpy(), atr(df, 14).to_numpy(), mid.to_numpy()
    lo_, up_ = lower.to_numpy(), upper.to_numpy()
    ranging = (adx(df, 14) < 25).to_numpy() if p["range_only"] else np.ones(len(df), bool)
    out = []
    for i in range(21, len(df)):
        if not ranging[i] or not a[i] > 0:
            continue
        if c[i - 1] < lo_[i - 1] and c[i] > lo_[i] and m[i] > c[i]:
            out.append(Setup(i, 1, c[i] - 1.5 * a[i], m[i], max_bars=40))
        elif c[i - 1] > up_[i - 1] and c[i] < up_[i] and m[i] < c[i]:
            out.append(Setup(i, -1, c[i] + 1.5 * a[i], m[i], max_bars=40))
    return Plan(out)


def donchian_breakout(df: pd.DataFrame, p: dict[str, Any]) -> Plan:
    """Turtle-style: close beyond the N-bar channel; 2 ATR stop; exit on a close beyond the opposite M-bar channel."""
    n, m = p["entry"], p["exit"]
    hi, lo = df.high.rolling(n).max().shift(), df.low.rolling(n).min().shift()
    xhi, xlo = df.high.rolling(m).max().shift(), df.low.rolling(m).min().shift()
    c, a = df.close, atr(df, 20).to_numpy()
    up, dn = (c > hi).to_numpy(), (c < lo).to_numpy()
    out = [_market(df, i, 1 if up[i] else -1, 2 * a[i], None) for i in range(n + 1, len(df)) if (up[i] or dn[i]) and a[i] > 0]
    return Plan(out, (c < xlo).to_numpy(), (c > xhi).to_numpy())


# ---------- trend following ----------

def trend_following(df: pd.DataFrame, p: dict[str, Any]) -> Plan:
    """Time-series momentum: hold the direction of the past return (1/3/6/12-month blend or 12 months only, in bars of
    this timeframe), flip when it changes. The stop (k x ATR) only caps a disaster and sizes the trade: risking 1R on
    a volatility-based stop is volatility-scaled sizing."""
    per_day = max(1, round(86_400 / p["_bar_s"]))
    lbs = [21, 63, 126, 252] if p["lookback"] == "blend" else [252]
    c = df.close
    score = sum(np.sign(c / c.shift(lb * per_day) - 1) for lb in lbs) / len(lbs)
    sig = np.sign(score).fillna(0).to_numpy()
    a = atr(df, 20).to_numpy()
    out, prev = [], 0.0
    for i in range(len(df)):
        if sig[i] != 0 and sig[i] != prev and a[i] > 0:
            out.append(_market(df, i, int(sig[i]), p["atr_stop"] * a[i], None))
        if sig[i] != 0:
            prev = sig[i]
    return Plan(out, sig < 0, sig > 0)


# ---------- ICT ----------

def _ny(df: pd.DataFrame) -> tuple[np.ndarray, np.ndarray]:
    t = pd.to_datetime(df.time, unit="s", utc=True).dt.tz_convert(NY)
    return (t.dt.hour * 60 + t.dt.minute).to_numpy(), t.dt.date.to_numpy()


KILL_ZONES = {"london": (2 * 60, 5 * 60), "ny": (7 * 60, 10 * 60)}  # New York time


def ict_sweep_fvg(df: pd.DataFrame, p: dict[str, Any]) -> Plan:
    """ICT: inside a kill zone (New York time), price sweeps the Asian session high or low (20:00-00:00 NY), then a
    displacement leaves a fair value gap and closes through the latest swing (market structure shift). Enter on a
    limit at the gap (edge or midpoint), stop beyond the sweep extreme; target the opposite Asian extreme or a fixed R.
    One setup per kill zone per day."""
    if p["_bar_s"] > 3600:
        return Plan([])
    mins, day = _ny(df)
    h, lo, c, o = (df[k].to_numpy() for k in ("high", "low", "close", "open"))
    a = atr(df, 14).to_numpy()
    sh, sl, _, _ = swings(df, 2)
    zones = [KILL_ZONES[z] for z in (("london", "ny") if p["zone"] == "both" else (p["zone"],))]
    asia_hi, asia_lo, cur_day, hi_, lo_ = np.nan, np.nan, None, -np.inf, np.inf
    sweep_lo, sweep_hi, done, zone_end = np.inf, -np.inf, set(), 0
    out = []
    for i in range(3, len(df)):
        m = mins[i]
        if m >= 20 * 60:  # Asian range builds 20:00-00:00 NY for the next NY date
            if cur_day != ("asia", day[i]):
                cur_day, hi_, lo_ = ("asia", day[i]), -np.inf, np.inf
            hi_, lo_ = max(hi_, h[i]), min(lo_, lo[i])
            continue
        if cur_day is not None and cur_day[0] == "asia":
            asia_hi, asia_lo, cur_day = hi_, lo_, ("day", day[i])
            sweep_lo, sweep_hi = np.inf, -np.inf
        z = next((zz for zz in zones if zz[0] <= m < zz[1]), None)
        if z is None or np.isnan(asia_hi) or not a[i] > 0:
            continue
        zone_end = z[1]
        sweep_lo, sweep_hi = min(sweep_lo, lo[i]), max(sweep_hi, h[i])
        bars_left = max(1, int((zone_end - m) * 60 // p["_bar_s"]))
        disp = abs(c[i - 1] - o[i - 1]) > a[i]  # the middle candle of the gap is a displacement candle
        key = (day[i], z)
        if sweep_lo < asia_lo and lo[i] > h[i - 2] and c[i - 1] > o[i - 1] and disp and c[i] > sh[i] and (key, 1) not in done:
            top, bot = lo[i], h[i - 2]
            entry = top if p["entry"] == "edge" else (top + bot) / 2
            stop = sweep_lo - 0.1 * a[i]
            tgt = asia_hi if p["target"] == "liquidity" else entry + float(p["target"]) * (entry - stop)
            if entry > stop and tgt > entry:
                out.append(Setup(i, 1, stop, tgt, entry=entry, expiry=bars_left, max_bars=bars_left + int(4 * 3600 // p["_bar_s"])))
                done.add((key, 1))
        if sweep_hi > asia_hi and h[i] < lo[i - 2] and c[i - 1] < o[i - 1] and disp and c[i] < sl[i] and (key, -1) not in done:
            bot, top = h[i], lo[i - 2]
            entry = bot if p["entry"] == "edge" else (top + bot) / 2
            stop = sweep_hi + 0.1 * a[i]
            tgt = asia_lo if p["target"] == "liquidity" else entry - float(p["target"]) * (stop - entry)
            if entry < stop and tgt < entry:
                out.append(Setup(i, -1, stop, tgt, entry=entry, expiry=bars_left, max_bars=bars_left + int(4 * 3600 // p["_bar_s"])))
                done.add((key, -1))
    return Plan(out)


# ---------- SMC ----------

def smc_order_block(df: pd.DataFrame, p: dict[str, Any]) -> Plan:
    """SMC: a close through the latest swing high (low) is a break of structure, BOS, when it goes with the current
    structure, or a change of character, CHoCH, when it reverses it. The order block is the last opposite-colour
    candle before the impulse that broke structure. Enter on a limit at the order block's near edge, stop beyond its
    far edge; optionally only when the impulse left a fair value gap."""
    h, lo, c, o = (df[k].to_numpy() for k in ("high", "low", "close", "open"))
    a = atr(df, 14).to_numpy()
    sh, sl, shi, sli = swings(df, p["swing"])
    trend, used, out = 0, set(), []
    for i in range(1, len(df)):
        if np.isnan(sh[i]) or not a[i] > 0:
            continue
        for side, level, origin in ((1, sh[i], sli[i]), (-1, sl[i], shi[i])):
            broke = c[i] > level >= c[i - 1] if side > 0 else c[i] < level <= c[i - 1]
            if not broke or (side, level) in used:
                continue
            used.add((side, level))
            kind = "bos" if trend == side else "choch"
            trend = side
            if kind == "choch" and p["structure"] == "bos":
                continue
            start = max(0, origin if origin >= 0 else i - 20)
            ob = next((k for k in range(i - 1, start - 1, -1) if (c[k] < o[k] if side > 0 else c[k] > o[k])), None)
            if ob is None:
                continue
            gap = any((lo[k] > h[k - 2]) if side > 0 else (h[k] < lo[k - 2]) for k in range(max(ob + 2, 2), i + 1))
            if p["need_fvg"] and not gap:
                continue
            entry = h[ob] if side > 0 else lo[ob]
            stop = (lo[ob] - 0.1 * a[i]) if side > 0 else (h[ob] + 0.1 * a[i])
            if (entry - stop) * side <= 0 or (c[i] - entry) * side <= 0:
                continue
            out.append(Setup(i, side, stop, _target(side, entry, stop, p["rr"]), entry=entry, expiry=p["expiry"],
                             max_bars=p["expiry"] * 3, tag=kind))
    return Plan(out)


# ---------- ICC ----------

def icc(df: pd.DataFrame, p: dict[str, Any]) -> Plan:
    """ICC (Indication, Correction, Continuation): Indication = a close through the latest swing high (low). Correction
    = price pulls back and prints a swing low (high) that stays above (below) the swing that started the move.
    Continuation = a close beyond the indication's extreme; enter at the next open, stop beyond the correction extreme.
    Optional trend filter: longs only above the 200 EMA, shorts only below. A setup that takes longer than max_bars to
    complete is dropped."""
    h, lo, c = df.high.to_numpy(), df.low.to_numpy(), df.close.to_numpy()
    a = atr(df, 14).to_numpy()
    trend = ema(df.close, 200).to_numpy()
    sh, sl, shi, sli = swings(df, p["swing"])
    out: list[Setup] = []
    state: dict[int, dict[str, Any] | None] = {1: None, -1: None}
    for i in range(1, len(df)):
        if np.isnan(sh[i]) or np.isnan(sl[i]) or not a[i] > 0:
            continue
        for side in (1, -1):
            s = state[side]
            if s is not None:
                beyond = (lambda x, y: x > y) if side > 0 else (lambda x, y: x < y)
                corr_idx, corr_val = (sli[i], sl[i]) if side > 0 else (shi[i], sh[i])
                if beyond(s["floor"], c[i]) or i - s["start"] > p["max_bars"]:
                    state[side] = None  # structure failed or took too long
                elif s["corr"] is None:
                    s["extreme"] = max(s["extreme"], h[i]) if side > 0 else min(s["extreme"], lo[i])
                    if corr_idx > s["start"] and beyond(corr_val, s["floor"]):
                        s["corr"] = corr_val  # correction printed and held structure; the extreme is now fixed
                else:
                    if corr_idx > s["start"] and beyond(corr_val, s["floor"]):
                        s["corr"] = corr_val  # a deeper correction swing that still holds structure
                    if beyond(c[i], s["extreme"]):
                        stop = s["corr"] - side * 0.1 * a[i]
                        ok = not p["trend_filter"] or beyond(c[i], trend[i])
                        if ok and (c[i] - stop) * side > 0:
                            out.append(Setup(i, side, stop, _target(side, c[i], stop, p["rr"]), max_bars=p["max_bars"]))
                        state[side] = None
                continue
            level = sh[i] if side > 0 else sl[i]
            if (c[i] > level >= c[i - 1]) if side > 0 else (c[i] < level <= c[i - 1]):
                state[side] = {"floor": sl[i] if side > 0 else sh[i], "extreme": h[i] if side > 0 else lo[i],
                               "start": i, "corr": None}
    return Plan(out)


# ---------- Ras 1.1: rules from the research in jarvis/lab/ras11.py ----------

def _clock(df: pd.DataFrame, tz: str, shift_s: int = 0) -> tuple[np.ndarray, np.ndarray]:
    """Local hour and weekday of each bar's open plus shift_s (shift by one bar to read the time the bar closes)."""
    t = pd.to_datetime(df.time.to_numpy() + shift_s, unit="s", utc=True).tz_convert(tz)
    return np.asarray(t.hour), np.asarray(t.day_of_week)


def fix_reversal(df: pd.DataFrame, p: dict[str, Any]) -> Plan:
    """The dollar tends to rise into a benchmark fix and fall after it (Krohn, Mueller, Whelan 2024): at the close of
    the bar that ends on the fix, sell dollars for a few hours. Hourly bars can only approximate Tokyo's 09:55."""
    side = -1 if str(df.attrs.get("symbol", "")).startswith("USD_") else 1
    bar_s, a = p["_bar_s"], atr(df, 14).to_numpy()
    lon, lon_day = _clock(df, "Europe/London", bar_s)
    tok, tok_day = _clock(df, "Asia/Tokyo", bar_s)
    at_lon, at_tok = (lon == 16) & (lon_day < 5), (tok == 10) & (tok_day < 5)
    hit = at_lon | at_tok if p["fix"] == "both" else at_lon if p["fix"] == "london" else at_tok
    return Plan([_market(df, i, side, p["atr_stop"] * a[i], None, max_bars=p["hold"], tag=f"after {p['fix']} fix")
                 for i in range(20, len(df)) if hit[i] and a[i] > 0])


def gold_asia_drift(df: pd.DataFrame, p: dict[str, Any]) -> Plan:
    """Gold has tended to rise in Asian hours and give it back later (CBS thesis): buy at the start of the window,
    flat at the end. Long only. Times are UTC, since Asian markets keep no daylight saving."""
    bar_s, a = p["_bar_s"], atr(df, 14).to_numpy()
    hour, day = _clock(df, "UTC", bar_s)
    n = max(1, round((p["end"] - p["start"]) * 3600 / bar_s))
    return Plan([_market(df, i, 1, p["atr_stop"] * a[i], None, max_bars=n, tag="asia drift")
                 for i in range(20, len(df)) if hour[i] == p["start"] and day[i] < 5 and a[i] > 0])


def gold_london_fade(df: pd.DataFrame, p: dict[str, Any]) -> Plan:
    """Gold's returns from the London open to the PM fix have been among the weakest of the day (Zurich thesis): sell
    at the London open, flat after `hold` bars. Short only."""
    bar_s, a = p["_bar_s"], atr(df, 14).to_numpy()
    hour, day = _clock(df, "Europe/London", bar_s)
    return Plan([_market(df, i, -1, p["atr_stop"] * a[i], None, max_bars=p["hold"], tag="london fade")
                 for i in range(20, len(df)) if hour[i] == 8 and day[i] < 5 and a[i] > 0])


def cta_momentum(df: pd.DataFrame, p: dict[str, Any]) -> Plan:
    """One fixed reading of a trader's post: join an existing trend when momentum picks up, tight stop, trail until
    momentum ends. Trend = 50 EMA against 200 EMA; pickup = a close beyond the last 5 bars; trail = the `trail`-bar
    low or high."""
    c, a = df.close.to_numpy(), atr(df, 14).to_numpy()
    fast, slow = ema(df.close, 50).to_numpy(), ema(df.close, 200).to_numpy()
    hi5, lo5 = df.high.rolling(5).max().shift().to_numpy(), df.low.rolling(5).min().shift().to_numpy()
    out = []
    for i in range(200, len(df)):
        side = 1 if fast[i] > slow[i] and c[i] > hi5[i] >= c[i - 1] else -1 if fast[i] < slow[i] and c[i] < lo5[i] <= c[i - 1] else 0
        if side and a[i] > 0:
            out.append(_market(df, i, side, p["atr_stop"] * a[i], None))
    low, high = df.low.rolling(p["trail"]).min().shift().to_numpy(), df.high.rolling(p["trail"]).max().shift().to_numpy()
    return Plan(out, c < low, c > high)


# ---------- Legends: fixed readings of famous traders' methods (jarvis/lab/legends.py) ----------

def simons_reversal(df: pd.DataFrame, p: dict[str, Any]) -> Plan:
    """Short-term reversal, the kind of edge Renaissance is reported to have started with: after a sharp 4-bar move of
    more than k ATR, bet on part of it coming back; out after `hold` bars."""
    c, a = df.close.to_numpy(), atr(df, 14).to_numpy()
    move = c - np.r_[np.full(4, np.nan), c[:-4]]
    out = []
    for i in range(20, len(df)):
        if a[i] > 0 and abs(move[i]) > p["k"] * a[i]:
            out.append(_market(df, i, -1 if move[i] > 0 else 1, 2 * a[i], None, max_bars=p["hold"]))
    return Plan(out)


def ptj_200day(df: pd.DataFrame, p: dict[str, Any]) -> Plan:
    """Paul Tudor Jones: be long above the 200-day average and out (here: short) below it; look for 5-to-1 trades."""
    sma = df.close.rolling(200).mean()
    a = atr(df, 14).to_numpy()
    up, dn = cross_up(df.close, sma), cross_down(df.close, sma)
    out = [_market(df, i, 1 if up[i] else -1, p["atr_stop"] * a[i], p["rr"]) for i in range(200, len(df)) if (up[i] or dn[i]) and a[i] > 0]
    return Plan(out, dn, up)


def livermore_highs(df: pd.DataFrame, p: dict[str, Any]) -> Plan:
    """Jesse Livermore: act when price makes a new high (or low) for the period, stop at the last pivot, stay in while
    the line of least resistance holds (exit on a close beyond the 20-bar opposite extreme)."""
    c = df.close.to_numpy()
    hi = df.high.rolling(p["period"]).max().shift().to_numpy()
    lo = df.low.rolling(p["period"]).min().shift().to_numpy()
    lo20, hi20 = df.low.rolling(20).min().shift().to_numpy(), df.high.rolling(20).max().shift().to_numpy()
    out = []
    for i in range(p["period"] + 1, len(df)):
        if c[i] > hi[i] >= c[i - 1] and lo20[i] < c[i]:
            out.append(Setup(i, 1, float(lo20[i]), None))
        elif c[i] < lo[i] <= c[i - 1] and hi20[i] > c[i]:
            out.append(Setup(i, -1, float(hi20[i]), None))
    return Plan(out, c < lo20, c > hi20)


def druckenmiller_pullback(df: pd.DataFrame, p: dict[str, Any]) -> Plan:
    """A fixed stand-in for Druckenmiller's 'find the trend, size up, cut fast': in a 50/200 EMA trend, enter when a
    pullback touches the 20 EMA and closes back with the trend; 2 ATR stop; exit on a close through the 50 EMA or at a
    fixed R."""
    c, a = df.close.to_numpy(), atr(df, 14).to_numpy()
    e20, e50, e200 = (ema(df.close, n).to_numpy() for n in (20, 50, 200))
    lo, hi = df.low.to_numpy(), df.high.to_numpy()
    out = []
    for i in range(200, len(df)):
        if not a[i] > 0:
            continue
        if e50[i] > e200[i] and lo[i] <= e20[i] < c[i]:
            out.append(_market(df, i, 1, 2 * a[i], p["rr"]))
        elif e50[i] < e200[i] and hi[i] >= e20[i] > c[i]:
            out.append(_market(df, i, -1, 2 * a[i], p["rr"]))
    return Plan(out, c < e50, c > e50)


def soros_squeeze(df: pd.DataFrame, p: dict[str, Any]) -> Plan:
    """A fixed stand-in for Soros's boom-bust trades: a long quiet spell (Bollinger width at its lowest in `lookback`
    bars) that breaks out of the band; ride it until a close back through the 20-bar average or a fixed R."""
    mid, sd = df.close.rolling(20).mean(), df.close.rolling(20).std()
    width = (4 * sd / mid).to_numpy()
    quiet = width <= pd.Series(width).rolling(p["lookback"]).min().to_numpy() * 1.05
    c, a, m = df.close.to_numpy(), atr(df, 14).to_numpy(), mid.to_numpy()
    upper, lower = (mid + 2 * sd).to_numpy(), (mid - 2 * sd).to_numpy()
    out, armed = [], -10**9
    for i in range(p["lookback"] + 20, len(df)):
        if quiet[i - 1]:
            armed = i
        if i - armed <= 10 and a[i] > 0 and (c[i] > upper[i] or c[i] < lower[i]):
            out.append(_market(df, i, 1 if c[i] > upper[i] else -1, 2 * a[i], p["rr"]))
            armed = -10**9
    return Plan(out, c < m, c > m)


def vwap_ema9(df: pd.DataFrame, p: dict[str, Any]) -> Plan:
    """A reel's 3-minute rule: buy when the 9 EMA crosses above the day's VWAP, sell when it crosses below. The reel gives no
    stop or exit, so: stop 1.5 ATR, exit at a fixed R or on the opposite cross. VWAP uses Capital.com tick volume (forex has
    no central volume). 'ny' trades the New York stock session only (VWAP from 09:30, flat by 16:00), as in the reel;
    'fx' anchors VWAP at the 17:00 New York rollover and trades around the clock."""
    t = pd.to_datetime(df.time, unit="s", utc=True).dt.tz_convert(NY)
    mins = (t.dt.hour * 60 + t.dt.minute).to_numpy()
    if p["session"] == "ny":
        inside = (mins >= 570) & (mins < 960)
        key = pd.Series(np.where(inside, t.dt.strftime("%Y-%m-%d"), None), index=df.index)
    else:
        inside = np.ones(len(df), bool)
        key = (t + pd.Timedelta(hours=7)).dt.strftime("%Y-%m-%d")
    v = df.volume.clip(lower=1).astype(float).where(inside)
    tp = (df.high + df.low + df.close) / 3
    vwap = ((tp * v).groupby(key).cumsum() / v.groupby(key).cumsum()).where(inside)
    e9 = ema(df.close, 9)
    same = (key == key.shift()).to_numpy() & inside
    up, dn = cross_up(e9, vwap) & same, cross_down(e9, vwap) & same
    a = atr(df, 14).to_numpy()
    bar_min = p["_bar_s"] / 60
    out = []
    for i in range(30, len(df)):
        if not (up[i] or dn[i]) or not a[i] > 0:
            continue
        kw: dict[str, Any] = {}
        if p["session"] == "ny":
            if mins[i] >= 930:  # no new entries in the last half hour
                continue
            kw["max_bars"] = max(1, int((960 - mins[i] - bar_min) // bar_min))  # out by the 16:00 close
        out.append(_market(df, i, 1 if up[i] else -1, p["atr_stop"] * a[i], p["rr"], **kw))
    return Plan(out, dn, up)


# ---------- Books: Elder's Trading for a Living, Murphy's Technical Analysis (jarvis/lab/books.py) ----------

def _weekly_tide(df: pd.DataFrame) -> np.ndarray:
    """Elder's first screen: the slope of weekly MACD-Histogram (12, 26, 9) between its two latest finished weeks.
    +1 up, -1 down, 0 unknown. A week counts only once the next week has started (no peeking at an open week)."""
    wk = pd.to_datetime(df.time, unit="s").dt.to_period("W-FRI")  # UTC, naive
    closes = df.close.groupby(wk).last()
    macd = ema(closes, 12) - ema(closes, 26)
    hist = macd - ema(macd, 9)
    slope = np.sign(hist.diff()).shift(1)  # the slope known during the following week
    return wk.map(slope).fillna(0).to_numpy()


def elder_triple_screen(df: pd.DataFrame, p: dict[str, Any]) -> Plan:
    """Triple Screen: weekly MACD-Histogram slope sets the side (screen 1); the 2-day EMA of Force Index going against
    it on the daily chart marks the wave (screen 2); a trailing buy-stop one tick above the day's high enters (screen 3).
    Stop below the lower of the last two days' lows; out when the weekly tide turns, or at a fixed R."""
    tide = _weekly_tide(df)
    fi2 = ema(df.volume.astype(float) * df.close.diff(), 2).to_numpy()
    hi, lo, a = df.high.to_numpy(), df.low.to_numpy(), atr(df, 14).to_numpy()
    out = []
    for i in range(60, len(df)):
        tick = 0.02 * a[i]
        if tide[i] > 0 and fi2[i] < 0:
            e, st = hi[i] + tick, min(lo[i], lo[i - 1]) - tick
            out.append(Setup(i, 1, st, _target(1, e, st, p["rr"]), entry=e, expiry=1, stop_entry=True))
        elif tide[i] < 0 and fi2[i] > 0:
            e, st = lo[i] - tick, max(hi[i], hi[i - 1]) + tick
            out.append(Setup(i, -1, st, _target(-1, e, st, p["rr"]), entry=e, expiry=1, stop_entry=True))
    return Plan(out, tide < 0, tide > 0)


def elder_ray(df: pd.DataFrame, p: dict[str, Any]) -> Plan:
    """Elder-ray: Bull Power = high - 13 EMA, Bear Power = low - 13 EMA. Buy when the trend is up and Bear Power is
    negative but rising, with a buy-stop above the last two days' highs and a stop below the latest minor low; short the
    mirror image. Trend: the 13 EMA's slope (stand-alone) or the weekly tide (as in Triple Screen)."""
    e13 = ema(df.close, 13).to_numpy()
    hi, lo, a = df.high.to_numpy(), df.low.to_numpy(), atr(df, 14).to_numpy()
    bull, bear = hi - e13, lo - e13
    trend = _weekly_tide(df) if p["trend"] == "weekly" else np.sign(np.r_[0.0, np.diff(e13)])
    out = []
    for i in range(30, len(df)):
        tick = 0.02 * a[i]
        if trend[i] > 0 and bear[i] < 0 and bear[i] > bear[i - 1]:
            e, st = max(hi[i], hi[i - 1]) + tick, lo[i - 4:i + 1].min() - tick
            out.append(Setup(i, 1, st, _target(1, e, st, p["rr"]), entry=e, expiry=1, stop_entry=True))
        elif trend[i] < 0 and bull[i] > 0 and bull[i] < bull[i - 1]:
            e, st = min(lo[i], lo[i - 1]) - tick, hi[i - 4:i + 1].max() + tick
            out.append(Setup(i, -1, st, _target(-1, e, st, p["rr"]), entry=e, expiry=1, stop_entry=True))
    return Plan(out, trend < 0, trend > 0)


def elder_channel(df: pd.DataFrame, p: dict[str, Any]) -> Plan:
    """Channel trading as the professionals do it (Elder, after Appel): a 22-day EMA with a channel sized to hold about
    95% of the last 100 days. When the channel is fairly flat, buy a poke below the lower line that closes back inside
    and sell a poke above the upper line; target the EMA (value) or the far line; stop 1 ATR beyond the poke."""
    e = ema(df.close, 22)
    coef = ((df.close - e).abs() / e).rolling(100).quantile(0.95).shift(1).to_numpy()
    em, c, hi, lo, a = e.to_numpy(), df.close.to_numpy(), df.high.to_numpy(), df.low.to_numpy(), atr(df, 14).to_numpy()
    up, dn = em * (1 + coef), em * (1 - coef)
    out = []
    for i in range(120, len(df)):
        if not (a[i] > 0 and coef[i] > 0):
            continue
        flat = abs(em[i] - em[i - 5]) < 0.25 * coef[i] * em[i]
        if not flat:
            continue
        if lo[i] < dn[i] < c[i]:
            st = lo[i] - a[i]
            out.append(Setup(i, 1, st, em[i] if p["target"] == "value" else up[i], max_bars=20))
        elif hi[i] > up[i] > c[i]:
            st = hi[i] + a[i]
            out.append(Setup(i, -1, st, em[i] if p["target"] == "value" else dn[i], max_bars=20))
    return Plan([s for s in out if (s.target - df.close.iat[s.i]) * s.side > 0])


def murphy_triple_ma(df: pd.DataFrame, p: dict[str, Any]) -> Plan:
    """Murphy's triple crossover (4, 9 and 18 days): buy when the 4 > 9 > 18 line-up forms, sell short when 4 < 9 < 18
    forms; out when the 4-day crosses back through the 9-day. ATR stop (the book leaves the stop to the trader)."""
    s4, s9, s18 = (df.close.rolling(n).mean() for n in (4, 9, 18))
    bull = ((s4 > s9) & (s9 > s18)).to_numpy()
    bear = ((s4 < s9) & (s9 < s18)).to_numpy()
    a = atr(df, 14).to_numpy()
    out = [_market(df, i, 1 if bull[i] else -1, p["atr_stop"] * a[i], None) for i in range(20, len(df))
           if a[i] > 0 and ((bull[i] and not bull[i - 1]) or (bear[i] and not bear[i - 1]))]
    return Plan(out, cross_down(s4, s9), cross_up(s4, s9))


def murphy_double_ma(df: pd.DataFrame, p: dict[str, Any]) -> Plan:
    """Murphy's double crossover with simple averages (5 and 20, or 10 and 50 days): buy when the shorter crosses above
    the longer, sell short on the cross below, always out on the opposite cross. ATR stop."""
    f, sl = df.close.rolling(p["fast"]).mean(), df.close.rolling(p["slow"]).mean()
    up, dn, a = cross_up(f, sl), cross_down(f, sl), atr(df, 14).to_numpy()
    out = [_market(df, i, 1 if up[i] else -1, p["atr_stop"] * a[i], None) for i in range(p["slow"], len(df)) if (up[i] or dn[i]) and a[i] > 0]
    return Plan(out, dn, up)


def four_week_rule(df: pd.DataFrame, p: dict[str, Any]) -> Plan:
    """Donchian's four-week rule as Murphy teaches it: buy a close above the last four weeks' high, sell short a close
    below the last four weeks' low, and reverse on the opposite signal, so it is always in the market. The stop is the
    opposite four-week extreme, where the reversal would come."""
    n = p["weeks"] * 5
    hi = df.high.rolling(n).max().shift().to_numpy()
    lo = df.low.rolling(n).min().shift().to_numpy()
    c = df.close.to_numpy()
    long_, short_ = c > hi, c < lo
    out = []
    for i in range(n + 1, len(df)):
        if long_[i] and not long_[i - 1] and lo[i] < c[i]:
            out.append(Setup(i, 1, float(lo[i]), None))
        elif short_[i] and not short_[i - 1] and hi[i] > c[i]:
            out.append(Setup(i, -1, float(hi[i]), None))
    return Plan(out, short_, long_)


def rsi_failure_swing(df: pd.DataFrame, p: dict[str, Any]) -> Plan:
    """Wilder's failure swing, from Murphy's oscillator lesson. Bottom: RSI falls under 30, bounces to a peak, dips but
    holds above 30, then breaks that peak: buy. Top: the mirror above 70. Stop at the swing's price extreme; target a
    fixed R, and out after 40 days at most."""
    r = rsi(df.close, 14).to_numpy()
    hi, lo = df.high.to_numpy(), df.low.to_numpy()
    out = []
    state, peak, ext = 0, 0.0, 0.0  # +1 watching a bottom, -1 a top; peak = the RSI level that must break
    dipped = False
    for i in range(15, len(df)):
        if r[i] < 30:
            state, peak, ext, dipped = 1, r[i], lo[i], False
            continue
        if r[i] > 70:
            state, peak, ext, dipped = -1, r[i], hi[i], False
            continue
        if state == 1:
            ext = min(ext, lo[i])
            if not dipped:
                if r[i] < r[i - 1] and r[i - 1] >= peak:
                    peak, dipped = r[i - 1], True
                else:
                    peak = max(peak, r[i])
            elif r[i] > peak:
                out.append(_setup_at(df, i, 1, ext, p["rr"]))
                state = 0
        elif state == -1:
            ext = max(ext, hi[i])
            if not dipped:
                if r[i] > r[i - 1] and r[i - 1] <= peak:
                    peak, dipped = r[i - 1], True
                else:
                    peak = min(peak, r[i])
            elif r[i] < peak:
                out.append(_setup_at(df, i, -1, ext, p["rr"]))
                state = 0
    return Plan([s for s in out if s is not None])


def _setup_at(df: pd.DataFrame, i: int, side: int, stop: float, rr: float | None) -> Setup | None:
    c = float(df.close.iat[i])
    if (c - stop) * side <= 0:
        return None
    return Setup(i, side, float(stop), _target(side, c, stop, rr), max_bars=40)


# ---------- catalog ----------

CATALOG: dict[str, dict[str, Any]] = {
    "ema_cross": {"group": "Indicators", "name": "EMA crossover", "fn": ema_cross, "tf": "HOUR_4",
                  "grid": {"fast_slow": [(9, 21), (20, 50), (50, 200)], "rr": [2.0, 3.0, None]},
                  "rules": "Buy when the fast EMA crosses above the slow EMA, sell on the cross below. Stop 2 ATR. Exit at a "
                           "fixed R, or on the opposite cross."},
    "macd_trend": {"group": "Indicators", "name": "MACD with 200 EMA", "fn": macd_trend, "tf": "HOUR",
                   "grid": {"rr": [1.5, 2.0, 3.0]},
                   "rules": "MACD(12,26,9) histogram crosses zero in the direction of the 200 EMA. Stop 2 ATR, fixed R target."},
    "rsi_pullback": {"group": "Indicators", "name": "RSI pullback", "fn": rsi_pullback, "tf": "HOUR",
                     "grid": {"period_level": [(2, 10), (14, 30)], "atr_stop": [2.0, 3.0], "max_bars": [10, 30]},
                     "rules": "Above the 200 EMA, buy when RSI crosses back above the oversold level (below it, sell from "
                              "overbought). Exit when RSI is back at 50, at the stop (ATR), or after a time limit."},
    "bollinger_reversion": {"group": "Indicators", "name": "Bollinger reversion", "fn": bollinger_reversion, "tf": "HOUR",
                            "grid": {"k": [2.0, 2.5], "range_only": [True, False]},
                            "rules": "A close back inside the 20-bar Bollinger band after a close outside it. Target the "
                                     "middle band, stop 1.5 ATR. Optional: only when ADX < 25 (ranging market)."},
    "donchian_breakout": {"group": "Indicators", "name": "Donchian breakout (Turtle)", "fn": donchian_breakout, "tf": "DAY",
                          "grid": {"entry_exit": [(20, 10), (55, 20)]},
                          "rules": "Close above the N-bar high buys, below the N-bar low sells. Stop 2 ATR. Exit on a close "
                                   "beyond the opposite M-bar channel."},
    "trend_following": {"group": "Trend following", "name": "Time-series momentum", "fn": trend_following, "tf": "DAY",
                        "grid": {"lookback": ["blend", "12m"], "atr_stop": [3.0, 5.0]},
                        "rules": "Hold the direction of the past 1/3/6/12-month return (or 12 months only); flip when it "
                                 "changes. The ATR stop sizes the trade (volatility scaling) and caps a disaster."},
    "ict_sweep_fvg": {"group": "ICT", "name": "Kill zone sweep + FVG", "fn": ict_sweep_fvg, "tf": "MINUTE_15",
                      "grid": {"zone": ["london", "ny", "both"], "entry": ["edge", "ce"], "target": ["liquidity", 2.0]},
                      "rules": "In the London (02-05) or New York (07-10) kill zone, New York time: price sweeps the Asian "
                               "range high/low, then a displacement candle leaves a fair value gap and closes through the "
                               "last swing (market structure shift). Limit entry at the FVG edge or midpoint (CE), stop "
                               "beyond the sweep, target the opposite Asian extreme or 2R."},
    "smc_order_block": {"group": "SMC", "name": "BOS/CHoCH + order block", "fn": smc_order_block, "tf": "HOUR",
                        "grid": {"structure": ["bos", "both"], "need_fvg": [False, True], "rr": [2.0, 3.0]},
                        "rules": "A close through the latest swing high/low breaks structure (BOS with the trend, CHoCH "
                                 "against it). Limit entry at the last opposite candle before the impulse (order block), "
                                 "stop beyond it, fixed R target. Optional: the impulse must leave a fair value gap."},
    "icc": {"group": "ICC", "name": "Indication · Correction · Continuation", "fn": icc, "tf": "HOUR",
            "grid": {"rr": [1.5, 2.0, 3.0], "trend_filter": [False, True]},
            "rules": "Indication: a close through the latest swing. Correction: a pullback swing that holds above the "
                     "move's origin. Continuation: a close beyond the indication's extreme - enter, stop beyond the "
                     "correction, fixed R target. Optional 200 EMA trend filter."},
    "fix_reversal": {"group": "Ras 1.1", "name": "Dollar fix reversal", "fn": fix_reversal, "tf": "HOUR",
                     "grid": {"fix": ["london", "tokyo", "both"], "hold": [2, 4, 6], "atr_stop": [2.0, 3.0]},
                     "rules": "At the close of the bar ending on the London 4pm or Tokyo 10am fix, sell dollars for a few "
                              "hours: buy EUR/USD and GBP/USD, sell USD/JPY. ATR stop."},
    "gold_asia_drift": {"group": "Ras 1.1", "name": "Gold Asian-hours drift", "fn": gold_asia_drift, "tf": "HOUR",
                        "grid": {"window": [(0, 6), (0, 7), (1, 7)], "atr_stop": [2.0, 3.0]},
                        "rules": "Buy gold at the start of Asian hours and exit before London (UTC window). Long only, "
                                 "ATR stop."},
    "gold_london_fade": {"group": "Ras 1.1", "name": "Gold London fade", "fn": gold_london_fade, "tf": "HOUR",
                         "grid": {"hold": [4, 7], "atr_stop": [2.0, 3.0]},
                         "rules": "Sell gold at the London open and exit a few hours later, before or around the PM fix. "
                                  "Short only, ATR stop."},
    "cta_momentum": {"group": "Ras 1.1", "name": "With-trend momentum pickup", "fn": cta_momentum, "tf": "DAY",
                     "grid": {"trail": [5, 10, 20], "atr_stop": [1.5, 2.5]},
                     "rules": "With the 50 EMA on the trend's side of the 200 EMA, enter on a close beyond the last 5 bars. "
                              "ATR stop, then exit on a close beyond the trailing low or high."},
    "vwap_ema9": {"group": "Ras 1.1", "name": "VWAP + 9 EMA cross (3 minute)", "fn": vwap_ema9, "tf": "MINUTE_3",
                  "grid": {"session": ["ny", "fx"], "rr": [1.5, None]},
                  "rules": "On 3-minute bars, buy when the 9 EMA crosses above the day's VWAP and sell when it crosses below. "
                           "Stop 1.5 ATR, exit at 1.5R or on the opposite cross. Either the New York stock session only "
                           "(flat by 16:00) or around the clock."},
    "elder_triple_screen": {"group": "Books", "name": "Elder: Triple Screen", "fn": elder_triple_screen, "tf": "DAY",
                            "grid": {"rr": [None, 3.0]},
                            "rules": "Weekly MACD-Histogram slope sets the side; the 2-day EMA of Force Index going the other way on "
                                     "the daily chart marks the pullback; a buy-stop above the day's high (sell-stop below its low) "
                                     "enters. Stop beyond the last two days; out when the weekly slope turns, or at 3R."},
    "elder_ray": {"group": "Books", "name": "Elder: Elder-ray", "fn": elder_ray, "tf": "DAY",
                  "grid": {"trend": ["ema", "weekly"], "rr": [None, 2.0]},
                  "rules": "Trend from the 13-day EMA slope (or the weekly MACD-Histogram). In an uptrend, buy when Bear Power "
                           "(low minus the 13 EMA) is negative but rising, with a buy-stop above the last two days' highs and a "
                           "stop below the 5-day low; shorts mirror. Out when the trend turns, or at 2R."},
    "elder_channel": {"group": "Books", "name": "Elder: fade the channel", "fn": elder_channel, "tf": "DAY",
                      "grid": {"target": ["value", "far line"]},
                      "rules": "22-day EMA with a channel holding about 95% of recent prices. When the channel is flat, buy a "
                               "poke below the lower line that closes back inside, sell a poke above the upper line. Target the "
                               "EMA or the far line; stop 1 ATR beyond the poke; out after 20 days."},
    "murphy_triple_ma": {"group": "Books", "name": "Murphy: 4-9-18 triple crossover", "fn": murphy_triple_ma, "tf": "DAY",
                         "grid": {"atr_stop": [2.0, 3.0]},
                         "rules": "Buy when the 4-, 9- and 18-day averages line up upward, sell short when they line up downward; "
                                  "out when the 4-day crosses back through the 9-day. ATR stop."},
    "murphy_double_ma": {"group": "Books", "name": "Murphy: double crossover", "fn": murphy_double_ma, "tf": "DAY",
                         "grid": {"fast_slow": [(5, 20), (10, 50)], "atr_stop": [2.0, 3.0]},
                         "rules": "Buy when the 5-day crosses above the 20-day (or 10 over 50), sell short on the cross below, out "
                                  "on the opposite cross. ATR stop."},
    "four_week_rule": {"group": "Books", "name": "Murphy: the four-week rule", "fn": four_week_rule, "tf": "DAY",
                       "grid": {"weeks": [4]},
                       "rules": "Buy a close above the four-week high, sell short a close below the four-week low, and reverse "
                                "on the opposite signal. Stop at the opposite four-week extreme."},
    "rsi_failure_swing": {"group": "Books", "name": "Murphy: RSI failure swing", "fn": rsi_failure_swing, "tf": "DAY",
                          "grid": {"rr": [2.0, 3.0]},
                          "rules": "RSI(14) falls under 30, bounces, dips but holds above 30, then breaks its bounce high: buy. "
                                   "The mirror above 70 sells. Stop at the swing's extreme; target 2R or 3R; out after 40 days."},
    "simons_reversal": {"group": "Legends", "name": "Jim Simons: short-term reversal", "fn": simons_reversal, "tf": "HOUR",
                        "grid": {"k": [2.0, 3.0], "hold": [4, 8]},
                        "rules": "After a 4-bar move larger than k ATR, trade against it. 2 ATR stop, out after a few bars."},
    "ptj_200day": {"group": "Legends", "name": "Paul Tudor Jones: 200-day line, 5 to 1", "fn": ptj_200day, "tf": "DAY",
                   "grid": {"rr": [5.0, None], "atr_stop": [1.5, 3.0]},
                   "rules": "Buy when price closes above the 200-day average, sell when it closes below. ATR stop, target 5 "
                            "times the risk or the opposite cross."},
    "livermore_highs": {"group": "Legends", "name": "Jesse Livermore: new highs", "fn": livermore_highs, "tf": "DAY",
                        "grid": {"period": [120, 250]},
                        "rules": "Buy a close at a new 6- or 12-month high (sell a new low). Stop at the 20-day low (high); "
                                 "exit on a close beyond it."},
    "druckenmiller_pullback": {"group": "Legends", "name": "Stanley Druckenmiller: trend pullback", "fn": druckenmiller_pullback, "tf": "DAY",
                               "grid": {"rr": [3.0, None]},
                               "rules": "With the 50 EMA on the trend's side of the 200 EMA, enter when price dips to the 20 EMA "
                                        "and closes back above it. 2 ATR stop, exit at 3R or on a close through the 50 EMA."},
    "soros_squeeze": {"group": "Legends", "name": "George Soros: quiet spell breakout", "fn": soros_squeeze, "tf": "DAY",
                      "grid": {"lookback": [60, 120], "rr": [3.0, None]},
                      "rules": "After the Bollinger band is at its narrowest in months, trade the first close outside it. "
                               "2 ATR stop, exit at 3R or on a close back through the 20-day average."},
}
FIXED = {"ema_cross": {}, "macd_trend": {}, "rsi_pullback": {}, "bollinger_reversion": {}, "donchian_breakout": {},
         "trend_following": {}, "ict_sweep_fvg": {}, "smc_order_block": {"swing": 3, "expiry": 24},
         "icc": {"swing": 3, "max_bars": 96}, "fix_reversal": {}, "gold_asia_drift": {}, "gold_london_fade": {},
         "cta_momentum": {}, "simons_reversal": {}, "ptj_200day": {}, "livermore_highs": {}, "druckenmiller_pullback": {},
         "soros_squeeze": {}, "vwap_ema9": {"atr_stop": 1.5}, "elder_triple_screen": {}, "elder_ray": {}, "elder_channel": {},
         "murphy_triple_ma": {}, "murphy_double_ma": {}, "four_week_rule": {}, "rsi_failure_swing": {}}
PAIRS = {"fast_slow": ("fast", "slow"), "period_level": ("period", "level"), "entry_exit": ("entry", "exit"), "window": ("start", "end")}


def combos(key: str) -> list[dict[str, Any]]:
    """Every parameter combination for a strategy (paired grid keys are unpacked)."""
    grid = CATALOG[key]["grid"]
    out = []
    for vals in product(*grid.values()):
        p = dict(FIXED[key])
        for k, v in zip(grid, vals):
            p.update(dict(zip(PAIRS[k], v)) if k in PAIRS else {k: v})
        out.append(p)
    return out


def label(p: dict[str, Any]) -> str:
    return ", ".join(f"{k}={'exit rule' if v is None else v}" for k, v in p.items() if not k.startswith("_"))
