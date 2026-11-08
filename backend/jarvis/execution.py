"""Approval-based crypto execution (USDT perpetuals, Binance Demo Trading). Nothing is sent unless:
execution was enabled with the typed phrase, the kill switch is off, and you clicked APPROVE on that signal.
Every entry is protected by a reduce-only stop; if the stop can't be placed the position is closed at once."""
import asyncio
import logging
import time
from typing import Any

import ccxt.pro as ccxtpro

from jarvis.bus import Bus
from jarvis.config import secret
from jarvis.halt import Halt
from jarvis.station.store import Store

log = logging.getLogger("jarvis.execution")
SUBMIT_TIMEOUT_MS = 300_000


class Rejected(Exception):
    """A pre-trade check failed; nothing was sent to the exchange."""


def ccxt_symbol(key: str) -> str:
    return key.split(":", 1)[1]  # "perp:BTC/USDT:USDT" -> "BTC/USDT:USDT"


def has_keys() -> bool:
    return bool(secret("binance_api_key", block=False) and secret("binance_api_secret", block=False))


class Executor:
    def __init__(self, bus: Bus, store: Store, cfg: dict[str, Any], halt: Halt, exchange: Any = None) -> None:
        self.bus, self.store, self.cfg, self.halt = bus, store, cfg, halt
        s = store.get_setting("execution", {})
        self.enabled: bool = bool(s.get("enabled")) and s.get("environment") == cfg["environment"]
        self.ex = exchange  # injected in tests; built lazily otherwise
        self.balance: float | None = None
        self.error = ""
        self._lock = asyncio.Lock()

    # ---------- exchange ----------
    def _exchange(self) -> Any:
        if self.ex is None:
            if not has_keys():
                raise Rejected("no Binance API key in the keychain — run: uv run python -m jarvis.keys")
            if self.cfg["environment"] != "demo":
                raise Rejected("only Binance Demo Trading is allowed in this build")
            ex = getattr(ccxtpro, self.cfg["venue"])({"apiKey": secret("binance_api_key"), "secret": secret("binance_api_secret"),
                                                      "enableRateLimit": True,
                                                      "options": {"warnOnFetchOpenOrdersWithoutSymbol": False}})
            ex.enable_demo_trading(True)
            self.ex = ex
        return self.ex

    async def refresh_balance(self) -> float:
        bal = await self._exchange().fetch_balance()
        self.balance = float((bal.get("USDT") or {}).get("free") or 0.0)
        return self.balance

    # ---------- settings ----------
    def state(self) -> dict[str, Any]:
        return {"type": "execution_state", "enabled": self.enabled, "environment": self.cfg["environment"], "has_keys": has_keys(),
                "balance": self.balance, "error": self.error, "confirm_phrase": self.cfg["confirm_phrase"],
                "risk_pct": self.cfg["risk_pct"], "max_notional_usdt": self.cfg["max_notional_usdt"],
                "leverage": self.cfg["leverage"], "trailing_pct": self.cfg["trailing_pct"],
                "open": self.store.executions(open_only=True), "recent": self.store.executions(limit=20)}

    def _publish_state(self) -> None:
        self.bus.publish(self.state(), key="execution_state")

    async def enable(self, phrase: str) -> dict[str, Any]:
        if phrase.strip() != self.cfg["confirm_phrase"]:
            raise Rejected(f"type exactly: {self.cfg['confirm_phrase']}")
        await self.refresh_balance()  # proves the key works before anything can be approved
        self.enabled, self.error = True, ""
        self.store.set_setting("execution", {"enabled": True, "environment": self.cfg["environment"], "at": int(time.time() * 1000)})
        self._publish_state()
        return self.state()

    def disable(self) -> dict[str, Any]:
        self.enabled = False
        self.store.set_setting("execution", {"enabled": False, "environment": self.cfg["environment"]})
        self._publish_state()
        return self.state()

    # ---------- approve / reject ----------
    def _event(self, x: dict[str, Any]) -> dict[str, Any]:
        x = self.store.save_execution(x)
        self.bus.publish({**x, "type": "execution"}, key=f"execution:{x['signal_id']}")
        self._publish_state()
        return x

    def reject(self, signal_id: int, why: str = "rejected by you") -> dict[str, Any]:
        sig = self.store.signal(signal_id)
        if sig is None:
            raise Rejected("no such signal")
        if self.store.execution_for(signal_id):
            raise Rejected("this signal already has a decision")
        return self._event({"signal_id": signal_id, "symbol": sig["symbol"], "status": "rejected", "reason": why,
                            "opened": int(time.time() * 1000), "closed": int(time.time() * 1000), "environment": self.cfg["environment"]})

    def _check(self, sig: dict[str, Any] | None, mods: dict[str, Any], now_ms: int, active: dict[str, Any]) -> dict[str, float]:
        if not self.enabled:
            raise Rejected("execution is off — enable it in Settings with the typed confirmation")
        if self.halt.active:
            raise Rejected("kill switch is active")
        if sig is None:
            raise Rejected("no such signal")
        if sig["mode"] != "crypto" or not sig["symbol"].startswith("perp:"):
            raise Rejected("only USDT-perpetual crypto signals can execute; this one stays paper")
        live = active.get(sig["symbol"])
        if sig["status"] != "active" or live is None or live["id"] != sig["id"]:
            raise Rejected("signal is no longer active")
        if not live.get("actionable") or now_ms >= (sig.get("entry_until") or 0):
            raise Rejected("entry window has closed")
        if self.store.execution_for(sig["id"]):
            raise Rejected("this signal already has a decision")
        lv, side = sig["levels"], 1 if sig["call"] == "BUY" else -1
        stop, tp = float(mods.get("stop") or lv["stop"]), float(mods.get("tp") or lv["tp1"])
        risk_pct = float(mods.get("risk_pct") or self.cfg["risk_pct"])
        if (lv["entry"] - stop) * side <= 0:
            raise Rejected("stop must be on the losing side of the entry")
        if (tp - lv["entry"]) * side <= 0:
            raise Rejected("target must be on the winning side of the entry")
        if not 0 < risk_pct <= self.cfg["max_risk_pct"]:
            raise Rejected(f"risk must be between 0 and {self.cfg['max_risk_pct']}%")
        return {"stop": stop, "tp": tp, "risk_pct": risk_pct, "side": side}

    async def approve(self, signal_id: int, mods: dict[str, Any], active: dict[str, Any]) -> dict[str, Any]:
        """active = the crypto book's live signals (for the actionable / entry-window check)."""
        async with self._lock:  # one order flow at a time
            now_ms = int(time.time() * 1000)
            sig = self.store.signal(signal_id)
            p = self._check(sig, mods, now_ms, active)
            ex, sym, side = self._exchange(), ccxt_symbol(sig["symbol"]), p["side"]
            await ex.load_markets()
            t = await ex.fetch_ticker(sym)
            px = float(t["ask"] if side > 0 else t["bid"])
            risk_px = abs(sig["levels"]["entry"] - p["stop"])
            if (sig["levels"]["entry"] - px) * side < -self.cfg["max_adverse_r"] * risk_px:
                raise Rejected(f"price already moved {abs(px - sig['levels']['entry']) / risk_px:.2f}R past the entry — skipped")
            if (px - p["stop"]) * side <= 0:
                raise Rejected("price is already beyond the stop")
            bal = await self.refresh_balance()
            qty = bal * p["risk_pct"] / 100 / abs(px - p["stop"])
            qty = min(qty, self.cfg["max_notional_usdt"] / px, bal * self.cfg["leverage"] / px)
            qty = float(ex.amount_to_precision(sym, qty))
            limits = (ex.markets[sym].get("limits") or {})
            if qty <= 0 or qty < ((limits.get("amount") or {}).get("min") or 0) or qty * px < ((limits.get("cost") or {}).get("min") or 0):
                raise Rejected("position size is below the exchange minimum")

            x = {"signal_id": signal_id, "symbol": sig["symbol"], "side": "long" if side > 0 else "short", "qty": qty,
                 "stop": p["stop"], "tp": p["tp"], "risk_pct": p["risk_pct"], "status": "submitting", "opened": now_ms,
                 "orders": {}, "environment": self.cfg["environment"], "modified": bool(mods), "ref_price": px}
            x = self._event(x)
            buy, sell = ("buy", "sell") if side > 0 else ("sell", "buy")
            for call in (lambda: ex.set_margin_mode(self.cfg["margin_mode"], sym), lambda: ex.set_leverage(self.cfg["leverage"], sym)):
                try:
                    await call()
                except Exception as e:  # "no need to change" style errors are fine
                    log.info("margin/leverage: %r", e)
            try:
                entry = await ex.create_order(sym, "market", buy, qty)
            except Exception as e:
                x.update(status="failed", reason=f"entry order failed: {e}", closed=int(time.time() * 1000))
                return self._event(x)
            x["orders"]["entry"] = entry["id"]
            x["entry_px"] = float(entry.get("average") or entry.get("price") or px)
            try:
                sl = await ex.create_order(sym, "market", sell, qty, None, {"stopLossPrice": p["stop"], "reduceOnly": True})
                x["orders"]["stop"] = sl["id"]
                tp = await ex.create_order(sym, "market", sell, qty, None, {"takeProfitPrice": p["tp"], "reduceOnly": True})
                x["orders"]["tp"] = tp["id"]
                if self.cfg.get("trailing_pct"):
                    tr = await ex.create_order(sym, "market", sell, qty, None, {"trailingPercent": self.cfg["trailing_pct"], "reduceOnly": True})
                    x["orders"]["trailing"] = tr["id"]
            except Exception as e:
                # never leave a position without its stop
                log.error("protective orders failed for %s: %r — closing", sym, e)
                await self._cancel_ours(x)
                try:
                    await ex.create_order(sym, "market", sell, qty, None, {"reduceOnly": True})
                    x.update(status="failed", reason=f"protective orders failed ({e}); position closed at market")
                except Exception as e2:
                    x.update(status="failed", reason=f"protective orders failed ({e}) AND emergency close failed ({e2}) — CHECK THE EXCHANGE")
                x["closed"] = int(time.time() * 1000)
                return self._event(x)
            x["status"] = "open"
            log.info("executed %s %s %s", sym, x["side"], qty)
            return self._event(x)

    # ---------- lifecycle ----------
    async def _cancel_ours(self, x: dict[str, Any]) -> None:
        sym = ccxt_symbol(x["symbol"])
        for name, oid in x.get("orders", {}).items():
            if name == "entry":
                continue
            try:
                await self.ex.cancel_order(oid, sym)
            except Exception as e:  # already filled or cancelled
                log.info("cancel %s %s: %r", name, oid, e)

    async def _pnl(self, x: dict[str, Any]) -> float | None:
        try:
            trades = await self.ex.fetch_my_trades(ccxt_symbol(x["symbol"]), since=x["opened"] - 1000)
        except Exception:
            return None
        cash = sum((t["cost"] if t["side"] == "sell" else -t["cost"]) for t in trades)
        fees = sum(float((t.get("fee") or {}).get("cost") or 0) for t in trades)
        return round(cash - fees, 4)

    async def check_open(self) -> None:
        open_x = self.store.executions(open_only=True)
        if not open_x:
            return
        try:
            self._exchange()  # after a restart nothing else has rebuilt the client, and open trades still need watching
        except Rejected as e:
            self.error = str(e)
            return
        positions = await self.ex.fetch_positions([ccxt_symbol(x["symbol"]) for x in open_x])
        size = {p["symbol"]: abs(float(p.get("contracts") or 0)) for p in positions}
        for x in open_x:
            if x["status"] == "submitting" and time.time() * 1000 - (x.get("opened") or 0) > SUBMIT_TIMEOUT_MS:  # never heard back
                held = size.get(ccxt_symbol(x["symbol"]), 0) > 0
                x.update(status="open" if held else "failed", reason="no confirmation from the exchange" + (": position found, check its stop" if held else ""))
                self.store.save_execution(x)
                self._event(x)
            elif x["status"] == "open" and size.get(ccxt_symbol(x["symbol"]), 0) == 0:
                await self._cancel_ours(x)  # the other bracket leg must not linger
                x.update(status="closed", closed=int(time.time() * 1000), pnl=await self._pnl(x))
                self._event(x)

    async def run(self) -> None:
        self._publish_state()
        while True:
            await asyncio.sleep(self.cfg["monitor_seconds"])
            try:
                await self.check_open()
            except Exception as e:
                self.error = f"monitor: {e}"
                log.warning("execution monitor: %r", e)

    async def flatten_all(self) -> dict[str, Any]:
        """Kill switch: cancel EVERY open futures order and close EVERY futures position on the account."""
        out: dict[str, Any] = {"cancelled": 0, "closed": [], "errors": []}
        if self.ex is None and not has_keys():
            return out
        ex = self._exchange()
        try:
            for o in await ex.fetch_open_orders():
                try:
                    await ex.cancel_order(o["id"], o["symbol"])
                    out["cancelled"] += 1
                except Exception as e:
                    out["errors"].append(f"cancel {o['symbol']}: {e}")
            for p in await ex.fetch_positions():
                n = abs(float(p.get("contracts") or 0))
                if n:
                    side = "sell" if p.get("side") == "long" else "buy"
                    try:
                        await ex.create_order(p["symbol"], "market", side, n, None, {"reduceOnly": True})
                        out["closed"].append(p["symbol"])
                    except Exception as e:
                        out["errors"].append(f"close {p['symbol']}: {e}")
        except Exception as e:
            out["errors"].append(str(e))
        now = int(time.time() * 1000)
        for x in self.store.executions(open_only=True):
            x.update(status="closed", closed=now, reason="kill switch")
            self._event(x)
        return out

    async def close(self) -> None:
        if self.ex is not None and hasattr(self.ex, "close"):
            await self.ex.close()
