"""TEST 101 trading rules, pure functions on 5-minute bars, written from the trader-* skills:

  scanner      daily tags on closes (20-day closing-high breakout, bull flag, reclaim of the 20-day average, oversold,
               oversold bounce) and a liquidity floor; the intraday scan is the entry rule itself
  entry        15-minute opening range (9:30, 9:35, 9:40 bars); the first COMPLETED bar after it that CLOSES above the
               range high, between 9:45 and 15:30 New York. One trigger per stock per day. Long only.
  exit         stop at the range midpoint rounded down to the cent; target at `target_r` x risk (2R), rounded half up;
               flat by 15:55 (12:55 on an early close). Refused when the risk is under 0.05% of the price.
  time machine replays the rest of the day from the stock's own past full sessions (same time of day, +/- 2 bars),
               stop checked first; the skill's verdict. Ras adds one rule the skill leaves to you: when the verdict
               leans toward the stop, the trade is skipped (`tm_gate`).
  budget       whole shares, the smallest of max loss / risk, position cap, max order, cash after the buffer
  risk         stop required, max loss, reward:risk, daily loss, trades per day, entry window, one trade per stock,
               halts; sized at the HIGHER of the planned entry and the fill price
  broker       paper only, inside Ras: a market fill at the next bar's open plus slippage; the stop fills at the stop or
               the open if it gaps through; the target at the target; the rest at the 15:50 bar's close
Same bars and settings: the same trades, live or replayed."""
import math
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import numpy as np

NY = ZoneInfo("America/New_York")
BAR = 300

# NYSE calendar from nyse.com (as carried by trader-scanner, read 2026-10-13)
HOLIDAYS = {"2026-01-01", "2026-01-19", "2026-02-16", "2026-04-03", "2026-05-25", "2026-06-19", "2026-07-03", "2026-09-07",
            "2026-11-26", "2026-12-25", "2027-01-01", "2027-01-18", "2027-02-15", "2027-03-26", "2027-05-31", "2027-06-18",
            "2027-07-05", "2027-09-06", "2027-11-25", "2027-12-24"}
EARLY = {"2026-11-27", "2026-12-24", "2027-11-26"}

DEFAULTS: dict[str, Any] = {
    "start_balance": 5000.0, "budget": 5000.0, "cash_buffer_pct": 10.0, "max_position_pct": 75.0, "max_order_usd": 4000.0,
    "min_order_usd": 5.0, "max_loss_per_trade_usd": 50.0, "max_daily_loss_usd": 150.0, "max_trades_per_day": 3,
    "min_reward_risk": 1.0, "no_entries_before": "09:45", "no_entries_after": "15:30", "flat_by": "15:55",
    "or_minutes": 15, "target_r": 2.0, "tm_gate": True, "replays": 10_000, "seed": 19, "slippage_pct": 0.02,
    "min_price": 5.0, "min_volume": 1_000_000, "scan_filter": False,
    "universe": ["AAPL", "MSFT", "NVDA", "AMZN", "META", "GOOGL", "TSLA", "AMD", "AVGO", "NFLX", "PLTR", "COIN", "MU", "INTC",
                 "ORCL", "CRM", "UBER", "SHOP", "SNOW", "BA", "DIS", "JPM", "BAC", "XOM", "CVX", "WMT", "COST", "KO", "PFE", "F",
                 "GM", "NKE", "SBUX", "HD", "SOFI", "RIVN", "MARA", "HOOD", "SPY", "QQQ"],
}


def trading_day(d: date) -> bool:
    return d.weekday() < 5 and d.isoformat() not in HOLIDAYS


def session(d: date) -> tuple[int, int]:
    """Open and close of the NYSE regular session, as UTC timestamps."""
    close = time(13) if d.isoformat() in EARLY else time(16)
    return (int(datetime.combine(d, time(9, 30), NY).timestamp()), int(datetime.combine(d, close, NY).timestamp()))


def at(d: date, hhmm: str) -> int:
    h, m = map(int, hhmm.split(":"))
    return int(datetime.combine(d, time(h, m), NY).timestamp())


def clock(d: date, cfg: dict[str, Any]) -> dict[str, int]:
    """The day's times. On a 1 p.m. close the entry cutoff and flat-by time move up by three hours, as in the skills."""
    o, c = session(d)
    shift = 3 * 3600 if d.isoformat() in EARLY else 0
    return {"open": o, "close": c, "or_end": o + cfg["or_minutes"] * 60, "first": at(d, cfg["no_entries_before"]),
            "last": at(d, cfg["no_entries_after"]) - shift, "flat": at(d, cfg["flat_by"]) - shift, "bars": (c - o) // BAR}


@dataclass
class Day:
    """One stock's regular-session bars for one day; `t` is each bar's start. `live_open` is the open of a bar still
    forming (live only): where a market order sent now would start."""
    d: date
    t: np.ndarray
    o: np.ndarray
    h: np.ndarray
    l: np.ndarray
    c: np.ndarray
    v: np.ndarray
    live_open: float | None = None

    def full(self) -> bool:
        o, c = session(self.d)
        return len(self.t) == (c - o) // BAR and int(self.t[0]) == o and bool(np.all(np.diff(self.t) == BAR))


def cents_down(x: float) -> float:
    return math.floor(round(x * 100, 6)) / 100


def cents_half_up(x: float) -> float:
    return math.floor(x * 100 + 0.5) / 100


# ---------- scanner: daily tags on closes ----------

def _rsi(c: np.ndarray, n: int = 14) -> np.ndarray:
    d = np.diff(c)
    up, dn = np.clip(d, 0, None), np.clip(-d, 0, None)
    out = np.full(len(c), np.nan)
    if len(d) < n:
        return out
    au, ad = up[:n].mean(), dn[:n].mean()
    for i in range(n, len(d) + 1):
        if i > n:
            au, ad = (au * (n - 1) + up[i - 1]) / n, (ad * (n - 1) + dn[i - 1]) / n
        out[i] = 100.0 if ad == 0 else 100 - 100 / (1 + au / ad)
    return out


def scan_tags(closes: np.ndarray) -> list[str]:
    """trader-scanner's daily setups, on daily closes up to yesterday."""
    tags = []
    if len(closes) >= 21 and closes[-1] > closes[-21:-1].max():
        tags.append("breakout_20d")
    if len(closes) >= 16 and closes[-6] > 1.10 * closes[-16] and closes[-5:].max() <= 1.05 * closes[-5:].min():
        tags.append("bull_flag")
    if len(closes) >= 21:
        ma = np.convolve(closes, np.ones(20) / 20, "valid")
        if closes[-2] < ma[-2] and closes[-1] > ma[-1]:
            tags.append("reclaim_ma20")
    r = _rsi(closes)
    if len(closes) >= 16 and r[-1] < 30:
        tags.append("oversold")
    if len(closes) >= 16 and r[-2] < 30 and closes[-1] > closes[-2]:
        tags.append("oversold_bounce")
    return tags


# ---------- entry ----------

def entry(day: Day, cfg: dict[str, Any], now: int | None = None) -> dict[str, Any]:
    """trader-entry's opening-range breakout. Status: closed, forming, incomplete, waiting, triggered, expired."""
    k = clock(day.d, cfg)
    if not len(day.t) or int(day.t[0]) != k["open"]:
        return {"status": "no data"}
    done = now if now is not None else k["close"]
    n = int(np.sum(day.t + BAR <= done))
    if n and not np.all(np.diff(day.t[:n]) == BAR):
        return {"status": "incomplete", "why": "a missing bar (a halt or a thin stock)"}
    n_or = (k["or_end"] - k["open"]) // BAR
    if n < n_or:
        return {"status": "forming"}
    hi, lo = float(day.h[:n_or].max()), float(day.l[:n_or].min())
    out = {"or_high": hi, "or_low": lo}
    for i in range(n_or, n):
        if day.c[i] > hi:
            closes = int(day.t[i]) + BAR
            if closes < k["first"] or closes > k["last"]:
                return {**out, "status": "expired", "why": "first close above the range came outside the entry window", "i": i}
            return {**out, "status": "triggered", "i": i, "time": closes, "price": float(day.c[i])}
    last = float(day.c[n - 1])
    if done >= k["last"]:
        return {**out, "status": "expired", "why": "no close above the range by the cutoff", "last": last}
    return {**out, "status": "waiting", "last": last, "away_pct": (hi - last) / hi * 100}


# ---------- exit ----------

def exit_plan(e: dict[str, Any], cfg: dict[str, Any]) -> dict[str, Any]:
    entry_px = e["price"]
    stop = cents_down((e["or_high"] + e["or_low"]) / 2)
    risk = round(entry_px - stop, 6)
    if stop >= entry_px:
        return {"ok": False, "why": "the stop would be at or above the entry"}
    if risk < 0.0005 * entry_px:
        return {"ok": False, "why": "the stop is too tight to survive the spread (under 0.05% of the price)"}
    target = cents_half_up(entry_px + cfg["target_r"] * risk)
    return {"ok": True, "entry": entry_px, "stop": stop, "target": target, "risk": risk, "rr": round((target - entry_px) / risk, 3)}


# ---------- time machine ----------

def pool(days: list[Day]) -> np.ndarray | None:
    """Every full past session as bar ratios (open, high, low, close over the previous bar's close)."""
    rows = []
    for dy in days:
        if not dy.full():
            continue
        prev = np.r_[dy.o[0], dy.c[:-1]]
        rows.append(np.stack([dy.o / prev, dy.h / prev, dy.l / prev, dy.c / prev], axis=1))
    return np.stack(rows) if len(rows) >= 20 else None


def time_machine(P: np.ndarray, start_bar: int, last_bar: int, entry_px: float, stop: float, target: float, target_r: float,
                 replays: int, seed: int, window: int = 2) -> dict[str, Any]:
    """trader-timemachine on these bars: replay bars start_bar..last_bar (inclusive) `replays` times."""
    rng = np.random.default_rng(seed)
    D, B, _ = P.shape
    risk = entry_px - stop
    px = np.full(replays, entry_px)
    alive = np.ones(replays, bool)
    r = np.zeros(replays)
    for j in range(start_bar, last_bar + 1):
        d = rng.integers(0, D, replays)
        b = np.clip(j + rng.integers(-window, window + 1, replays), 0, B - 1)
        ro = P[d, b]
        lo, hi = px * ro[:, 2], px * ro[:, 1]
        hit_s = alive & (lo <= stop)
        hit_t = alive & ~hit_s & (hi >= target)
        r[hit_s], r[hit_t] = -1.0, target_r
        alive &= ~(hit_s | hit_t)
        px = px * ro[:, 3]
    r[alive] = (px[alive] - entry_px) / risk
    tf, sf = float(np.mean(r == target_r)), float(np.mean(r == -1.0))
    nf = 1 - tf - sf
    green = tf + float(np.mean(alive & (px > entry_px)))
    need = 1 / (1 + target_r)
    hit = tf + sf
    share = tf / hit if hit else 0.0
    gap = (share - need) * 100
    side = "the target" if gap > 0 else "the stop"
    verdict = ("Close to a coin flip" if target_r == 1 else "Close to break-even") if abs(gap) < 5 else \
        f"Leans toward {side}" if abs(gap) < 15 else f"Strongly toward {side}"
    return {"target_first": tf, "stop_first": sf, "neither": nf, "green": green, "avg_r": float(r.mean()), "verdict": verdict,
            "toward_stop": gap <= -5, "share": share, "need": need, "replays": replays, "seed": seed, "sessions": D}


# ---------- budget and risk ----------

def size(px: float, stop: float, target: float, cash: float, cfg: dict[str, Any]) -> dict[str, Any]:
    """trader-budget: whole shares, the smallest of four limits, sized at `px` (the higher of plan and fill)."""
    risk = px - stop
    if risk <= 0:
        return {"shares": 0, "why": "the price is already at or under the stop"}
    if target <= px:
        return {"shares": 0, "why": "the price is already at or over the target: the move happened"}
    rr = (target - px) / risk
    limits = {"max loss": cfg["max_loss_per_trade_usd"] / risk, "position cap": cfg["budget"] * cfg["max_position_pct"] / 100 / px,
              "max order": cfg["max_order_usd"] / px, "cash": max(0.0, cash - cfg["budget"] * cfg["cash_buffer_pct"] / 100) / px}
    by = min(limits, key=lambda k: limits[k])
    shares = int(math.floor(limits[by] + 1e-9))
    out = {"shares": shares, "decided_by": by, "limits": {k: int(math.floor(v + 1e-9)) for k, v in limits.items()}, "rr": rr,
           "max_loss": round(shares * risk, 2), "cost": round(shares * px, 2)}
    if rr < cfg["min_reward_risk"]:
        return {**out, "shares": 0, "why": f"reward:risk {rr:.2f} is under your minimum {cfg['min_reward_risk']:g}"}
    if shares == 0:
        return {**out, "why": f"not one share fits your {by} limit"}
    if shares * px < cfg["min_order_usd"]:
        return {**out, "shares": 0, "why": "the order is under the minimum order size"}
    return out


@dataclass
class Trade:
    symbol: str
    status: str                  # open, closed, blocked
    trigger_time: int
    plan: dict[str, Any]
    tm: dict[str, Any] | None = None
    sizing: dict[str, Any] = field(default_factory=dict)
    why: str = ""
    shares: int = 0
    fill: float | None = None
    fill_time: int | None = None
    exit: float | None = None
    exit_time: int | None = None
    reason: str = ""
    pnl: float | None = None
    r: float | None = None
    mark: float | None = None
    tags: list[str] = field(default_factory=list)

    def row(self) -> dict[str, Any]:
        return dict(self.__dict__)


def _walk(day: Day, i0: int, last_bar: int, stop: float, target: float, slip: float, until: int) -> tuple[float | None, int | None, str]:
    """From bar i0 (filled at its open), bar by bar: stop first (at the open if it gaps through), then the target, then
    flat at the close of `last_bar`. Stops at bars that have closed by `until`; None while still open."""
    for i in range(i0, len(day.t)):
        if int(day.t[i]) + BAR > until:
            return None, None, "open"
        o, h, lo, c = map(float, (day.o[i], day.h[i], day.l[i], day.c[i]))
        if lo <= stop:
            return min(o, stop) * (1 - slip), int(day.t[i]) + BAR, "stop"
        if h >= target:
            return max(o, target) if i > i0 else target, int(day.t[i]) + BAR, "target"
        if i >= last_bar:
            return c * (1 - slip), int(day.t[i]) + BAR, "flat by close"
    return None, None, "open"


def run_day(days: dict[str, Day], pools: dict[str, np.ndarray | None], cfg: dict[str, Any], cash: float,
            now: int | None = None, halts: list[list[int | None]] | None = None, kill_at: int | None = None,
            tags: dict[str, list[str]] | None = None, entry_rule: Any = None) -> dict[str, Any]:
    """One day of the whole chain across the universe. `now` None = the day is over; else only bars closed by `now`
    count. `halts` are [from, to] spans with no new entries; `kill_at` flattens whatever is open at that moment.
    `entry_rule(symbol, day)` can replace the breakout (used only by the random-entry benchmark)."""
    d = next(iter(days.values())).d
    k = clock(d, cfg)
    until = now if now is not None else k["close"] + BAR
    slip = cfg["slippage_pct"] / 100
    last_bar = (k["flat"] - k["open"]) // BAR - 1   # the bar that closes at the flat-by time
    board, events = {}, []
    for sym, dy in days.items():
        e = entry_rule(sym, dy) if entry_rule else entry(dy, cfg, until)
        board[sym] = {**{x: e.get(x) for x in ("status", "or_high", "or_low", "price", "time", "last", "away_pct", "why")}, "tags": (tags or {}).get(sym, [])}
        if e["status"] == "triggered":
            i = e["i"]
            value = float(np.sum(dy.c[:i + 1] * dy.v[:i + 1]))
            events.append((e["time"], -value, sym, e))
    events.sort()
    trades: list[Trade] = []
    day_risk, taken = 0.0, 0
    for when, _, sym, e in events:
        dy = days[sym]
        plan = exit_plan(e, cfg)
        t = Trade(sym, "blocked", when, plan, tags=(tags or {}).get(sym, []))
        trades.append(t)
        if not plan["ok"]:
            t.why = plan["why"]
            continue
        if cfg.get("scan_filter") and not t.tags:
            t.why = "the morning scan did not tag this stock (scan filter is on)"
            continue
        if kill_at is not None and when >= kill_at or any(a is not None and when >= a and (b is None or when < b) for a, b in halts or []):
            t.why = "Ras was halted: no new entries"
            continue
        if taken >= cfg["max_trades_per_day"]:
            t.why = f"already {taken} trades today (your limit)"
            continue
        i = e["i"]
        nxt = i + 1
        if nxt < len(dy.t):
            fill_open = float(dy.o[nxt])
        elif nxt == len(dy.t) and dy.live_open is not None and now is not None:
            fill_open = float(dy.live_open)
        else:
            t.status, t.why = "pending", "fills at the next bar's open"
            continue
        P = pools.get(sym)
        if P is None:
            t.why = "fewer than 20 full past sessions for the time machine"
            continue
        t.tm = time_machine(P, nxt, last_bar, plan["entry"], plan["stop"], plan["target"], cfg["target_r"], cfg["replays"], cfg["seed"])
        if cfg["tm_gate"] and t.tm["toward_stop"]:
            t.why = f"time machine: {t.tm['verdict'].lower()}"
            continue
        fill = fill_open * (1 + slip)
        px = max(plan["entry"], fill)
        open_cost = sum(x.shares * x.fill for x in trades if x.shares and x.fill and (x.status == "open" or (x.exit_time or 0) > when))
        realised = sum(x.pnl or 0 for x in trades if x.status == "closed" and x.exit_time and x.exit_time <= when)
        sz = size(px, plan["stop"], plan["target"], cash + realised - open_cost, cfg)
        t.sizing = sz
        if not sz["shares"]:
            t.why = sz["why"]
            continue
        if day_risk + sz["max_loss"] > cfg["max_daily_loss_usd"] + 1e-9:
            t.why = f"would take today's risk to ${day_risk + sz['max_loss']:.2f}, over your ${cfg['max_daily_loss_usd']:g} daily loss"
            continue
        day_risk += sz["max_loss"]
        taken += 1
        t.shares, t.fill, t.fill_time = sz["shares"], round(fill, 4), int(dy.t[nxt]) if nxt < len(dy.t) else when
        ex, ex_t, reason = _walk(dy, nxt, last_bar, plan["stop"], plan["target"], slip, until)
        if kill_at is not None and (ex_t is None or ex_t > kill_at) and kill_at >= t.fill_time:
            j = int(np.sum(dy.t + BAR <= kill_at)) - 1
            ex, ex_t, reason = float(dy.c[max(j, nxt)]) * (1 - slip), kill_at, "kill switch"
        if ex is None:
            t.status = "open"
            j = int(np.sum(dy.t + BAR <= until)) - 1
            t.mark = float(dy.c[j]) if j >= nxt else fill
            t.pnl = round(t.shares * (t.mark - t.fill), 2)
        else:
            t.status, t.exit, t.exit_time, t.reason = "closed", round(ex, 4), ex_t, reason
            t.pnl = round(t.shares * (ex - t.fill), 2)
        t.r = round((((t.exit if t.exit is not None else t.mark) or t.fill) - t.fill) / (px - plan["stop"]), 3)
    return {"date": d.isoformat(), "board": board, "trades": [t.row() for t in trades],
            "pnl": round(sum(t.pnl or 0 for t in trades if t.status == "closed"), 2),
            "open_pnl": round(sum(t.pnl or 0 for t in trades if t.status == "open"), 2)}


def stats(trades: list[dict[str, Any]]) -> dict[str, Any]:
    rs = np.array([t["r"] for t in trades if t["status"] == "closed" and t["r"] is not None])
    pnl = np.array([t["pnl"] for t in trades if t["status"] == "closed"])
    if not len(rs):
        return {"trades": 0}
    wins, losses = pnl[pnl > 0].sum(), -pnl[pnl < 0].sum()
    se = float(rs.std(ddof=1) / math.sqrt(len(rs))) if len(rs) > 1 else None
    return {"trades": int(len(rs)), "win_rate": float(np.mean(pnl > 0)), "avg_r": float(rs.mean()), "se_r": se,
            "profit_factor": float(wins / losses) if losses else None, "total_usd": round(float(pnl.sum()), 2),
            "targets": int(sum(t["reason"] == "target" for t in trades if t["status"] == "closed")),
            "stops": int(sum(t["reason"] == "stop" for t in trades if t["status"] == "closed"))}


def verdict(s: dict[str, Any]) -> str:
    """Plain reading of a record: too few trades, or whether the average is outside two standard errors of zero."""
    if s.get("trades", 0) < 30:
        return "too few trades to tell"
    lo, hi = s["avg_r"] - 2 * s["se_r"], s["avg_r"] + 2 * s["se_r"]
    return "positive beyond the noise" if lo > 0 else "negative beyond the noise" if hi < 0 else "can't tell from zero"


def days_back(today: date, n: int) -> list[date]:
    out, d = [], today
    while len(out) < n:
        d -= timedelta(days=1)
        if trading_day(d):
            out.append(d)
    return out[::-1]
