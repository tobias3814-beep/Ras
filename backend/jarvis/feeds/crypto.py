"""Live crypto candles via CCXT websockets (public data, no API key)."""
import asyncio
import logging
from typing import Any

import ccxt.pro as ccxtpro

from jarvis.bus import Bus

log = logging.getLogger("jarvis.crypto")


def to_candle(row: list[float]) -> dict[str, float]:
    ts, o, h, l, c, v = row[:6]
    return {"time": int(ts // 1000), "open": o, "high": h, "low": l, "close": c, "volume": v}


async def run(bus: Bus, cfg: dict[str, Any]) -> None:
    sym, tf = cfg["symbol"], cfg["timeframe"]
    key = f"candles:{sym}:{tf}"
    backoff = 1
    while True:
        ex = getattr(ccxtpro, cfg["exchange"])({"enableRateLimit": True})
        try:
            history = [to_candle(r) for r in await ex.fetch_ohlcv(sym, tf, limit=cfg.get("history", 300))]
            candles = {c["time"]: c for c in history}
            bus.publish({"type": "candles", "symbol": sym, "tf": tf, "candles": history}, key=key)
            bus.publish({"type": "status", "feed": "crypto", "state": "live", "detail": cfg["exchange"]},
                        key="status:crypto")
            backoff = 1
            while True:
                for row in await ex.watch_ohlcv(sym, tf):
                    c = to_candle(row)
                    candles[c["time"]] = c
                    bus.publish({"type": "candle", "symbol": sym, "tf": tf, "candle": c})
                # keep snapshot current so a reconnecting UI gets the full series
                bus.snapshot[key] = {"type": "candles", "symbol": sym, "tf": tf,
                                     "candles": sorted(candles.values(), key=lambda x: x["time"])[-1000:]}
        except Exception as e:
            bus.publish({"type": "status", "feed": "crypto", "state": "reconnecting", "detail": type(e).__name__},
                        key="status:crypto")
            log.warning("crypto stream dropped: %r", e)
        finally:
            await ex.close()
        await asyncio.sleep(backoff)
        backoff = min(backoff * 2, 60)
