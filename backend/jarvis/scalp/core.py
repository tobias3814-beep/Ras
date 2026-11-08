"""Scalp framework shared by Signal Station scalp mode and the crypto desk: strategy interface, regime, confluence, levels."""
from dataclasses import dataclass, field
from typing import Any

import pandas as pd

from jarvis.indicators import adx
from jarvis.station.agents import Vote


@dataclass
class Setup:
    strategy: str
    direction: int  # +1 long, -1 short
    stop: float     # structural stop; widened to the ATR minimum later
    score: int      # 0-100
    reason: str


@dataclass
class ScalpInput:
    symbol: str
    bias: pd.DataFrame   # 15M
    mid: pd.DataFrame    # 5M structure
    fast: pd.DataFrame   # 1M entry
    price: float
    now_s: float
    regime: str
    extra: dict[str, Any] = field(default_factory=dict)  # session windows, events, round step, order book, funding, OI


class Strategy:
    """Plugin interface: subclass, set name/market/regimes, implement evaluate()."""
    name = ""
    market = "fx"  # fx | crypto | both
    regimes: frozenset[str] = frozenset({"trend", "range"})

    def evaluate(self, x: ScalpInput) -> Setup | None:
        raise NotImplementedError


def regime(df5: pd.DataFrame, n: int = 20) -> str:
    """trend / range / chop from 5M ADX and Kaufman efficiency ratio."""
    c = df5.close
    er = abs(c.iloc[-1] - c.iloc[-n]) / c.diff().abs().iloc[-n + 1:].sum()
    a = float(adx(df5).iloc[-1])
    if a >= 25 and er >= 0.3:
        return "trend"
    if a < 18 and er < 0.15:
        return "chop"
    return "range"


def run(strategies: list[Strategy], x: ScalpInput) -> list[Setup]:
    out = []
    for s in strategies:
        if x.regime in s.regimes and (setup := s.evaluate(x)):
            out.append(setup)
    return out


def confluence(setups: list[Setup]) -> tuple[int, int, list[Setup]]:
    """(direction, score, agreeing setups). Each extra agreeing strategy +10, each opposing one -15."""
    if not setups:
        return 0, 0, []
    long = [s for s in setups if s.direction > 0]
    short = [s for s in setups if s.direction < 0]
    side, other = (long, short) if sum(s.score for s in long) >= sum(s.score for s in short) else (short, long)
    score = max(s.score for s in side) + 10 * (len(side) - 1) - 15 * len(other)
    return side[0].direction, int(max(0, min(100, score))), sorted(side, key=lambda s: -s.score)


def levels(direction: int, entry: float, structural_stop: float, atr5: float, cfg: dict[str, Any]) -> dict[str, float]:
    risk = max(abs(entry - structural_stop), cfg["stop_atr"] * atr5)
    return {"entry": entry, "entry_low": entry - 0.1 * risk, "entry_high": entry + 0.1 * risk,
            "stop": entry - direction * risk, "tp1": entry + direction * cfg["tp_r"][0] * risk,
            "tp2": entry + direction * cfg["tp_r"][1] * risk}


def vote(direction: int, score: int, setups: list[Setup], regime_name: str, lv: dict[str, float] | None) -> Vote:
    if not direction:
        return Vote("technical", "HOLD", 0, [f"Regime {regime_name}: no scalp setup"], watch="a strategy trigger")
    reasons = [f"{s.strategy}: {s.reason} ({s.score})" for s in setups[:4]] + [f"Regime: {regime_name}"]
    return Vote("technical", "BUY" if direction > 0 else "SELL", score, reasons, invalidation=lv["stop"] if lv else None,
                levels=lv or {})
