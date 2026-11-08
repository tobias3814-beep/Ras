"""What Ras says unprompted. Pure functions: event in, (text, priority, tone) out — or None to stay quiet.
Priorities: 1 crypto/outcomes · 2 intraday + briefings · 3 FX scalps · 4 risk warnings · 5 replies to the user."""
import re
from typing import Any

NAMES = {"GBP_USD": "cable", "EUR_USD": "the euro", "USD_JPY": "dollar-yen", "XAU_USD": "gold", "XAG_USD": "silver", "WTI_USD": "oil"}
DIGITS = {"GBP_USD": 5, "EUR_USD": 5, "USD_JPY": 3, "XAU_USD": 2, "XAG_USD": 3, "WTI_USD": 2}
Said = tuple[str, int, str]


def num(x: float, d: int) -> str:
    """Spoken price: '2,410' not '2,410.00'."""
    s = f"{x:,.{d}f}"
    return s.rstrip("0").rstrip(".") if "." in s else s


def name(sym: str) -> str:
    """'XAU_USD' -> 'gold'; crypto key 'perp:BTC/USDT:USDT' -> 'BTC'."""
    if sym in NAMES:
        return NAMES[sym]
    return sym.split(":")[1].split("/")[0] if ":" in sym else sym


def new_signal(s: dict[str, Any]) -> Said | None:
    if s.get("status") != "active":
        return None
    if s["mode"] == "scalp":
        return s.get("say") or f"Scalp {s['call'].lower()} on {name(s['symbol'])}.", 3, "normal"
    if s["mode"] == "crypto":
        return s.get("say") or f"Crypto {s['call'].lower()} on {name(s['symbol'])}.", 1, "normal"
    if s["mode"] == "trend":
        return s.get("say") or f"Trend {s['call'].lower()} on {name(s['symbol'])}.", 2, "normal"
    votes = {v["agent"]: v["direction"] for v in s.get("votes", [])}
    agree = [a for a, d in votes.items() if a != "technical" and d == s["call"]]
    against = [a for a, d in votes.items() if d not in (s["call"], "HOLD")]
    parts = [f"Sir, {name(s['symbol'])} flipped to {s['call']} on the 1-hour, confidence {s['confidence']}."]
    line = " and ".join([*(a.capitalize() for a in agree), "technicals" if agree else "Technicals"]) + (" agree" if agree else " lead")
    if votes.get("news") == "HOLD":
        line += ", news is neutral"
    parts.append(line + ".")
    if against:
        parts.append(f"{' and '.join(a.capitalize() for a in against)} disagree{'s' if len(against) == 1 else ''}.")
    lv, d = s.get("levels") or {}, DIGITS.get(s["symbol"], 4)
    if lv:
        parts.append(f"Entry zone {num(lv['entry_low'], d)} to {num(lv['entry_high'], d)}.")
    return " ".join(parts), 2, "normal"


def outcome(s: dict[str, Any]) -> Said | None:
    st = s.get("status")
    if st in (None, "active") or s.get("result_r") is None:
        return None
    what = {"hit_tp": "hit its target", "hit_sl": "was stopped out", "expired": "expired", "invalidated": "was invalidated",
            "time_stop": "hit its time stop"}.get(st, st)
    r = s["result_r"]
    return f"{name(s['symbol']).capitalize()} {s['mode']} {what}, {'plus' if r >= 0 else 'minus'} {abs(r):.1f} R.", 1, "normal"


RISK = [  # (pattern in hold_why, key, text, tone)
    (r"Daily loss limit", "daily", "Sir, the daily {mode} loss limit is hit. No more {mode} signals today — the market will be there tomorrow.", "breach"),
    (r"Cooldown after", "cooldown", "Two losses in a row on the {mode} desk, sir. I'm pausing it for a cooldown. Perhaps a cup of tea.", "warning"),
    (r"Max \d+ signals per day", "maxday", "That's the {mode} quota for today, sir. Overtrading is how good weeks become bad ones.", "warning"),
    (r"Correlation guard", "corr", "A word of caution, sir: you're stacking the same exposure across several positions.", "warning"),
]


def risk_warning(call: dict[str, Any], already: set[str]) -> Said | None:
    """Once per day per (mode, rule): call `already` with keys you've said."""
    text = " ".join(call.get("hold_why", []) + call.get("warnings", []))
    mode = call["mode"]
    for pat, key, msg, tone in RISK:
        k = f"{mode}:{key}"
        if re.search(pat, text) and k not in already:
            already.add(k)
            return msg.format(mode=mode), 4, tone
    return None


def briefing(b: dict[str, Any]) -> Said | None:
    return (b["text"], 2, "normal") if b.get("kind") in ("morning", "eod") else None


def execution(x: dict[str, Any]) -> Said | None:
    who = name(x["symbol"])
    if x["status"] == "open":
        return f"Order filled: {x['side']} {who}, size {x['qty']:g}, stop {x['stop']:g}, target {x['tp']:g}. Demo account.", 4, "normal"
    if x["status"] == "closed":
        pnl = x.get("pnl")
        return f"{who.capitalize()} position closed" + (f", P and L {pnl:+.2f} USDT." if pnl is not None else "."), 4, "normal"
    if x["status"] == "failed":
        return f"Sir, the {who} order failed: {x.get('reason', 'unknown error')}", 4, "breach"
    return None


def kill(k: dict[str, Any]) -> Said:
    ex = k.get("exchange") or {}
    return (f"Kill switch engaged. {ex.get('cancelled', 0)} orders cancelled, {len(ex.get('closed', []))} positions closed, "
            f"{k.get('paper_closed', 0)} paper positions closed. New signals are paused."
            + (f" Errors: {'; '.join(ex['errors'])}." if ex.get("errors") else ""), 5, "breach")


def classify(e: dict[str, Any], said_risk: set[str], start_ms: int) -> Said | None:
    """Everything worth saying (voice) or sending (Telegram) about one bus event; ignores the replayed snapshot."""
    t = e.get("type")
    ts = e.get("closed") or e.get("closed_at") or e.get("created") or e.get("opened") or e.get("since") or 0
    if ts < start_ms:
        return None
    if t == "signal":
        return new_signal(e) if e["status"] == "active" else outcome(e)
    if t == "call":
        return risk_warning(e, said_risk)
    if t == "briefing":
        return briefing(e)
    if t == "execution":
        return execution(e)
    if t == "kill":
        return kill(e)
    if t == "limit":
        return f"Sir, {e['why']}. New trades are paused until you resume.", 4, "warning"
    if t == "test101_alert":  # TEST 101's paper desk (US stocks), trader-alerts inside Ras
        if e["kind"] == "entry":
            return f"Test 101 paper buy: {e['shares']} {e['symbol']} at {e['fill']:.2f}, stop {e['stop']:.2f}, target {e['target']:.2f}.", 2, "normal"
        return f"Test 101 closed {e['symbol']} at the {e['reason']}, {'plus' if (e.get('pnl') or 0) >= 0 else 'minus'} {abs(e.get('pnl') or 0):.2f} dollars.", 2, "normal"
    if t == "brain_lesson":  # the brain switched on a lesson that held on trades it had not seen
        return f"Sir, I have learnt something. {e['text']}", 2, "normal"
    if t == "fault":
        return f"Sir, part of Ras has failed: {e['name']}. Please check the log.", 4, "warning"
    return None
