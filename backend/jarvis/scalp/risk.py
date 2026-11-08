"""Scalp risk rules (separate from intraday): costs, spread, session/rollover/blackout, DXY/BTC filters,
daily limits and the cross-division correlation guard."""
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

import pandas as pd

from jarvis.scalp.sessions import near_rollover
from jarvis.station.agents import blackout_event

# signed exposure per +1 unit long; crypto alts all load the same "crypto beta" factor
EXPOSURE = {"EUR_USD": {"USD": -1}, "GBP_USD": {"USD": -1}, "USD_JPY": {"USD": 1}, "XAU_USD": {"USD": -1},
            "XAG_USD": {"USD": -1}, "WTI_USD": {}}
USD_SHORT_WHEN_LONG = {"EUR_USD", "GBP_USD", "XAU_USD", "XAG_USD"}


def net_rr(lv: dict[str, float], cost: float) -> float:
    return (abs(lv["tp1"] - lv["entry"]) - cost) / (abs(lv["entry"] - lv["stop"]) + cost)


def usd_move(fast: dict[str, pd.DataFrame], bars: int = 15) -> float | None:
    """% move of a quick USD basket over the last N 1M bars (+ = USD stronger)."""
    legs = [(-1, "EUR_USD"), (-1, "GBP_USD"), (1, "USD_JPY")]
    moves = [s * (f.close.iloc[-1] / f.close.iloc[-bars - 1] - 1) * 100 for s, k in legs
             if (f := fast.get(k)) is not None and len(f) > bars]
    return sum(moves) / len(moves) if moves else None


def fx_checks(sym: str, ins: dict[str, Any], direction: int, lv: dict[str, float] | None, bid: float | None,
              ask: float | None, fast: pd.DataFrame, events: list[dict[str, Any]], session: str | None,
              usd: float | None, now_ms: int, cfg: dict[str, Any], min_rr: float) -> list[str]:
    out = []
    if session is None:
        out.append("Session inactive — scalps fire at London open, NY open and the overlap" + (" (and Tokyo)" if sym == "USD_JPY" else ""))
    if near_rollover(now_ms, cfg["rollover_minutes"]):
        out.append(f"Daily rollover (17:00 New York) — clears {cfg['rollover_minutes']} min after")
    if e := blackout_event(sym, ins.get("ccy", []), events, now_ms, cfg["blackout_before"], cfg["blackout_after"]):
        out.append(f"Scalp news blackout: {e['country']} {e['title']} — clears {cfg['blackout_after']} min after release")
    if bid is None or ask is None:
        return out + ["No live quote"]
    spread, pip = ask - bid, ins["pip"]
    if spread / pip > ins["max_spread"]:
        out.append(f"Spread {spread / pip:.1f} pips > max {ins['max_spread']}")
    if "ask_close" in fast and len(fast) >= 30:
        med = float((fast.ask_close - fast.bid_close).iloc[-60:].median())
        if med > 0 and spread > cfg["spread_widen_x"] * med:
            out.append(f"Spread widened to {spread / med:.1f}x its 1h median (news/rollover) — wait for it to normalise")
    if lv:
        if spread > cfg["max_spread_tp_pct"] / 100 * abs(lv["tp1"] - lv["entry"]):
            out.append(f"Spread is over {cfg['max_spread_tp_pct']}% of the first target")
        if (rr := net_rr(lv, spread)) < min_rr:
            out.append(f"Net R:R {rr:.2f} < {min_rr} after spread")
    if usd is not None and direction and sym in EXPOSURE and EXPOSURE[sym]:
        usd_long = direction * EXPOSURE[sym]["USD"]  # +1 if this trade is long USD
        if usd * -usd_long >= cfg["dxy_spike_pct"]:
            out.append(f"DXY filter: USD basket {usd:+.2f}% in 15 min — not fading a sharp dollar move")
    return out


def crypto_checks(lv: dict[str, float] | None, bid: float | None, ask: float | None, fee_pct: float, scfg: dict[str, Any],
                  btc: dict[str, float] | None, direction: int, min_rr: float) -> list[str]:
    if bid is None or ask is None:
        return ["No live quote"]
    out, mid = [], (bid + ask) / 2
    spread_pct = (ask - bid) / mid * 100
    if spread_pct > scfg["max_spread_pct"]:
        out.append(f"Spread {spread_pct:.3f}% > {scfg['max_spread_pct']}%")
    if lv:
        cost = (ask - bid) + mid * (2 * fee_pct + 2 * scfg["slippage_pct"]) / 100
        if (rr := net_rr(lv, cost)) < min_rr:
            out.append(f"Doesn't clear costs: net R:R {rr:.2f} < {min_rr} after fees, spread and slippage")
    if btc and direction and btc["corr"] >= scfg["btc_corr_min"] and btc["trend"] == -direction:
        out.append(f"BTC filter: {btc['corr']:.2f} correlated to BTC, and BTC's 5M trend is against this trade")
    return out


def limit_checks(signals_today: list[dict[str, Any]], active: int, session: str | None, lim: dict[str, Any], now_ms: int) -> list[str]:
    out = []
    closed = sorted((s for s in signals_today if s["status"] != "active" and s["result_r"] is not None), key=lambda s: s["closed_at"])
    if (day_r := sum(s["result_r"] for s in closed)) <= -lim["daily_loss_r"]:
        out.append(f"Daily loss limit hit ({day_r:+.1f}R) — resets tomorrow")
    if len(signals_today) >= lim["max_per_day"]:
        out.append(f"Max {lim['max_per_day']} signals per day reached")
    if session and sum(s.get("session") == session for s in signals_today) >= lim["max_per_session"]:
        out.append(f"Max {lim['max_per_session']} scalps this session reached")
    if active >= lim["max_concurrent"]:
        out.append(f"Max {lim['max_concurrent']} concurrent open")
    n = lim["cooldown_losses"]
    if len(closed) >= n and all(s["result_r"] < 0 for s in closed[-n:]) and now_ms - closed[-1]["closed_at"] < lim["cooldown_minutes"] * 60_000:
        out.append(f"Cooldown after {n} straight losses — {lim['cooldown_minutes']} min")
    return out


def exposure(sym: str) -> dict[str, int]:
    return EXPOSURE.get(sym, {"CRYPTO": 1})


def correlation_guard(sym: str, direction: int, open_signals: list[dict[str, Any]], max_same: int) -> tuple[list[str], list[str]]:
    """(vetoes, warnings) for stacking the same exposure across intraday, scalp and crypto signals."""
    totals: dict[str, int] = {}
    names: dict[str, list[str]] = {}
    for s in open_signals:
        d = 1 if s["call"] == "BUY" else -1
        for f, w in exposure(s["symbol"]).items():
            totals[f] = totals.get(f, 0) + d * w
            names.setdefault(f, []).append(f"{s['call'].lower()} {s['symbol']}")
    veto, warn = [], []
    for f, w in exposure(sym).items():
        new = totals.get(f, 0) + direction * w
        if abs(new) > abs(totals.get(f, 0)) and abs(new) >= 2:
            msg = f"{'long' if new > 0 else 'short'} {f} x{abs(new)}: " + ", ".join(names.get(f, [])[:4]) + f" + this {sym}"
            (veto if abs(new) > max_same else warn).append(f"Correlation guard — {msg}")
    return veto, warn


def day_start_ms(now_ms: int, tz: str) -> int:
    d = datetime.fromtimestamp(now_ms / 1000, ZoneInfo(tz)).replace(hour=0, minute=0, second=0, microsecond=0)
    return int(d.timestamp() * 1000)


