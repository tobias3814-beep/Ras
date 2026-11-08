"""Account-wide safety limits. These catch bugs, bad data and pile-ups: there is no good evidence they improve returns, so
they are set wide enough not to fire on ordinary losses. Entry limits skip one trade; the two account limits pause all
new trades (open positions keep their stops) until you press resume."""
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from jarvis.station.store import Store

DEFAULTS = {"max_open_risk_pct": 12.0, "max_cluster_risk_pct": 6.0, "daily_loss_pct": 5.0, "drawdown_pct": 30.0}
CLUSTERS = {"XAU_USD": "metals", "XAG_USD": "metals", "WTI_USD": "oil", "EUR_USD": "European FX", "GBP_USD": "European FX",
            "USD_JPY": "yen"}  # markets in a cluster tend to move together, so their risk adds up


def entry_block(open_positions: list[dict[str, Any]], sym: str, mode: str, side: int, risk: float, balance: float,
                cfg: dict[str, Any] | None = None) -> str:
    """Why this new position must not open, or "". Only positions in the same account currency are passed in."""
    c = {**DEFAULTS, **(cfg or {})}
    if mode != "trend" and any(p["symbol"] == sym and p["side"] != side for p in open_positions):
        return f"opposite position already open on {sym}: both sides would just pay costs"  # trend holds for months: it isn't blocked
    total = sum(p.get("risk", 0.0) for p in open_positions) + risk
    if total > balance * c["max_open_risk_pct"] / 100:
        return f"total open risk would be ${total:.2f}, over the {c['max_open_risk_pct']:g}% account limit"
    if (cluster := CLUSTERS.get(sym)) is not None:
        grouped = sum(p.get("risk", 0.0) for p in open_positions if CLUSTERS.get(p["symbol"]) == cluster) + risk
        if grouped > balance * c["max_cluster_risk_pct"] / 100:
            return f"{cluster} risk would be ${grouped:.2f}, over the {c['max_cluster_risk_pct']:g}% limit for markets that move together"
    return ""


class AccountWatch:
    """Daily loss and drawdown, on equity including open positions. Baselines survive a restart."""

    def __init__(self, store: Store, cfg: dict[str, Any] | None, tz: str) -> None:
        self.store, self.cfg, self.tz = store, {**DEFAULTS, **(cfg or {})}, ZoneInfo(tz)
        self.base: dict[str, Any] = store.get_setting("account_watch", {})

    def _save(self) -> None:
        self.store.set_setting("account_watch", self.base)

    def check(self, equity: float, now_ms: int) -> str:
        """The limit just broken, or ""."""
        day = datetime.fromtimestamp(now_ms / 1000, self.tz).date().isoformat()
        b, before = self.base, dict(self.base)
        if b.get("day") != day:
            b.update(day=day, day_start=equity)
        b["peak"] = max(b.get("peak", equity), equity)
        if b != before:
            self._save()
        if (lost := b["day_start"] - equity) > b["day_start"] * self.cfg["daily_loss_pct"] / 100:
            return f"daily loss limit: down ${lost:.2f} today, over {self.cfg['daily_loss_pct']:g}%"
        if (drop := b["peak"] - equity) > b["peak"] * self.cfg["drawdown_pct"] / 100:
            return f"drawdown limit: ${drop:.2f} below the peak, over {self.cfg['drawdown_pct']:g}%"
        return ""

    def reset(self, equity: float, now_ms: int) -> None:
        """Pressing resume accepts the loss so far; the limits count from here."""
        self.base = {}
        self.check(equity, now_ms)
