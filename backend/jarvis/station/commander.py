"""Commander: technicals time the trade, fundamentals confirm or veto it, the Risk Officer has the last word."""
from dataclasses import asdict
from typing import Any

from jarvis.station.agents import RiskReport, Vote

SIGN = {"BUY": 1, "SELL": -1, "HOLD": 0}


def decide(symbol: str, votes: list[Vote], risk: RiskReport, spread: float | None, cfg: dict[str, Any], now_ms: int,
           mode: str = "intraday", hold_time: str = "4–12h", expiry_ms: int | None = None) -> dict[str, Any]:
    w = cfg["weights"]
    tech = next(v for v in votes if v.agent == "technical")
    others = [v for v in votes if v.agent != "technical"]
    side = SIGN[tech.direction]

    # fundamentals score relative to the technical direction, -100..100; neutral agents dilute it
    total_w = sum(w.get(v.agent, 1.0) for v in others) or 1.0
    support = sum(w.get(v.agent, 1.0) * SIGN[v.direction] * side * v.confidence for v in others) / total_w
    conf = round(max(0.0, min(100.0, tech.confidence + cfg["support_factor"] * support))) if side else 0

    hold_why: list[str] = []
    if not side:
        hold_why.append("No technical setup" + (" — timeframes don't line up" if mode == "intraday" else " — no strategy triggered"))
    else:
        if support <= -cfg["conflict_score"]:
            against = ", ".join(f"{v.agent} {v.direction} {v.confidence}" for v in others if SIGN[v.direction] == -side)
            hold_why.append(f"Agents conflict: technicals say {tech.direction} but {against}")
        if conf < cfg["min_confidence"]:
            hold_why.append(f"Confidence {conf} is below the {cfg['min_confidence']} threshold")
    hold_why += [f"Risk Officer veto — {r}" for r in risk.reasons]

    agree = [v.agent for v in others if side and SIGN[v.direction] == side]
    disagree = [v.agent for v in others if side and SIGN[v.direction] == -side]
    summary = (f"{' and '.join(['technicals', *agree])} agree" if agree else "Technicals only — fundamentals neutral") + \
        (f"; {', '.join(disagree)} disagree" if disagree else "")
    reasons = ([summary] if side and others else []) + tech.reasons[:3] + [v.reasons[0] for v in others if v.direction != "HOLD"]

    call = "HOLD" if hold_why else tech.direction
    decision: dict[str, Any] = {
        "type": "call", "symbol": symbol, "mode": mode, "priority": 1 if mode == "intraday" else 2, "call": call, "lean": tech.direction, "confidence": conf,
        "votes": [{k: v for k, v in asdict(x).items() if k != "levels"} for x in votes],
        "risk": asdict(risk), "reasons": reasons[:5], "hold_why": hold_why,
        "watch": [v.watch for v in votes if v.watch], "warnings": risk.warnings, "created": now_ms,
    }
    if call != "HOLD":
        lv, sp = tech.levels, spread or 0.0
        decision.update(levels=lv, invalidation=tech.invalidation, hold_time=hold_time, spread=sp,
                        rr=round((abs(lv["tp1"] - lv["entry"]) - sp) / (abs(lv["entry"] - lv["stop"]) + sp), 2),
                        expires=now_ms + (expiry_ms or int(cfg["intraday"]["expiry_hours"] * 3_600_000)))
    return decision
