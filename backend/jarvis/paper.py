"""Paper trading for both divisions: simulated fills on live quotes. Never sends orders anywhere."""
import logging
import math
import time
from collections.abc import Callable
from typing import Any

from jarvis.bus import Bus
from jarvis.halt import halt
from jarvis.limits import entry_block
from jarvis.station.store import Store

log = logging.getLogger("jarvis.paper")


DAY_MS = 86_400_000
ROLL_MS = 22 * 3_600_000  # overnight funding rolls at 22:00 UTC
DEFAULT_FINANCING_PCT = 4.0  # a year, either side, until the broker's rate for the instrument is known


def division(sig: dict[str, Any]) -> str:
    return "crypto" if sig["mode"] == "crypto" else "fx"


def units_for(risk_amount: float, entry: float, stop: float, usd_per_quote: float) -> float:
    """Position size so that hitting the stop loses risk_amount (account currency)."""
    dist = abs(entry - stop)
    return risk_amount / (dist * usd_per_quote) if dist > 0 else 0.0


class Paper:
    def __init__(self, bus: Bus, store: Store, cfg: dict[str, Any], instruments: dict[str, Any], fees_pct: dict[str, float],
                 slippage_pct: float) -> None:
        self.bus, self.store, self.cfg, self.instruments = bus, store, cfg, instruments
        self.fees_pct, self.slippage_pct = fees_pct, slippage_pct
        self.quotes: dict[str, tuple[float, float]] = {}
        self.open: dict[int, dict[str, Any]] = {p["signal_id"]: p for p in store.positions(open_only=True)}
        # a fresh account (Settings -> start fresh) overrides config: {div: {balance, since}}; older trades stay in the journal
        self.account: dict[str, dict[str, Any]] = store.get_setting("paper_account", {})
        self.rules: dict[str, dict[str, Any]] = {}  # Capital.com dealing rules per instrument (min size, step, margin %)
        self.waiting: dict[int, dict[str, Any]] = {}  # FX signals seen before the dealing rules loaded (start-up)
        self.exits: dict[str, float] = {"partial": 0.0}  # main.py shares the Station's dict
        self.gate: Callable[[dict[str, Any]], str | None] | None = None  # the brain: a reason to skip, or None
        self._dirty = True

    # ---------- money ----------
    def start(self, div: str) -> float:
        return self.account.get(div, {}).get("balance", self.cfg[div]["balance"])

    def since(self, div: str) -> int:
        return self.account.get(div, {}).get("since", 0)

    def balance(self, div: str) -> float:
        return self.start(div) + self.store.realised(div, self.since(div))

    def reset(self, div: str, balance: float, now_ms: int) -> dict[str, Any]:
        """Start a new paper account: close what's open, then count only trades from now on."""
        for pos in [p for p in self.open.values() if p["division"] == div]:
            bid, ask = self.quotes.get(pos["symbol"], (pos["entry"], pos["entry"]))
            self._close(pos, bid if pos["side"] > 0 else ask, "account reset", now_ms, slip=True)
        self.account[div] = {"balance": balance, "since": now_ms}
        self.store.set_setting("paper_account", self.account)
        self._dirty = True
        log.info("paper %s account reset to %.2f", div, balance)
        return self.snapshot()

    def size(self, sym: str, units: float, fill: float, stop: float, div: str) -> tuple[float, str]:
        """Apply the broker's rules like a real account would: size step, minimum size, risk cap and free margin.
        Returns (units, "") or (0, why it can't be taken)."""
        r, usd = self.rules.get(sym), self._usd_per_quote(sym, fill)
        if not r or div != "fx":
            return units, ""
        bal = self.balance(div)
        step, mn, mpct = r.get("step") or 0, r.get("min_size") or 0, r.get("margin_pct")
        if step:
            units = math.floor(units / step + 1e-9) * step
        if units < mn:
            units = mn  # smallest real trade; allowed only if its risk stays under the cap
            risk = units * abs(fill - stop) * usd
            cap = self.cfg[div].get("max_risk_pct", 2.0)
            if risk > bal * cap / 100:
                return 0.0, (f"minimum size {mn:g} risks ${risk:.2f} = {risk / bal * 100:.1f}% of the ${bal:.2f} account "
                             f"(cap {cap:g}%)")
        if mpct:
            used = sum(p.get("margin", 0.0) for p in self.open.values() if p["division"] == div)
            free = bal - used
            if units * fill * usd * mpct / 100 > free:
                units = math.floor(free / (fill * usd * mpct / 100) / (step or 1e-9)) * (step or 1e-9)
                if units < mn or units <= 0:
                    return 0.0, f"not enough free margin (${free:.2f} free, {mpct:g}% margin)"
        return units, ""

    def _usd_per_quote(self, sym: str, px: float) -> float:
        return 1 / px if sym.startswith("USD_") else 1.0  # USD/JPY P&L is in JPY

    def _slip(self, sym: str, px: float) -> float:
        if sym in self.instruments:
            return self.cfg["fx"]["slippage_pips"] * self.instruments[sym]["pip"]
        return px * self.slippage_pct / 100

    def financing(self, p: dict[str, Any], now_ms: int) -> float:
        """Overnight funding so far, signed (negative is a cost): one charge per 22:00 UTC passed, weekends included,
        which averages out brokers' triple-charge days over a long hold. Crypto fees are modelled separately."""
        so_far = p.get("financing", 0.0)
        if p["division"] != "fx":
            return so_far
        start = p.get("fin_from", p["opened"])
        nights = (now_ms - ROLL_MS) // DAY_MS - (start - ROLL_MS) // DAY_MS
        rate = (self.rules.get(p["symbol"]) or {}).get("fin_long" if p["side"] > 0 else "fin_short")
        if rate is None:
            rate = -self.cfg["fx"].get("default_financing_pct", DEFAULT_FINANCING_PCT) / 365
        notional = p["units"] * p["entry"] * self._usd_per_quote(p["symbol"], p["entry"])
        return so_far + max(0, nights) * rate / 100 * notional

    def _fee(self, sym: str, notional: float) -> float:
        return notional * self.fees_pct.get(sym.split(":")[0], 0.0) / 100 if sym not in self.instruments else 0.0

    # ---------- lifecycle ----------
    def set_rules(self, rules: dict[str, dict[str, Any]], now_ms: int | None = None) -> None:
        """Dealing rules arrived (Capital.com, daily): open what was waiting for them."""
        self.rules.update(rules)
        for sid, s in list(self.waiting.items()):
            if s["symbol"] in self.rules:
                self.waiting.pop(sid)
                if sid not in self.open:
                    self._open(s, now_ms or int(time.time() * 1000))

    def on_signal(self, s: dict[str, Any], now_ms: int) -> None:
        if s["status"] == "active" and s["id"] not in self.open and self.cfg["auto_follow"] and s.get("levels"):
            if s["symbol"] in self.instruments and s["symbol"] not in self.rules:
                self.waiting[s["id"]] = s  # don't size a broker position without the broker's min size / step / margin
                return
            self._open(s, now_ms)
        elif s["status"] != "active" and self.waiting.pop(s["id"], None) is not None:
            return
        elif s["status"] != "active" and s["id"] in self.open:
            p = self.open[s["id"]]
            bid, ask = self.quotes.get(p["symbol"], (s["levels"]["entry"],) * 2)
            self._close(p, (bid if p["side"] > 0 else ask), f"signal {s['status'].replace('_', ' ')}", now_ms, slip=True)

    def _handled(self, sid: int) -> bool:
        """One signal gets one decision: a position it already had, or a recorded 'not taken', is final."""
        return bool(self.store.positions_for([sid])) or self.store.journal([sid]).get(sid, {}).get("trade_log", "").startswith("Not taken")

    def _open(self, s: dict[str, Any], now_ms: int) -> None:
        if halt.active or self._handled(s["id"]):
            return
        if self.gate and (why := self.gate(s)):
            log.info("paper skip %s: brain — %s", s["symbol"], why)
            self.store.save_journal(s["id"], now_ms, trade_log=f"Not taken on paper: the brain skipped it — {why}.")
            return
        sym, lv, side = s["symbol"], s["levels"], 1 if s["call"] == "BUY" else -1
        div = division(s)
        bid, ask = self.quotes.get(sym, (lv["entry"], lv["entry"]))
        fill = (ask if side > 0 else bid) + side * self._slip(sym, lv["entry"])
        risk_amt = self.balance(div) * self.cfg[div]["risk_pct"] / 100
        units = units_for(risk_amt, fill, lv["stop"], self._usd_per_quote(sym, fill))
        if div == "crypto":  # leverage cap
            units = min(units, self.balance(div) * self.cfg["crypto"]["max_leverage"] / fill)
        units, why = self.size(sym, units, fill, lv["stop"], div)
        if why:
            log.info("paper skip %s: %s", sym, why)
            self.store.save_journal(s["id"], now_ms, trade_log=f"Not taken on paper: {why}.")
            return
        usd = self._usd_per_quote(sym, fill)
        real_risk = units * abs(fill - lv["stop"]) * usd
        if div == "fx" and (why := entry_block([p for p in self.open.values() if p["division"] == "fx"], sym, s["mode"], side,
                                               real_risk, self.balance(div), self.cfg.get("limits"))):
            log.info("paper skip %s: %s", sym, why)
            self.store.save_journal(s["id"], now_ms, trade_log=f"Not taken on paper: {why}.")
            return
        mpct = (self.rules.get(sym) or {}).get("margin_pct") if div == "fx" else None
        p = {"division": div, "signal_id": s["id"], "symbol": sym, "mode": s["mode"], "side": side, "units": units,
             "entry": fill, "stop": lv["stop"], "tp": lv["tp1"], "tp2": lv.get("tp2"),
             "risk": units * abs(fill - lv["stop"]) * usd if self.rules.get(sym) else risk_amt,  # real risk after rounding
             "margin": units * fill * usd * mpct / 100 if mpct else 0.0, "opened": now_ms,
             "fees": self._fee(sym, units * fill), "closed": None, "pnl": None,
             "slip_r": round(abs(fill - lv["entry"]) / abs(fill - lv["stop"]), 4) if fill != lv["stop"] else 0.0}
        self.open[s["id"]] = self.store.save_position(p)
        self._dirty = True
        log.info("paper open %s %s %.4g @ %.6g", sym, "long" if side > 0 else "short", units, fill)

    def _close(self, p: dict[str, Any], px: float, why: str, now_ms: int, slip: bool) -> None:
        exit_px = px - p["side"] * self._slip(p["symbol"], px) if slip else px
        gross = (exit_px - p["entry"]) * p["side"] * p["units"] * self._usd_per_quote(p["symbol"], exit_px)
        fees, fin = p["fees"] + self._fee(p["symbol"], p["units"] * exit_px), self.financing(p, now_ms)
        net = gross + p.get("banked", 0.0) - fees + fin
        p.update(exit=exit_px, closed=now_ms, reason=why, fees=fees, financing=fin, pnl=round(net, 2),
                 r_net=round(net / p["risk"], 4) if p["risk"] else None)  # from unrounded money: $0.50 risk is too small for cents
        self.store.save_position(p)
        self.open.pop(p["signal_id"], None)
        self._dirty = True
        log.info("paper close %s %s pnl=%.2f", p["symbol"], why, p["pnl"])

    def on_quote(self, sym: str, bid: float, ask: float, now_ms: int) -> None:
        self.quotes[sym] = (bid, ask)
        for p in [p for p in self.open.values() if p["symbol"] == sym]:
            self.check(p, bid, ask, now_ms)
        self._dirty = self._dirty or any(p["symbol"] == sym for p in self.open.values())

    def check(self, p: dict[str, Any], bid: float, ask: float, now_ms: int) -> None:
        px = bid if p["side"] > 0 else ask
        if (px - p["stop"]) * p["side"] <= 0:
            why = "breakeven" if p.get("tp1_taken") else "stop"
            self._close(p, min(px, p["stop"]) if p["side"] > 0 else max(px, p["stop"]), why, now_ms, slip=True)
        elif p["tp"] is not None and (px - p["tp"]) * p["side"] >= 0 and not self._take_partial(p, now_ms):  # trend: no target
            self._close(p, p["tp"], "target 2" if p.get("tp1_taken") else "target", now_ms, slip=False)  # limit: no slippage

    def _take_partial(self, p: dict[str, Any], now_ms: int) -> bool:
        """At TP1: close the partial fraction (rounded to the broker's step), stop to entry, rest aims at TP2.
        False when partials are off, already taken, or the position is too small to split."""
        f = self.exits["partial"]
        if not f or p.get("tp1_taken") or not p.get("tp2"):
            return False
        r = self.rules.get(p["symbol"]) or {}
        step, mn = r.get("step") or 0, r.get("min_size") or 0
        part = math.floor(p["units"] * f / step + 1e-9) * step if step else p["units"] * f
        if part <= 0 or part < mn or p["units"] - part < mn:
            return False
        p.update(financing=self.financing(p, now_ms), fin_from=now_ms)  # on the old size up to here
        p["banked"] = p.get("banked", 0.0) + (p["tp"] - p["entry"]) * p["side"] * part * self._usd_per_quote(p["symbol"], p["tp"])
        p["units"] -= part
        p.update(stop0=p["stop"], tp1=p["tp"], stop=p["entry"], tp=p["tp2"], tp1_taken=True, margin=p.get("margin", 0.0) * p["units"] / (p["units"] + part))
        self.store.save_position(p)
        log.info("paper %s: banked %.2f at TP1, rest runs to TP2 with stop at entry", p["symbol"], p["banked"])
        return True

    def flatten(self, why: str, now_ms: int) -> int:
        """Close every open paper position at the current quote (kill switch)."""
        n = 0
        self.waiting.clear()
        for pos in list(self.open.values()):
            bid, ask = self.quotes.get(pos["symbol"], (pos["entry"], pos["entry"]))
            self._close(pos, bid if pos["side"] > 0 else ask, why, now_ms, slip=True)
            n += 1
        return n

    def marks_known(self) -> bool:
        """Every open position has a quote. Until then equity shows them at their entry price, which is no basis for limits."""
        return all(p["symbol"] in self.quotes for p in self.open.values())

    def snapshot(self) -> dict[str, Any]:
        out = {"type": "paper", "accounts": {}, "open": []}
        upnl = {"fx": 0.0, "crypto": 0.0}
        now_ms = int(time.time() * 1000)
        for p in self.open.values():
            bid, ask = self.quotes.get(p["symbol"], (p["entry"], p["entry"]))
            px = bid if p["side"] > 0 else ask
            fin = self.financing(p, now_ms)
            u = (px - p["entry"]) * p["side"] * p["units"] * self._usd_per_quote(p["symbol"], px) + p.get("banked", 0.0) - p["fees"] + fin
            upnl[p["division"]] += u
            out["open"].append({**p, "financing": round(fin, 4), "mark": px, "upnl": round(u, 2), "r": round(u / p["risk"], 2) if p["risk"] else None})
        for div in ("fx", "crypto"):
            bal = self.balance(div)
            out["accounts"][div] = {"currency": self.cfg[div]["currency"], "balance": round(bal, 2),
                                    "equity": round(bal + upnl[div], 2), "start": self.start(div), "since": self.since(div)}
        out["recent"] = [p for p in self.store.positions(limit=50) if p.get("closed") and p["opened"] >= self.since(p["division"])][:10]
        out["rules"] = self.rules
        return out

    async def run(self) -> None:
        q = self.bus.subscribe()
        last_pub = 0.0
        while True:
            e = await q.get()
            try:
                now_ms = int(time.time() * 1000)
                t = e.get("type")
                if t == "signal":
                    self.on_signal(e, now_ms)
                elif t == "price":
                    self.on_quote(e["symbol"], e["bid"], e["ask"], now_ms)
                elif t == "cprices":
                    for k, (b, a) in e["quotes"].items():
                        self.on_quote(k, b, a, now_ms)
                if self._dirty and time.time() - last_pub >= 1:
                    self.bus.publish(self.snapshot(), key="paper")
                    last_pub, self._dirty = time.time(), False
            except Exception as ex:
                self.bus.failed("paper trading", ex)
