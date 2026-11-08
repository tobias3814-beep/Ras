"""Replay a rule's trades through a real small account: its balance, the broker's minimum sizes, margin, and every other trade
open at the time. Results in R say whether a rule has an edge; this says whether this account could have used it."""
import math
import time
from typing import Any

from jarvis.limits import entry_block


def simulate(trades: list[dict[str, Any]], spec: dict[str, Any]) -> dict[str, Any] | None:
    """spec = {balance, risk_pct, max_risk_pct, rules, limits}, plus optional money rules from the books:
    dd_cut = [[drawdown %, risk multiplier], ...] trades smaller while the account is below its peak (Jones, Seykota, McKay);
    month_stop_pct stops new trades for the rest of a calendar month once it has lost that much of the month's opening
    balance (Elder's 6% rule). trades need time, closed_at, symbol, call, mode, entry, risk_px, r."""
    if not trades:
        return None
    start = bal = float(spec["balance"])
    rules, peak, worst = spec.get("rules", {}), start, 0.0
    open_: list[dict[str, Any]] = []
    out = {"taken": 0, "too_big": 0, "no_margin": 0, "limits": 0, "paused": 0}
    month, month_open = "", start

    def close_until(t: int) -> None:
        nonlocal bal, peak, worst
        for p in sorted([p for p in open_ if p["closed_at"] <= t], key=lambda p: p["closed_at"]):
            open_.remove(p)
            bal += p["pnl"]
            peak = max(peak, bal)
            worst = max(worst, (peak - bal) / peak if peak > 0 else 1.0)

    for t in sorted(trades, key=lambda t: t["time"]):
        close_until(t["time"])
        if bal <= 0:
            break
        m = time.strftime("%Y-%m", time.gmtime(t["time"] / 1000))
        if m != month:
            month, month_open = m, bal
        if spec.get("month_stop_pct") and bal <= month_open * (1 - spec["month_stop_pct"] / 100):
            out["paused"] += 1
            continue
        risk_pct = spec["risk_pct"]
        dd = (peak - bal) / peak * 100 if peak > 0 else 0.0
        for at, mult in sorted(spec.get("dd_cut") or [], reverse=True):
            if dd >= at:
                risk_pct *= mult
                break
        sym, side = t["symbol"], 1 if t["call"] == "BUY" else -1
        usd = 1 / t["entry"] if sym.startswith("USD_") else 1.0
        r = rules.get(sym) or {}
        step, mn, mpct = r.get("step") or 0, r.get("min_size") or 0, r.get("margin_pct") or 0
        units = bal * risk_pct / 100 / (t["risk_px"] * usd)
        units = math.floor(units / step + 1e-9) * step if step else units
        if units < mn:
            units = mn
            if units * t["risk_px"] * usd > bal * spec["max_risk_pct"] / 100:
                out["too_big"] += 1
                continue
        if units <= 0:
            out["too_big"] += 1
            continue
        margin = units * t["entry"] * usd * mpct / 100
        if margin > bal - sum(p["margin"] for p in open_):
            out["no_margin"] += 1
            continue
        risk = units * t["risk_px"] * usd
        if entry_block(open_, sym, t["mode"], side, risk, bal, spec.get("limits")):
            out["limits"] += 1
            continue
        open_.append({"symbol": sym, "side": side, "risk": risk, "margin": margin, "closed_at": t["closed_at"], "pnl": t["r"] * risk})
        out["taken"] += 1
    close_until(10 ** 15)
    return {**out, "start": start, "final": round(bal, 2), "return_pct": round((bal / start - 1) * 100, 1), "max_dd_pct": round(worst * 100, 1)}
